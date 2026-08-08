from __future__ import annotations

from base64 import urlsafe_b64decode
from configparser import ConfigParser
import csv
from dataclasses import dataclass
from email.parser import Parser
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tomllib
from typing import Iterable
from zipfile import BadZipFile, ZipFile

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import parse_tag, sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from wavebench import __version__
from wavebench.errors import ConfigError


ENTRY_POINT_GROUP = "wavebench.instruments"
MAX_METADATA_BYTES = 1024 * 1024
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_WHEEL_MEMBERS = 4096
MAX_WHEEL_UNCOMPRESSED_BYTES = 256 * 1024 * 1024

_SUBPROCESS_ENV_KEYS = (
    "PATH",
    "HOME",
    "SystemRoot",
    "WINDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "APPDATA",
    "PATHEXT",
    "ComSpec",
    "VIRTUAL_ENV",
)


def build_subprocess_environment() -> dict[str, str]:
    """Build a minimal, platform-safe environment for package subprocesses."""

    environment: dict[str, str] = {}
    wanted = {key.casefold(): key for key in _SUBPROCESS_ENV_KEYS}
    for key, value in os.environ.items():
        canonical = wanted.get(key.casefold())
        if canonical is not None:
            environment[canonical] = value
    environment.setdefault("PATH", os.environ.get("PATH", os.defpath))
    if "HOME" not in environment:
        try:
            environment["HOME"] = str(Path.home())
        except RuntimeError:
            pass
    environment["PIP_CONFIG_FILE"] = os.devnull
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONUTF8"] = "1"
    return environment


@dataclass(frozen=True)
class WheelEntryPoint:
    driver_id: str
    value: str


@dataclass(frozen=True)
class PluginPackage:
    input_path: Path
    wheel_path: Path
    source_kind: str
    distribution: str
    normalized_distribution: str
    version: str
    requires_python: str | None
    dependencies: tuple[str, ...]
    entry_points: tuple[WheelEntryPoint, ...]
    sha256: str
    size_bytes: int
    file_count: int
    metadata_sha256: str
    record_sha256: str
    member_paths: tuple[str, ...]
    build_backend: str | None = None

    @property
    def driver_ids(self) -> tuple[str, ...]:
        return tuple(item.driver_id for item in self.entry_points)


@dataclass(frozen=True)
class SourceProject:
    path: Path
    distribution: str
    version: str
    build_backend: str
    entry_points: tuple[WheelEntryPoint, ...]


def inspect_plugin_package(
    path: str | Path,
    *,
    build_directory: str | Path | None = None,
    python_executable: str | Path = sys.executable,
    build_timeout_s: float = 120.0,
) -> PluginPackage:
    candidate = Path(path).expanduser()
    if candidate.is_dir():
        if build_directory is None:
            raise ConfigError(
                "source package inspection requires a temporary build directory / "
                "源码包检查需要临时构建目录"
            )
        source = inspect_source_project(candidate)
        wheel = build_source_wheel(
            source,
            output_directory=build_directory,
            python_executable=python_executable,
            timeout_s=build_timeout_s,
        )
        inspected = inspect_plugin_wheel(wheel)
        if canonicalize_name(source.distribution) != inspected.normalized_distribution:
            raise ConfigError(
                "built wheel distribution does not match pyproject.toml / "
                "构建出的 wheel distribution 与 pyproject.toml 不一致"
            )
        if Version(source.version) != Version(inspected.version):
            raise ConfigError(
                "built wheel version does not match pyproject.toml / "
                "构建出的 wheel 版本与 pyproject.toml 不一致"
            )
        if source.entry_points != inspected.entry_points:
            raise ConfigError(
                "built wheel entry points do not match pyproject.toml / "
                "构建出的 wheel entry point 与 pyproject.toml 不一致"
            )
        return PluginPackage(
            **{
                **inspected.__dict__,
                "input_path": source.path,
                "source_kind": "source",
                "build_backend": source.build_backend,
            }
        )
    if not candidate.is_file():
        raise ConfigError(f"plugin package not found: {candidate.name} / 未找到插件包")
    if candidate.suffix.lower() != ".whl":
        raise ConfigError("plugin package must be a source directory or .whl file / 插件包必须是源码目录或 .whl 文件")
    return inspect_plugin_wheel(candidate)


def inspect_source_project(path: str | Path) -> SourceProject:
    root = Path(path).expanduser().resolve()
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        raise ConfigError("source package is missing pyproject.toml / 源码包缺少 pyproject.toml")
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid plugin pyproject.toml / 插件 pyproject.toml 无效: {exc}") from exc
    build_system = data.get("build-system")
    project = data.get("project")
    if not isinstance(build_system, dict) or not isinstance(project, dict):
        raise ConfigError("pyproject.toml must define build-system and project tables / 必须定义 build-system 和 project")
    backend = build_system.get("build-backend")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(backend, str) or not backend.strip():
        raise ConfigError("plugin build-system.build-backend must be a non-empty string / build backend 必须为非空字符串")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("plugin project.name must be a non-empty string / project.name 必须为非空字符串")
    if not isinstance(version, str) or not version.strip():
        raise ConfigError("plugin project.version must be a static string / project.version 必须是静态字符串")
    try:
        Version(version)
    except InvalidVersion as exc:
        raise ConfigError(f"invalid plugin version / 插件版本无效: {version}") from exc
    entry_points = _source_entry_points(project)
    return SourceProject(
        path=root,
        distribution=name,
        version=version,
        build_backend=backend,
        entry_points=entry_points,
    )


def build_source_wheel(
    source: SourceProject,
    *,
    output_directory: str | Path,
    python_executable: str | Path = sys.executable,
    timeout_s: float = 120.0,
) -> Path:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    before = {item.resolve() for item in output.glob("*.whl")}
    command = [
        str(python_executable),
        "-m",
        "pip",
        "--isolated",
        "wheel",
        "--no-build-isolation",
        "--no-deps",
        "--no-index",
        "--disable-pip-version-check",
        "--no-input",
        "--no-cache-dir",
        "--wheel-dir",
        str(output),
        str(source.path),
    ]
    try:
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env=build_subprocess_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ConfigError("plugin wheel build timed out / 插件 wheel 构建超时") from exc
    if completed.returncode != 0:
        detail = _sanitize_subprocess_detail(completed.stderr or completed.stdout, source.path)
        raise ConfigError(f"plugin wheel build failed / 插件 wheel 构建失败: {detail}")
    wheels = sorted(
        item.resolve()
        for item in output.glob("*.whl")
        if item.resolve() not in before
    )
    if len(wheels) != 1:
        raise ConfigError(
            f"plugin build must produce exactly one wheel, got {len(wheels)} / "
            "插件构建必须恰好生成一个 wheel"
        )
    return wheels[0]


def inspect_plugin_wheel(path: str | Path) -> PluginPackage:
    supplied_path = Path(path).expanduser()
    if supplied_path.is_symlink():
        raise ConfigError("plugin wheel must not be a symlink / 插件 wheel 不能是软链接")
    wheel_path = supplied_path.resolve()
    if not wheel_path.is_file() or wheel_path.suffix.lower() != ".whl":
        raise ConfigError("plugin wheel is not a regular .whl file / 插件 wheel 不是普通 .whl 文件")
    if wheel_path.stat().st_size > MAX_WHEEL_BYTES:
        raise ConfigError("plugin wheel is too large / 插件 wheel 过大")
    try:
        filename_name, filename_version, _build, filename_tags = parse_wheel_filename(wheel_path.name)
    except (InvalidVersion, ValueError) as exc:
        raise ConfigError(f"invalid wheel filename / wheel 文件名无效: {wheel_path.name}") from exc
    if not filename_tags.intersection(set(sys_tags())):
        raise ConfigError("wheel is not compatible with this Python interpreter / wheel 与当前 Python 解释器不兼容")
    try:
        with ZipFile(wheel_path) as archive:
            infos = archive.infolist()
            _validate_wheel_members(infos)
            metadata_names = [item.filename for item in infos if item.filename.endswith(".dist-info/METADATA")]
            wheel_names = [item.filename for item in infos if item.filename.endswith(".dist-info/WHEEL")]
            record_names = [item.filename for item in infos if item.filename.endswith(".dist-info/RECORD")]
            entry_names = [item.filename for item in infos if item.filename.endswith(".dist-info/entry_points.txt")]
            if len(metadata_names) != 1:
                raise ConfigError("wheel must contain exactly one METADATA file / wheel 必须恰好包含一个 METADATA")
            if len(entry_names) != 1:
                raise ConfigError("wheel must contain exactly one entry_points.txt / wheel 必须恰好包含一个 entry_points.txt")
            if len(wheel_names) != 1:
                raise ConfigError("wheel must contain exactly one WHEEL file / wheel 必须恰好包含一个 WHEEL")
            if len(record_names) != 1:
                raise ConfigError("wheel must contain exactly one RECORD file / wheel 必须恰好包含一个 RECORD")
            dist_info = metadata_names[0].rsplit("/", 1)[0]
            expected_names = {
                f"{dist_info}/WHEEL": wheel_names[0],
                f"{dist_info}/RECORD": record_names[0],
                f"{dist_info}/entry_points.txt": entry_names[0],
            }
            if any(expected != actual for expected, actual in expected_names.items()):
                raise ConfigError("wheel metadata files must share one dist-info directory / wheel 元数据必须位于同一 dist-info 目录")
            metadata_text = _read_small_text(archive, metadata_names[0])
            wheel_text = _read_small_text(archive, wheel_names[0])
            record_text = _read_small_text(archive, record_names[0])
            entry_text = _read_small_text(archive, entry_names[0])
            wheel_tags = _validate_wheel_metadata(wheel_text)
            if wheel_tags != filename_tags:
                raise ConfigError(
                    "wheel filename tags do not match WHEEL metadata / "
                    "wheel 文件名标签与 WHEEL 元数据不一致"
                )
            member_paths = _validate_record(archive, infos, record_names[0], record_text)
    except BadZipFile as exc:
        raise ConfigError("plugin wheel is not a valid ZIP archive / 插件 wheel 不是有效 ZIP") from exc
    message = Parser().parsestr(metadata_text)
    name = message.get("Name")
    version = message.get("Version")
    if not name or not version:
        raise ConfigError("wheel METADATA is missing Name or Version / wheel METADATA 缺少 Name 或 Version")
    try:
        parsed_version = Version(version)
    except InvalidVersion as exc:
        raise ConfigError(f"invalid wheel metadata version / wheel metadata 版本无效: {version}") from exc
    if canonicalize_name(name) != canonicalize_name(filename_name):
        raise ConfigError("wheel filename and METADATA distribution do not match / wheel 文件名与 METADATA distribution 不一致")
    if parsed_version != filename_version:
        raise ConfigError("wheel filename and METADATA version do not match / wheel 文件名与 METADATA 版本不一致")
    requires_python = message.get("Requires-Python")
    _validate_python_requirement(requires_python)
    dependencies = tuple(message.get_all("Requires-Dist", ()))
    _validate_wavebench_requirement(dependencies)
    entry_points = _parse_entry_points(entry_text)
    return PluginPackage(
        input_path=wheel_path,
        wheel_path=wheel_path,
        source_kind="wheel",
        distribution=name,
        normalized_distribution=canonicalize_name(name),
        version=str(parsed_version),
        requires_python=requires_python,
        dependencies=dependencies,
        entry_points=entry_points,
        sha256=_sha256_file(wheel_path),
        size_bytes=wheel_path.stat().st_size,
        file_count=len(infos),
        metadata_sha256=sha256(metadata_text.encode()).hexdigest(),
        record_sha256=sha256(record_text.encode()).hexdigest(),
        member_paths=member_paths,
    )


def _source_entry_points(project: dict[str, object]) -> tuple[WheelEntryPoint, ...]:
    groups = project.get("entry-points")
    group = groups.get(ENTRY_POINT_GROUP) if isinstance(groups, dict) else None
    if not isinstance(group, dict) or not group:
        raise ConfigError(f"plugin must declare [{ENTRY_POINT_GROUP}] entry points / 插件必须声明可执行仪器 entry point")
    return _validated_entry_points(group.items())


def _parse_entry_points(text: str) -> tuple[WheelEntryPoint, ...]:
    parser = ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except Exception as exc:
        raise ConfigError(f"invalid wheel entry_points.txt / wheel entry_points.txt 无效: {exc}") from exc
    if not parser.has_section(ENTRY_POINT_GROUP):
        raise ConfigError(f"wheel does not provide {ENTRY_POINT_GROUP} / wheel 未提供可执行仪器 entry point")
    return _validated_entry_points(parser.items(ENTRY_POINT_GROUP))


def _validated_entry_points(items: Iterable[tuple[str, object]]) -> tuple[WheelEntryPoint, ...]:
    result: list[WheelEntryPoint] = []
    seen: set[str] = set()
    for raw_name, raw_value in items:
        name = str(raw_name)
        value = str(raw_value)
        if not name or name.strip() != name or any(char.isspace() for char in name):
            raise ConfigError(f"invalid instrument entry point name / 仪器 entry point 名称无效: {name!r}")
        if name in seen:
            raise ConfigError(f"duplicate instrument entry point / 重复的仪器 entry point: {name}")
        if not value.strip() or ":" not in value:
            raise ConfigError(f"invalid instrument entry point target / 仪器 entry point 目标无效: {name}")
        seen.add(name)
        result.append(WheelEntryPoint(name, value.strip()))
    if not result:
        raise ConfigError("plugin wheel has no instrument entry points / 插件 wheel 没有仪器 entry point")
    if len(result) != 1:
        raise ConfigError(
            "plugin wheel must provide exactly one instrument entry point / "
            "插件 wheel 必须恰好提供一个仪器 entry point"
        )
    return tuple(sorted(result, key=lambda item: item.driver_id))


def _validate_wheel_members(infos: Iterable[object]) -> None:
    items = tuple(infos)
    if len(items) > MAX_WHEEL_MEMBERS:
        raise ConfigError("plugin wheel contains too many members / 插件 wheel 成员过多")
    names = [info.filename for info in items]
    if len(names) != len(set(names)):
        raise ConfigError("plugin wheel contains duplicate members / 插件 wheel 包含重复成员")
    if sum(info.file_size for info in items) > MAX_WHEEL_UNCOMPRESSED_BYTES:
        raise ConfigError("plugin wheel expands beyond the size limit / 插件 wheel 解压后超过大小限制")
    for info in items:
        name = info.filename
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ConfigError(f"unsafe wheel member path / 不安全的 wheel 成员路径: {name}")
        if path.parts and path.parts[0] == "wavebench":
            raise ConfigError(
                f"plugin wheel must not overwrite the WaveBench core package / "
                f"插件 wheel 不能覆盖 WaveBench 核心包: {name}"
            )
        if path.suffix == ".pth":
            raise ConfigError(f"plugin wheel pth files are not supported / 不支持插件 wheel 的 pth 文件: {name}")
        if any(part.endswith(".data") for part in path.parts):
            raise ConfigError(f"plugin wheel data relocation is not supported / 不支持 wheel data 重定位: {name}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise ConfigError(f"wheel symlink members are not supported / 不支持 wheel 软链接成员: {name}")


def _read_small_text(archive: ZipFile, name: str) -> str:
    info = archive.getinfo(name)
    if info.file_size > MAX_METADATA_BYTES:
        raise ConfigError(f"wheel metadata file is too large / wheel metadata 文件过大: {PurePosixPath(name).name}")
    try:
        return archive.read(info).decode("utf-8")
    except UnicodeError as exc:
        raise ConfigError(f"wheel metadata must be UTF-8 / wheel metadata 必须是 UTF-8: {PurePosixPath(name).name}") from exc


def _validate_wheel_metadata(text: str) -> frozenset[object]:
    message = Parser().parsestr(text)
    wheel_versions = tuple(message.get_all("Wheel-Version", ()))
    if wheel_versions != ("1.0",):
        raise ConfigError(
            "plugin wheel must declare exactly Wheel-Version 1.0 / "
            "插件 wheel 必须且只能声明 Wheel-Version 1.0"
        )
    purelib = (message.get("Root-Is-Purelib") or "").strip().lower()
    if purelib != "true":
        raise ConfigError("plugin wheel must be pure Python / 插件 wheel 必须是纯 Python wheel")
    tag_lines = tuple(message.get_all("Tag", ()))
    if not tag_lines:
        raise ConfigError("wheel metadata is missing Tag / wheel 元数据缺少 Tag")
    try:
        tags = frozenset(tag for value in tag_lines for tag in parse_tag(value))
    except ValueError as exc:
        raise ConfigError("wheel metadata contains an invalid Tag / wheel 元数据包含无效 Tag") from exc
    if not tags:
        raise ConfigError("wheel metadata is missing Tag / wheel 元数据缺少 Tag")
    return tags


def _validate_record(
    archive: ZipFile,
    infos: Iterable[object],
    record_name: str,
    record_text: str,
) -> tuple[str, ...]:
    rows: dict[str, tuple[str, str]] = {}
    try:
        for row in csv.reader(record_text.splitlines()):
            if len(row) != 3 or not row[0] or row[0] in rows:
                raise ValueError("invalid RECORD row")
            rows[row[0]] = (row[1], row[2])
    except (csv.Error, ValueError) as exc:
        raise ConfigError("invalid wheel RECORD / wheel RECORD 无效") from exc
    members = tuple(sorted(info.filename for info in infos if not info.is_dir()))
    if set(rows) != set(members):
        raise ConfigError("wheel RECORD file list does not match archive members / RECORD 文件清单与 wheel 不一致")
    for name in members:
        digest_text, size_text = rows[name]
        if name == record_name:
            if digest_text or size_text:
                raise ConfigError("wheel RECORD self-entry must omit hash and size / RECORD 自身条目不能包含哈希和大小")
            continue
        if not digest_text.startswith("sha256="):
            raise ConfigError(f"wheel RECORD must use sha256 hashes / RECORD 必须使用 sha256: {name}")
        try:
            expected = urlsafe_b64decode(digest_text[7:] + "===")
            expected_size = int(size_text)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"invalid wheel RECORD hash or size / RECORD 哈希或大小无效: {name}") from exc
        payload = archive.read(name)
        if sha256(payload).digest() != expected:
            raise ConfigError(f"wheel RECORD hash mismatch / RECORD hash 不匹配: {name}")
        if len(payload) != expected_size:
            raise ConfigError(f"wheel RECORD size mismatch / RECORD 大小不匹配: {name}")
    return members


def _validate_python_requirement(value: str | None) -> None:
    if not value:
        return
    try:
        specifier = SpecifierSet(value)
    except InvalidSpecifier as exc:
        raise ConfigError(f"invalid Requires-Python / Requires-Python 无效: {value}") from exc
    current = Version(".".join(str(item) for item in sys.version_info[:3]))
    if current not in specifier:
        raise ConfigError(f"plugin requires Python {value}; current is {current} / 插件与当前 Python 版本不兼容")


def _validate_wavebench_requirement(dependencies: tuple[str, ...]) -> None:
    matches: list[Requirement] = []
    for text in dependencies:
        try:
            requirement = Requirement(text)
        except InvalidRequirement as exc:
            raise ConfigError(f"invalid Requires-Dist / Requires-Dist 无效: {text}") from exc
        if canonicalize_name(requirement.name) == "wavebench" and (
            requirement.marker is None or requirement.marker.evaluate()
        ):
            matches.append(requirement)
    if len(matches) != 1:
        raise ConfigError("plugin must declare exactly one active WaveBench dependency / 插件必须声明一个生效的 WaveBench 依赖")
    if Version(__version__) not in matches[0].specifier:
        raise ConfigError(
            f"plugin requires {matches[0]}; current WaveBench is {__version__} / "
            "插件与当前 WaveBench 版本不兼容"
        )


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize_subprocess_detail(text: str, source: Path) -> str:
    compact = " ".join(text.split())[-1600:] or "no build output"
    replacements = {
        str(source): "<source>",
        str(source.parent): "<source-parent>",
        str(Path.home()): "~",
        str(Path(sys.prefix)): "<environment>",
    }
    for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if old:
            compact = compact.replace(old, new)
    return compact
