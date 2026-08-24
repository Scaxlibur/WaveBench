from __future__ import annotations

from base64 import urlsafe_b64encode
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from wavebench.errors import ConfigError
from wavebench.instruments.source_conformance import (
    SOURCE_CONFORMANCE_DIRECTORY,
    SOURCE_CONFORMANCE_SCHEMA,
    SOURCE_CONFORMANCE_SCHEME,
    source_conformance_evidence_digest,
    source_conformance_wheel_binding_digest,
)
from wavebench.instruments.source_extensions import SOURCE_CONTRACT_VERSION
from wavebench.plugins.package_inspect import (
    build_subprocess_environment,
    inspect_plugin_package,
    inspect_plugin_wheel,
)


def test_subprocess_environment_preserves_required_windows_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.setenv("Path", r"C:\\Windows\\System32")
    monkeypatch.setenv("SystemRoot", r"C:\\Windows")
    monkeypatch.setenv("TEMP", r"C:\\Temp")
    monkeypatch.setenv("USERPROFILE", r"C:\\Users\\tester")
    monkeypatch.setenv("ComSpec", r"C:\\Windows\\System32\\cmd.exe")
    monkeypatch.setenv("PIP_CONFIG_FILE", "should-not-survive")

    environment = build_subprocess_environment()

    assert environment["PATH"] == r"C:\\Windows\\System32"
    assert "Path" not in environment
    assert environment["SystemRoot"] == r"C:\\Windows"
    assert environment["TEMP"] == r"C:\\Temp"
    assert environment["USERPROFILE"] == r"C:\\Users\\tester"
    assert environment["ComSpec"].endswith("cmd.exe")
    assert environment["PIP_CONFIG_FILE"] != "should-not-survive"
    assert environment["PYTHONNOUSERSITE"] == "1"


def _wheel(
    root: Path,
    *,
    name: str = "wavebench-example-scope",
    version: str = "0.1.0",
    filename_tag: str = "py3-none-any",
    wheel_tag: str = "py3-none-any",
    wheel_version: str | None = "1.0",
    entry_points: str = "[wavebench.instruments]\nexample.scope = example:descriptor\n",
    requires_python: str = ">=3.11",
    requires_dist: str | tuple[str, ...] = "wavebench>=0.8,<0.9",
    extra_members: dict[str, bytes] | None = None,
    include_record: bool = True,
) -> Path:
    filename_name = name.replace("-", "_")
    path = root / f"{filename_name}-{version}-{filename_tag}.whl"
    dist_info = f"{filename_name}-{version}.dist-info"
    dependency_lines = "".join(
        f"Requires-Dist: {dependency}\n"
        for dependency in ((requires_dist,) if isinstance(requires_dist, str) else requires_dist)
    )
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {name}\n"
        f"Version: {version}\n"
        f"Requires-Python: {requires_python}\n"
        f"{dependency_lines}\n"
    )
    members = {
        f"{dist_info}/METADATA": metadata.encode(),
        f"{dist_info}/WHEEL": (
            (f"Wheel-Version: {wheel_version}\n" if wheel_version is not None else "")
            + "Root-Is-Purelib: true\n"
            + f"Tag: {wheel_tag}\n"
        ).encode(),
        f"{dist_info}/entry_points.txt": entry_points.encode(),
        "wavebench_example_scope/__init__.py": b"def descriptor():\n    return None\n",
        **(extra_members or {}),
    }
    if include_record:
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        for member, payload in members.items():
            digest = urlsafe_b64encode(sha256(payload).digest()).rstrip(b"=").decode()
            writer.writerow((member, f"sha256={digest}", len(payload)))
        writer.writerow((f"{dist_info}/RECORD", "", ""))
        members[f"{dist_info}/RECORD"] = output.getvalue().encode()
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for member, payload in members.items():
            archive.writestr(member, payload)
    return path


def _wheel_with_source_conformance(
    root: Path,
    *,
    wheel_sha256: str | None = None,
    manifest_path: str | None = None,
) -> Path:
    name = "wavebench-example-scope"
    version = "0.1.0"
    dist_info = "wavebench_example_scope-0.1.0.dist-info"
    manifest_id = "example-basic-read-a1"
    path = manifest_path or (
        f"{dist_info}/{SOURCE_CONFORMANCE_DIRECTORY}/{manifest_id}.json"
    )
    placeholder = _wheel(root, extra_members={path: b"{}"})
    with ZipFile(placeholder) as archive:
        binding = source_conformance_wheel_binding_digest(
            (
                (info.filename, archive.read(info))
                for info in archive.infolist()
                if not info.is_dir()
            ),
            dist_info=dist_info,
        )
    document: dict[str, object] = {
        "schema": SOURCE_CONFORMANCE_SCHEMA,
        "manifest_id": manifest_id,
        "conformance_scheme": SOURCE_CONFORMANCE_SCHEME,
        "claimed_level": "A1",
        "capability": "source.snapshot_v2",
        "feature": "basic",
        "direction": "read",
        "model": "EX1",
        "firmware_id": "1.0",
        "option_ids": [],
        "channels": [1],
        "core_version": "0.8.24",
        "plugin_version": version,
        "wheel_sha256": wheel_sha256 or binding,
        "descriptor_digest": "sha256:" + "1" * 64,
        "source_contract_version": SOURCE_CONTRACT_VERSION,
        "fixture": {"transport": "fake"},
        "safety_limits": {"output": "off"},
        "budget": {"admitted": True},
        "results": {"queries": 3},
        "session_health": {"after": "healthy", "before": "healthy"},
        "final_state": {"output": "off"},
        "coverage": ["basic read contract"],
        "limitations": ["no hardware coverage"],
        "evidence_digest": "sha256:" + "0" * 64,
    }
    document["evidence_digest"] = source_conformance_evidence_digest(document)
    return _wheel(
        root,
        name=name,
        version=version,
        extra_members={
            path: json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        },
    )


def test_inspect_wheel_reads_metadata_entry_points_and_hash(tmp_path):
    path = _wheel(tmp_path)

    package = inspect_plugin_wheel(path)

    assert package.distribution == "wavebench-example-scope"
    assert package.normalized_distribution == "wavebench-example-scope"
    assert package.version == "0.1.0"
    assert package.driver_ids == ("example.scope",)
    assert len(package.sha256) == 64
    assert package.size_bytes == path.stat().st_size
    assert package.source_kind == "wheel"


def test_inspect_wheel_verifies_source_conformance_binding(tmp_path):
    path = _wheel_with_source_conformance(tmp_path)

    package = inspect_plugin_wheel(path)

    assert package.source_conformance_wheel_sha256 is not None
    assert tuple(
        item.manifest_id for item in package.source_conformance_manifests
    ) == ("example-basic-read-a1",)


def test_inspect_wheel_rejects_source_conformance_binding_mismatch(tmp_path):
    path = _wheel_with_source_conformance(
        tmp_path,
        wheel_sha256="sha256:" + "9" * 64,
    )

    with pytest.raises(ConfigError, match="wheel_sha256"):
        inspect_plugin_wheel(path)


def test_inspect_wheel_rejects_cross_distribution_source_conformance(tmp_path):
    path = _wheel_with_source_conformance(
        tmp_path,
        manifest_path=(
            "foreign-0.1.0.dist-info/"
            f"{SOURCE_CONFORMANCE_DIRECTORY}/example-basic-read-a1.json"
        ),
    )

    with pytest.raises(ConfigError, match="belong to the wheel distribution"):
        inspect_plugin_wheel(path)


@pytest.mark.parametrize(
    ("entry_points", "message"),
    [
        ("[console_scripts]\nexample = example:main\n", "does not provide"),
        ("[wavebench.instruments]\nexample.scope = invalid\n", "target"),
    ],
)
def test_inspect_wheel_rejects_missing_or_invalid_instrument_entry_points(
    tmp_path,
    entry_points,
    message,
):
    path = _wheel(tmp_path, entry_points=entry_points)

    with pytest.raises(ConfigError, match=message):
        inspect_plugin_wheel(path)


def test_inspect_wheel_rejects_incompatible_wavebench_version(tmp_path):
    path = _wheel(tmp_path, requires_dist="wavebench>=99")

    with pytest.raises(ConfigError, match="current WaveBench"):
        inspect_plugin_wheel(path)


def test_source_v2_wheel_is_rejected_before_entry_point_import_on_old_core(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    sentinel = tmp_path / "entry-point-imported"
    path = _wheel(
        tmp_path,
        requires_dist="wavebench>=0.8.24,<0.9",
        extra_members={
            "wavebench_example_scope/__init__.py": (
                "from pathlib import Path\n"
                f"Path({str(sentinel)!r}).write_text('imported', encoding='utf-8')\n"
                "def descriptor():\n    return None\n"
            ).encode(),
        },
    )
    monkeypatch.setattr("wavebench.plugins.package_inspect.__version__", "0.8.23")

    with pytest.raises(ConfigError, match="current WaveBench"):
        inspect_plugin_wheel(path)

    assert not sentinel.exists()


def test_inspect_wheel_rejects_filename_and_metadata_tag_mismatch(tmp_path):
    path = _wheel(tmp_path, wheel_tag="py2-none-any")

    with pytest.raises(ConfigError, match="filename tags"):
        inspect_plugin_wheel(path)


@pytest.mark.parametrize("wheel_version", [None, "bogus", "2.0"])
def test_inspect_wheel_requires_supported_wheel_version(tmp_path, wheel_version):
    path = _wheel(tmp_path, wheel_version=wheel_version)

    with pytest.raises(ConfigError, match="Wheel-Version 1.0"):
        inspect_plugin_wheel(path)


def test_inspect_wheel_rejects_unsafe_member_path(tmp_path):
    path = _wheel(tmp_path, extra_members={"../escape.py": b"bad"})

    with pytest.raises(ConfigError, match="unsafe wheel member"):
        inspect_plugin_wheel(path)


@pytest.mark.parametrize(
    ("extra_members", "message"),
    [
        ({"wavebench/cli.py": b"override"}, "core package"),
        ({"plugin-bootstrap.pth": b"import plugin_bootstrap"}, "pth"),
    ],
)
def test_inspect_wheel_rejects_core_overrides_and_pth_files(
    tmp_path,
    extra_members,
    message,
):
    path = _wheel(tmp_path, extra_members=extra_members)

    with pytest.raises(ConfigError, match=message):
        inspect_plugin_wheel(path)


def test_inspect_wheel_requires_and_verifies_record(tmp_path):
    missing = _wheel(tmp_path, version="0.1.0", include_record=False)

    with pytest.raises(ConfigError, match="RECORD"):
        inspect_plugin_wheel(missing)

    tampered = _wheel(tmp_path, version="0.2.0")
    original = tmp_path / "original.whl"
    tampered.rename(original)
    with ZipFile(original) as source, ZipFile(tampered, "w", ZIP_DEFLATED) as archive:
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == "wavebench_example_scope/__init__.py":
                payload = b"tampered"
            archive.writestr(info, payload)

    with pytest.raises(ConfigError, match="RECORD hash"):
        inspect_plugin_wheel(tampered)


def test_inspect_wheel_rejects_duplicate_members(tmp_path):
    path = _wheel(tmp_path)
    with pytest.warns(UserWarning, match="Duplicate name"):
        with ZipFile(path, "a", ZIP_DEFLATED) as archive:
            archive.writestr("wavebench_example_scope/__init__.py", b"def descriptor():\n    return None\n")

    with pytest.raises(ConfigError, match="duplicate members"):
        inspect_plugin_wheel(path)


def test_inspect_wheel_rejects_excessive_uncompressed_size(tmp_path, monkeypatch):
    path = _wheel(tmp_path)
    monkeypatch.setattr(
        "wavebench.plugins.package_inspect.MAX_WHEEL_UNCOMPRESSED_BYTES",
        1,
    )

    with pytest.raises(ConfigError, match="expands beyond"):
        inspect_plugin_wheel(path)


def test_inspect_wheel_rejects_multi_driver_distribution(tmp_path):
    path = _wheel(
        tmp_path,
        entry_points=(
            "[wavebench.instruments]\n"
            "example.scope = example:scope_descriptor\n"
            "example.dmm = example:dmm_descriptor\n"
        ),
    )

    with pytest.raises(ConfigError, match="exactly one instrument entry point"):
        inspect_plugin_wheel(path)


def test_inspect_source_directory_builds_one_offline_wheel(tmp_path):
    source = tmp_path / "plugin"
    package_dir = source / "src" / "wavebench_example_scope"
    package_dir.mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        """
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "wavebench-example-scope"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["wavebench>=0.8,<0.9"]

[project.entry-points."wavebench.instruments"]
"example.scope" = "wavebench_example_scope:descriptor"

[tool.hatch.build.targets.wheel]
packages = ["src/wavebench_example_scope"]
""",
        encoding="utf-8",
    )
    (package_dir / "__init__.py").write_text("def descriptor():\n    return None\n", encoding="utf-8")
    build = tmp_path / "build"

    package = inspect_plugin_package(source, build_directory=build)

    assert package.source_kind == "source"
    assert package.input_path == source.resolve()
    assert package.build_backend == "hatchling.build"
    assert package.driver_ids == ("example.scope",)
    assert package.wheel_path.parent == build


def test_source_inspection_requires_explicit_build_directory(tmp_path):
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(ConfigError, match="temporary build directory"):
        inspect_plugin_package(source)
