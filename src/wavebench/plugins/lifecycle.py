from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterable, Iterator

from packaging.utils import canonicalize_name
from packaging.version import Version

from wavebench.errors import ConfigError
from wavebench.instruments.builtin import BUILTIN_INSTRUMENTS
from wavebench.instruments.migrations import BUILTIN_MIGRATION_DISTRIBUTIONS

from wavebench.services.file_lock import FileLock, FileLockBusy, FileLockError, FileLockUnavailable
from wavebench.services.platform_io import atomic_write_json, ensure_private_directory

from .package_inspect import (
    PluginPackage,
    WheelEntryPoint,
    build_subprocess_environment,
    inspect_plugin_package,
)


LEDGER_SCHEMA_VERSION = 2
JOURNAL_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class EnvironmentInfo:
    python: str
    prefix: str
    base_prefix: str
    purelib: str
    platlib: str
    version: str
    fingerprint: str

    def to_json(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class InstalledPlugin:
    driver_id: str
    distribution: str
    version: str
    status: str
    wheel_sha256: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class LifecycleResult:
    status: str
    driver_id: str
    distribution: str
    version: str


class PluginLifecycle:
    """Manage trusted local instrument wheels in one WaveBench virtual environment."""

    def __init__(self, *, python_executable: str | Path) -> None:
        candidate = Path(python_executable).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        # Preserve a venv launcher path. Resolving the common ``bin/python``
        # symlink would silently turn it into the base interpreter.
        self.python_executable = candidate.absolute()
        self._environment: EnvironmentInfo | None = None

    @property
    def state_dir(self) -> Path:
        return Path(self.environment().prefix) / ".wavebench"

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "plugin-installs-v1.json"

    @property
    def journal_path(self) -> Path:
        return self.state_dir / "plugin-transaction-v1.json"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "plugin-installs-v1.lock"

    @property
    def wheel_cache(self) -> Path:
        return self.state_dir / "plugin-wheel-cache"

    def environment(self) -> EnvironmentInfo:
        if self._environment is not None:
            return self._environment
        script = """
import json
import os
import sys
import sysconfig

paths = sysconfig.get_paths()
payload = {
    "python": os.path.abspath(sys.executable),
    "prefix": os.path.realpath(sys.prefix),
    "base_prefix": os.path.realpath(sys.base_prefix),
    "purelib": os.path.realpath(paths["purelib"]),
    "platlib": os.path.realpath(paths["platlib"]),
    "version": ".".join(str(item) for item in sys.version_info[:3]),
    "in_venv": sys.prefix != sys.base_prefix or hasattr(sys, "real_prefix"),
}
print(json.dumps(payload, sort_keys=True))
"""
        payload = self._run_json([str(self.python_executable), "-I", "-c", script])
        if not payload.get("in_venv"):
            raise ConfigError(
                "plugin lifecycle requires a virtual environment / "
                "插件生命周期操作必须在虚拟环境中执行"
            )
        prefix = Path(str(payload["prefix"]))
        for field in ("python", "purelib", "platlib"):
            candidate = Path(str(payload[field]))
            try:
                candidate.relative_to(prefix)
            except ValueError as exc:
                raise ConfigError(
                    "virtual environment paths escape sys.prefix / "
                    "虚拟环境路径超出 sys.prefix"
                ) from exc
        fingerprint_text = "\0".join(
            str(payload[field])
            for field in ("python", "prefix", "purelib", "platlib", "version")
        )
        self._environment = EnvironmentInfo(
            python=str(payload["python"]),
            prefix=str(payload["prefix"]),
            base_prefix=str(payload["base_prefix"]),
            purelib=str(payload["purelib"]),
            platlib=str(payload["platlib"]),
            version=str(payload["version"]),
            fingerprint=sha256(fingerprint_text.encode()).hexdigest(),
        )
        return self._environment

    def empty_ledger(self, environment: EnvironmentInfo | None = None) -> dict[str, object]:
        current = environment or self.environment()
        return {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "environment": current.to_json(),
            "generation": 0,
            "plugins": {},
        }

    def installed(self) -> tuple[InstalledPlugin, ...]:
        environment = self.environment()
        ledger = self._load_ledger(environment)
        inventory = self._inventory()
        file_owners = self._file_owners() if self._ledger_plugins(ledger) else {}
        results: list[InstalledPlugin] = []
        managed_distributions: set[str] = set()
        for raw_record in self._ledger_plugins(ledger).values():
            record = self._record(raw_record)
            normalized = canonicalize_name(record["distribution"])
            managed_distributions.add(normalized)
            matches = self._distribution_inventory(record["distribution"])
            if not matches:
                status = "missing"
                detail = "managed distribution is not installed / 受管分发未安装"
            elif len(matches) != 1:
                status = "broken"
                detail = "multiple installed distributions match / 存在多个同名分发"
            else:
                item = matches[0]
                expected_entries = self._record_entry_point_pairs(record)
                actual_entries = tuple(
                    sorted(
                        (str(entry["name"]), str(entry["value"]))
                        for entry in item.get("entry_points", ())
                    )
                )
                healthy = (
                    item.get("version") == record["version"]
                    and actual_entries == expected_entries
                    and item.get("integrity") is True
                    and item.get("metadata_sha256") == record["metadata_sha256"]
                    and item.get("record_sha256") == record["installed_record_sha256"]
                    and item.get("files_sha256") == record["installed_files_sha256"]
                )
                shared = self._shared_file_path(
                    (str(path) for path in item.get("files", ())),
                    file_owners,
                    ignored_distribution=record["distribution"],
                )
                if shared:
                    status = "broken"
                    detail = f"installed files have shared ownership / 安装文件存在共享归属: {shared}"
                else:
                    status = "healthy" if healthy else "drifted"
                    detail = "" if healthy else "installed metadata or files drifted / 安装元数据或文件已漂移"
            results.extend(
                InstalledPlugin(
                    driver_id=entry.driver_id,
                    distribution=record["distribution"],
                    version=record["version"],
                    status=status,
                    wheel_sha256=record["wheel_sha256"],
                    detail=detail,
                )
                for entry in self._record_entry_points(record)
            )
        for normalized, matches in sorted(inventory.items()):
            if normalized in managed_distributions:
                continue
            for item in matches:
                for entry in item.get("entry_points", ()):
                    results.append(
                        InstalledPlugin(
                            driver_id=str(entry["name"]),
                            distribution=str(item["name"]),
                            version=str(item["version"]),
                            status="unmanaged",
                            detail="distribution is not managed by WaveBench / 分发不受 WaveBench 管理",
                        )
                    )
        return tuple(sorted(results, key=lambda item: (item.driver_id, item.distribution)))

    def info(self, driver_id: str) -> InstalledPlugin:
        matches = [item for item in self.installed() if item.driver_id == driver_id]
        if not matches:
            raise ConfigError(
                f"installed plugin not found / 未找到已安装插件: {driver_id}"
            )
        if len(matches) != 1:
            raise ConfigError(
                f"installed plugin state is ambiguous / 已安装插件状态不唯一: {driver_id}"
            )
        return matches[0]

    def install(self, path: str | Path, *, dry_run: bool = False) -> LifecycleResult:
        with self._inspected_input(path) as package:
            self._assert_package_identity(package)
            result = LifecycleResult(
                status="would-install" if dry_run else "installed",
                driver_id=package.driver_ids[0],
                distribution=package.distribution,
                version=package.version,
            )
            if dry_run:
                environment = self.environment()
                ledger = self._load_ledger(environment)
                self._assert_first_install_allowed(package, ledger)
                return result
            with self._locked():
                environment = self.environment()
                self._assert_no_pending_journal()
                ledger = self._load_ledger(environment)
                self._assert_first_install_allowed(package, ledger)
                cached = self._cache_wheel(package)
                self._run_install_transaction(
                    operation="install",
                    package=package,
                    cached_wheel=cached,
                    ledger=ledger,
                    previous_record=None,
                )
            return result

    def upgrade(self, path: str | Path, *, dry_run: bool = False) -> LifecycleResult:
        return self._replace(path, direction="upgrade", dry_run=dry_run)

    def downgrade(self, path: str | Path, *, dry_run: bool = False) -> LifecycleResult:
        return self._replace(path, direction="downgrade", dry_run=dry_run)

    def remove(self, driver_id: str, *, dry_run: bool = False) -> LifecycleResult:
        environment = self.environment()
        if dry_run:
            current = self.info(driver_id)
            self._require_healthy(current)
            self._assert_distribution_file_ownership(current.distribution)
            return LifecycleResult(
                "would-remove",
                current.driver_id,
                current.distribution,
                current.version,
            )
        with self._locked():
            self._assert_no_pending_journal()
            ledger = self._load_ledger(environment)
            current = self.info(driver_id)
            self._require_healthy(current)
            record = self._record_for_driver_id(ledger, driver_id)
            self._assert_distribution_file_ownership(record["distribution"])
            rollback_wheel = self._record_wheel(record)
            journal = self._journal(
                environment=environment,
                operation="remove",
                stage="prepared",
                before_ledger=ledger,
                package=record,
            )
            self._write_json(self.journal_path, journal)
            try:
                self._update_journal(journal, "pip_started")
                self._pip_uninstall(record["distribution"])
                self._update_journal(journal, "pip_finished")
                if self._distribution_inventory(record["distribution"]):
                    raise ConfigError("plugin remove postflight failed / 插件卸载后检查失败")
                updated = self._without_record(ledger, record["normalized_distribution"])
                self._write_json(self.ledger_path, updated)
                self._update_journal(journal, "ledger_committed")
                self._remove_journal()
            except Exception as exc:
                self._rollback_install(rollback_wheel, record, ledger, journal, exc)
        return LifecycleResult(
            "removed",
            driver_id,
            record["distribution"],
            record["version"],
        )

    def recover(self) -> LifecycleResult:
        environment = self.environment()
        with self._locked():
            if not self.journal_path.exists():
                return LifecycleResult("nothing-to-recover", "", "", "")
            journal = self._read_json(self.journal_path, "transaction journal")
            if journal.get("schema_version") == 1:
                journal = self._migrate_journal_v1(journal)
                self._write_json(self.journal_path, journal)
            self._validate_journal(journal, environment)
            return self._recover_journal(journal, environment)

    def _replace(
        self,
        path: str | Path,
        *,
        direction: str,
        dry_run: bool,
    ) -> LifecycleResult:
        with self._inspected_input(path) as package:
            self._assert_package_identity(package)
            ledger = self._load_ledger(self.environment())
            current_record = self._record_for_distribution(
                ledger,
                package.normalized_distribution,
            )
            self._assert_replacement_entry_points(package, current_record)
            self._assert_additional_entry_points_available(
                package,
                current_record,
                ledger,
            )
            driver_id = self._record_entry_points(current_record)[0].driver_id
            current = self.info(driver_id)
            self._require_healthy(current)
            if canonicalize_name(current.distribution) != package.normalized_distribution:
                raise ConfigError(
                    "replacement distribution does not match managed plugin / "
                    "替换包的 distribution 与受管插件不一致"
                )
            current_version = Version(current.version)
            target_version = Version(package.version)
            valid_direction = (
                target_version > current_version
                if direction == "upgrade"
                else target_version < current_version
            )
            if not valid_direction:
                raise ConfigError(
                    f"plugin {direction} target has the wrong version direction / "
                    f"插件{direction}目标版本方向错误"
                )
            self._assert_no_file_ownership_conflicts(
                package,
                self._file_owners(strict=True),
                ignored_distribution=current.distribution,
            )
            result = LifecycleResult(
                "would-upgrade" if dry_run and direction == "upgrade" else
                "would-downgrade" if dry_run else
                "upgraded" if direction == "upgrade" else "downgraded",
                driver_id,
                package.distribution,
                package.version,
            )
            if dry_run:
                return result
            with self._locked():
                environment = self.environment()
                self._assert_no_pending_journal()
                ledger = self._load_ledger(environment)
                record = self._record_for_distribution(
                    ledger,
                    package.normalized_distribution,
                )
                self._assert_replacement_entry_points(package, record)
                self._assert_additional_entry_points_available(package, record, ledger)
                locked_current = self.info(driver_id)
                if locked_current.status != "healthy":
                    raise ConfigError("managed plugin must be healthy before replacement / 替换前插件必须健康")
                if canonicalize_name(locked_current.distribution) != package.normalized_distribution:
                    raise ConfigError(
                        "replacement distribution does not match managed plugin / "
                        "替换包的 distribution 与受管插件不一致"
                    )
                locked_version = Version(locked_current.version)
                locked_direction_valid = (
                    target_version > locked_version
                    if direction == "upgrade"
                    else target_version < locked_version
                )
                if not locked_direction_valid:
                    raise ConfigError(
                        f"plugin {direction} target has the wrong version direction / "
                        f"插件{direction}目标版本方向错误"
                    )
                self._assert_no_file_ownership_conflicts(
                    package,
                    self._file_owners(strict=True),
                    ignored_distribution=locked_current.distribution,
                )
                self._record_wheel(record)
                cached = self._cache_wheel(package)
                self._run_install_transaction(
                    operation=direction,
                    package=package,
                    cached_wheel=cached,
                    ledger=ledger,
                    previous_record=record,
                )
            return result

    def _run_install_transaction(
        self,
        *,
        operation: str,
        package: PluginPackage,
        cached_wheel: Path,
        ledger: dict[str, object],
        previous_record: dict[str, object] | None,
    ) -> None:
        environment = self.environment()
        record = self._package_record(package, cached_wheel)
        journal = self._journal(
            environment=environment,
            operation=operation,
            stage="prepared",
            before_ledger=ledger,
            package=record,
        )
        self._write_json(self.journal_path, journal)
        try:
            self._update_journal(journal, "pip_started")
            self._pip_install(cached_wheel)
            self._update_journal(journal, "pip_finished")
            postflight = self._postflight(record)
            record["installed_files_sha256"] = postflight["files_sha256"]
            record["installed_record_sha256"] = postflight["record_sha256"]
            self._update_journal(journal, "postflight_finished")
            updated = self._with_record(ledger, record)
            self._write_json(self.ledger_path, updated)
            self._update_journal(journal, "ledger_committed")
            self._remove_journal()
        except Exception as exc:
            if previous_record is None:
                self._rollback_uninstall(record, ledger, journal, exc)
            else:
                self._rollback_install(
                    self._record_wheel(previous_record),
                    previous_record,
                    ledger,
                    journal,
                    exc,
                )

    def _rollback_uninstall(
        self,
        record: dict[str, object],
        ledger: dict[str, object],
        journal: dict[str, object],
        original: Exception,
    ) -> None:
        try:
            self._update_journal(journal, "rollback_started")
            self._pip_uninstall(record["distribution"], allow_missing=True)
            if self._distribution_inventory(record["distribution"]):
                raise ConfigError("rollback uninstall did not remove distribution")
            self._write_json(self.ledger_path, ledger)
            self._remove_journal()
        except Exception as rollback_error:
            raise ConfigError(
                "recovery required: plugin install and rollback both failed / "
                "需要恢复：插件安装与回滚均失败"
            ) from rollback_error
        raise ConfigError(f"plugin postflight failed / 插件安装后检查失败: {original}") from original

    def _rollback_install(
        self,
        wheel: Path,
        record: dict[str, object],
        ledger: dict[str, object],
        journal: dict[str, object],
        original: Exception,
    ) -> None:
        try:
            self._update_journal(journal, "rollback_started")
            self._pip_install(wheel)
            self._postflight(record)
            self._write_json(self.ledger_path, ledger)
            self._remove_journal()
        except Exception as rollback_error:
            raise ConfigError(
                "recovery required: plugin operation and rollback both failed / "
                "需要恢复：插件操作与回滚均失败"
            ) from rollback_error
        raise ConfigError(f"plugin postflight failed / 插件操作后检查失败: {original}") from original

    @contextmanager
    def _inspected_input(self, path: str | Path) -> Iterator[PluginPackage]:
        with tempfile.TemporaryDirectory(prefix="wavebench-plugin-build-") as temporary:
            yield inspect_plugin_package(
                path,
                build_directory=temporary,
                python_executable=self.python_executable,
            )

    def _recover_journal(
        self,
        journal: dict[str, object],
        environment: EnvironmentInfo,
    ) -> LifecycleResult:
        stage = journal.get("stage")
        operation = journal.get("operation")
        before = journal.get("before_ledger")
        package = journal.get("package")
        if not isinstance(before, dict) or not isinstance(package, dict):
            raise ConfigError("invalid plugin transaction journal / 插件事务日志无效")
        record = self._record(package)
        driver_id = self._record_entry_points(record)[0].driver_id
        record_key = record["normalized_distribution"]
        current_ledger = self._load_ledger(environment)
        before_record_raw = self._ledger_plugins(before).get(record_key)
        before_record = self._record(before_record_raw) if before_record_raw is not None else None
        before_matches = (
            self._distribution_absent(record)
            if before_record is None
            else self._record_matches_environment(before_record)
        )
        desired_matches = (
            self._distribution_absent(record)
            if operation == "remove"
            else self._record_matches_environment(record, allow_empty_digest=True)
        )

        if stage == "prepared":
            if current_ledger != before or not before_matches:
                return self._recovery_required()
            self._remove_journal()
            return LifecycleResult("recovered-before-mutation", driver_id, record["distribution"], record["version"])

        if stage == "ledger_committed":
            if operation == "remove":
                ledger_matches = record_key not in self._ledger_plugins(current_ledger)
            else:
                ledger_record = self._ledger_plugins(current_ledger).get(record_key)
                ledger_matches = ledger_record == package
            if not desired_matches or not ledger_matches:
                return self._recovery_required()
            self._remove_journal()
            return LifecycleResult("recovered-after-commit", driver_id, record["distribution"], record["version"])

        if stage in {"pip_started", "pip_finished", "postflight_finished"}:
            if desired_matches:
                if operation == "remove":
                    updated = self._without_record(before, record_key)
                else:
                    postflight = self._postflight(record)
                    record["installed_files_sha256"] = str(postflight["files_sha256"])
                    record["installed_record_sha256"] = str(postflight["record_sha256"])
                    updated = self._with_record(before, record)
                self._write_json(self.ledger_path, updated)
                self._remove_journal()
                return LifecycleResult("recovered-to-desired", driver_id, record["distribution"], record["version"])
            if before_matches:
                self._write_json(self.ledger_path, before)
                self._remove_journal()
                return LifecycleResult("recovered-to-before", driver_id, record["distribution"], record["version"])

        if stage == "rollback_started" and before_matches:
            self._write_json(self.ledger_path, before)
            self._remove_journal()
            return LifecycleResult("recovered-after-rollback", driver_id, record["distribution"], record["version"])
        return self._recovery_required()

    @staticmethod
    def _recovery_required() -> LifecycleResult:
        raise ConfigError(
            "recovery required: transaction state needs manual inspection / "
            "需要恢复：事务状态必须人工检查"
        )

    def _record_matches_environment(
        self,
        record: dict[str, object],
        *,
        allow_empty_digest: bool = False,
    ) -> bool:
        matches = self._distribution_inventory(record["distribution"])
        if len(matches) != 1:
            return False
        item = matches[0]
        expected_entries = self._record_entry_point_pairs(record)
        actual_entries = tuple(
            sorted(
                (str(entry["name"]), str(entry["value"]))
                for entry in item.get("entry_points", ())
            )
        )
        digest_matches = item.get("files_sha256") == record["installed_files_sha256"]
        record_matches = item.get("record_sha256") == record["installed_record_sha256"]
        metadata_matches = bool(
            item.get("version") == record["version"]
            and actual_entries == expected_entries
            and item.get("integrity") is True
            and item.get("metadata_sha256") == record["metadata_sha256"]
        )
        if not metadata_matches:
            return False
        if digest_matches and record_matches:
            return True
        if (
            not allow_empty_digest
            or record["installed_files_sha256"]
            or record["installed_record_sha256"]
        ):
            return False
        try:
            self._postflight(record)
        except ConfigError:
            return False
        return True

    def _distribution_absent(self, record: dict[str, object]) -> bool:
        return not self._distribution_inventory(record["distribution"])

    def _assert_package_identity(self, package: PluginPackage) -> None:
        builtin_references = {
            reference
            for descriptor in BUILTIN_INSTRUMENTS
            for reference in (descriptor.driver_id, *descriptor.aliases)
        }
        for driver_id in package.driver_ids:
            if driver_id not in builtin_references:
                continue
            expected_distribution = BUILTIN_MIGRATION_DISTRIBUTIONS.get(driver_id)
            if package.normalized_distribution == canonicalize_name(expected_distribution or ""):
                continue
            raise ConfigError(
                f"external plugin conflicts with built-in driver / "
                f"外置插件与内置驱动冲突: {driver_id}"
            )

    def _assert_first_install_allowed(
        self,
        package: PluginPackage,
        ledger: dict[str, object],
    ) -> None:
        records = tuple(
            self._record(raw_record)
            for raw_record in self._ledger_plugins(ledger).values()
        )
        if package.normalized_distribution in self._ledger_plugins(ledger):
            raise ConfigError("plugin is already managed; use upgrade or downgrade / 插件已受管，请使用升级或降级")
        managed_driver_ids = {
            entry.driver_id
            for record in records
            for entry in self._record_entry_points(record)
        }
        if managed_driver_ids.intersection(package.driver_ids):
            raise ConfigError("managed driver ID is already installed / 受管的驱动 ID 已安装")
        inventory = self._inventory()
        if package.normalized_distribution in inventory:
            raise ConfigError("unmanaged distribution is already installed / 未受管的同名分发已安装")
        for matches in inventory.values():
            for item in matches:
                if any(
                    str(entry["name"]) in package.driver_ids
                    for entry in item.get("entry_points", ())
                ):
                    raise ConfigError("unmanaged driver ID is already installed / 未受管的同名驱动已安装")
        self._assert_no_file_ownership_conflicts(
            package,
            self._file_owners(strict=True),
        )

    @staticmethod
    def _assert_no_file_ownership_conflicts(
        package: PluginPackage,
        file_owners: dict[str, tuple[str, ...]],
        *,
        ignored_distribution: str | None = None,
    ) -> None:
        overlap = PluginLifecycle._shared_file_path(
            package.member_paths,
            file_owners,
            ignored_distribution=ignored_distribution,
        )
        if overlap:
            raise ConfigError(
                "plugin wheel overlaps files owned by another distribution / "
                f"插件 wheel 与其他分发拥有的文件重叠: {overlap}"
            )

    @staticmethod
    def _shared_file_path(
        paths: Iterable[str],
        file_owners: dict[str, tuple[str, ...]],
        *,
        ignored_distribution: str | None = None,
    ) -> str | None:
        ignored = canonicalize_name(ignored_distribution) if ignored_distribution else None
        for path in sorted(set(paths)):
            if any(owner != ignored for owner in file_owners.get(path, ())):
                return path
        return None

    def _assert_distribution_file_ownership(self, distribution: str) -> None:
        normalized = canonicalize_name(distribution)
        inventory = self._inventory(target_distribution=distribution)
        matches = inventory.get(normalized, ())
        if len(matches) != 1:
            raise ConfigError(
                "managed distribution ownership is ambiguous / 受管分发的文件归属不唯一"
            )
        overlap = self._shared_file_path(
            (str(path) for path in matches[0].get("files", ())),
            self._file_owners(strict=True),
            ignored_distribution=normalized,
        )
        if overlap:
            raise ConfigError(
                "managed plugin shares files with another distribution / "
                f"受管插件与其他分发共享文件: {overlap}"
            )

    @staticmethod
    def _require_healthy(plugin: InstalledPlugin) -> None:
        if plugin.status != "healthy":
            raise ConfigError(
                f"managed plugin must be healthy for this operation / "
                f"执行此操作前受管插件必须健康: {plugin.status}"
            )

    def _cache_wheel(self, package: PluginPackage) -> Path:
        target_dir = self.wheel_cache / package.sha256
        target = target_dir / package.wheel_path.name
        if target.is_file() and self._sha256_file(target) == package.sha256:
            return target
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target_dir / f".{target.name}.tmp-{os.getpid()}"
        with package.wheel_path.open("rb") as source, temporary.open("xb") as output:
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        if self._sha256_file(temporary) != package.sha256:
            temporary.unlink(missing_ok=True)
            raise ConfigError("cached wheel hash mismatch / 缓存 wheel 哈希不匹配")
        os.replace(temporary, target)
        self._fsync_directory(target_dir)
        return target

    def _record_wheel(self, record: dict[str, object]) -> Path:
        candidate = self.state_dir / record["wheel_cache"]
        try:
            candidate.resolve().relative_to(self.wheel_cache.resolve())
        except ValueError as exc:
            raise ConfigError("managed wheel cache path is unsafe / 受管 wheel 缓存路径不安全") from exc
        if not candidate.is_file() or self._sha256_file(candidate) != record["wheel_sha256"]:
            raise ConfigError("managed rollback wheel is missing or changed / 受管回滚 wheel 缺失或已变化")
        return candidate

    def _package_record(self, package: PluginPackage, cached_wheel: Path) -> dict[str, object]:
        return {
            "distribution": package.distribution,
            "normalized_distribution": package.normalized_distribution,
            "version": package.version,
            "entry_points": [
                {"driver_id": entry.driver_id, "value": entry.value}
                for entry in package.entry_points
            ],
            "wheel_sha256": package.sha256,
            "metadata_sha256": package.metadata_sha256,
            "record_sha256": package.record_sha256,
            "wheel_cache": str(cached_wheel.relative_to(self.state_dir)),
            "installed_files_sha256": "",
            "installed_record_sha256": "",
        }

    def _postflight(self, record: dict[str, object]) -> dict[str, object]:
        script = r'''
import base64
import csv
import hashlib
from importlib.metadata import distributions
import io
import json
import os
import sysconfig
import zipfile

from wavebench.instruments.api import descriptor_from_entry_point
from wavebench.instruments.registry import _validate_descriptor
from wavebench.instruments.source_conformance import validate_source_conformance_distribution
from wavebench.instruments.source_extension_capabilities import validate_source_plugin_dependencies
from wavebench.instruments.rf_source_capabilities import validate_rf_source_plugin_dependencies

(
    expected_name,
    expected_version,
    expected_entries_json,
    expected_metadata_sha256,
    expected_installed_record_sha256,
    wheel_path,
) = __import__("sys").argv[1:]
try:
    expected_entries = tuple(
        sorted(
            (str(item["driver_id"]), str(item["value"]))
            for item in json.loads(expected_entries_json)
        )
    )
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit("expected entry point record is invalid")
paths = list(dict.fromkeys((sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"])))
matches = []
for dist in distributions(path=paths):
    name = dist.metadata.get("Name", "")
    normalized = __import__("re").sub(r"[-_.]+", "-", name).lower()
    if normalized == expected_name:
        matches.append(dist)
if len(matches) != 1:
    raise SystemExit("expected exactly one installed distribution")
dist = matches[0]
if dist.version != expected_version:
    raise SystemExit("installed version mismatch")
dist_files = tuple(dist.files or ())
def installed_metadata_hash(suffix):
    candidates = [item for item in dist_files if str(item).endswith(suffix)]
    if len(candidates) != 1:
        raise SystemExit(f"installed metadata file is missing or ambiguous: {suffix}")
    digest = hashlib.sha256()
    with open(dist.locate_file(candidates[0]), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
if installed_metadata_hash(".dist-info/METADATA") != expected_metadata_sha256:
    raise SystemExit("installed METADATA hash mismatch")
installed_record_sha256 = installed_metadata_hash(".dist-info/RECORD")
if expected_installed_record_sha256 and installed_record_sha256 != expected_installed_record_sha256:
    raise SystemExit("installed RECORD hash mismatch")
entries = [entry for entry in dist.entry_points if entry.group == "wavebench.instruments"]
actual_entries = tuple(sorted((entry.name, entry.value) for entry in entries))
if actual_entries != expected_entries:
    raise SystemExit("installed entry point mismatch")
for expected_driver, expected_value in expected_entries:
    entry = next(
        item
        for item in entries
        if item.name == expected_driver and item.value == expected_value
    )
    descriptor = descriptor_from_entry_point(entry.load())
    if descriptor.driver_id != expected_driver or descriptor.aliases:
        raise SystemExit("descriptor identity mismatch")
    descriptor = descriptor.with_distribution(
        distribution=dist.metadata.get("Name", ""),
        version=dist.version,
        source=f"entry_point:{expected_driver}",
        origin="entry_point",
    )
    _validate_descriptor(descriptor, expected_kind=None)
    validate_source_conformance_distribution(descriptor, dist)
    validate_source_plugin_dependencies(
        descriptor,
        tuple(dist.metadata.get_all("Requires-Dist") or ()),
    )
    validate_rf_source_plugin_dependencies(
        descriptor,
        tuple(dist.metadata.get_all("Requires-Dist") or ()),
    )
with zipfile.ZipFile(wheel_path) as archive:
    record_names = [name for name in archive.namelist() if name.endswith(".dist-info/RECORD")]
    if len(record_names) != 1:
        raise SystemExit("cached wheel RECORD is missing or ambiguous")
    wheel_record = archive.read(record_names[0]).decode("utf-8")
    for row in csv.reader(io.StringIO(wheel_record)):
        if len(row) != 3 or not row[1]:
            continue
        path, hash_field, size_field = row
        algorithm, separator, expected_digest = hash_field.partition("=")
        if algorithm != "sha256" or separator != "=":
            raise SystemExit(f"unsupported cached wheel hash: {path}")
        target = dist.locate_file(path)
        if not os.path.isfile(target):
            raise SystemExit(f"cached wheel member missing after install: {path}")
        digest = hashlib.sha256()
        size = 0
        with open(target, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        encoded = base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode()
        if encoded != expected_digest or (size_field and size != int(size_field)):
            raise SystemExit(f"installed file does not match cached wheel: {path}")
file_rows = []
for item in dist.files or ():
    target = dist.locate_file(item)
    if item.hash is None or item.hash.mode != "sha256" or not os.path.isfile(target):
        continue
    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    encoded = __import__("base64").urlsafe_b64encode(digest.digest()).rstrip(b"=").decode()
    if encoded != item.hash.value:
        raise SystemExit(f"installed file hash mismatch: {item}")
    file_rows.append((str(item), encoded))
files_digest = hashlib.sha256(json.dumps(sorted(file_rows)).encode()).hexdigest()
print(json.dumps({
    "driver_ids": [driver_id for driver_id, _value in expected_entries],
    "files_sha256": files_digest,
    "record_sha256": installed_record_sha256,
}))
'''
        try:
            return self._run_json(
                [
                    self.environment().python,
                    "-I",
                    "-c",
                    script,
                    canonicalize_name(record["distribution"]),
                    record["version"],
                    json.dumps(
                        [
                            {"driver_id": entry.driver_id, "value": entry.value}
                            for entry in self._record_entry_points(record)
                        ],
                        sort_keys=True,
                    ),
                    record["metadata_sha256"],
                    record["installed_record_sha256"],
                    str(self._record_wheel(record)),
                ]
            )
        except ConfigError as exc:
            raise ConfigError(f"plugin postflight failed / 插件安装后检查失败: {exc}") from exc

    def _inventory(
        self,
        *,
        target_distribution: str | None = None,
    ) -> dict[str, tuple[dict[str, object], ...]]:
        script = r'''
import base64
import hashlib
from importlib.metadata import distributions
import json
import os
import re
import sysconfig

paths = list(dict.fromkeys((sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"])))
target_name = __import__("sys").argv[1]
result = []
for dist in distributions(path=paths):
    entries = [entry for entry in dist.entry_points if entry.group == "wavebench.instruments"]
    name = dist.metadata.get("Name", "")
    normalized_name = re.sub(r"[-_.]+", "-", name).lower()
    if target_name:
        if normalized_name != target_name:
            continue
    elif not entries:
        continue
    integrity = True
    rows = []
    try:
        dist_files = tuple(dist.files or ())
    except Exception:
        integrity = False
        dist_files = ()
    for item in dist_files:
        target = dist.locate_file(item)
        if item.hash is None or item.hash.mode != "sha256":
            continue
        if not os.path.isfile(target):
            integrity = False
            continue
        digest = hashlib.sha256()
        with open(target, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        encoded = base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode()
        if encoded != item.hash.value:
            integrity = False
        rows.append((str(item), encoded))
    def metadata_hash(suffix):
        dist_info = getattr(dist, "_path", None)
        if dist_info is None:
            return ""
        target = os.path.join(str(dist_info), suffix.rsplit("/", 1)[-1])
        if not os.path.isfile(target):
            return ""
        digest = hashlib.sha256()
        with open(target, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    result.append({
        "name": name,
        "normalized_name": normalized_name,
        "version": dist.version,
        "entry_points": [{"name": entry.name, "value": entry.value} for entry in entries],
        "integrity": integrity,
        "metadata_sha256": metadata_hash(".dist-info/METADATA"),
        "record_sha256": metadata_hash(".dist-info/RECORD"),
        "files_sha256": hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest(),
        "files": sorted(str(item) for item in dist_files),
    })
print(json.dumps(result))
'''
        rows = self._run_json(
            [
                self.environment().python,
                "-I",
                "-c",
                script,
                canonicalize_name(target_distribution) if target_distribution else "",
            ]
        )
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            grouped.setdefault(str(row["normalized_name"]), []).append(row)
        return {key: tuple(value) for key, value in grouped.items()}

    def _file_owners(self, *, strict: bool = False) -> dict[str, tuple[str, ...]]:
        script = r'''
from importlib.metadata import distributions
import json
import re
import sysconfig

paths = list(dict.fromkeys((sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"])))
owners = {}
unreadable = []
for dist in distributions(path=paths):
    name = dist.metadata.get("Name", "")
    normalized = re.sub(r"[-_.]+", "-", name).lower()
    try:
        raw_files = dist.files
        if raw_files is None:
            unreadable.append(normalized or "<unknown>")
            dist_files = ()
        else:
            dist_files = tuple(raw_files)
    except Exception:
        unreadable.append(normalized or "<unknown>")
        dist_files = ()
    for item in dist_files:
        owners.setdefault(str(item), []).append(normalized)
print(json.dumps({
    "owners": {path: sorted(set(names)) for path, names in owners.items()},
    "unreadable": sorted(set(unreadable)),
}))
'''
        rows = self._run_json([self.environment().python, "-I", "-c", script])
        if not isinstance(rows, dict) or not isinstance(rows.get("owners"), dict):
            raise ConfigError("plugin ownership probe returned invalid data / 插件归属探测返回无效数据")
        unreadable = rows.get("unreadable")
        if strict and isinstance(unreadable, list) and unreadable:
            names = ", ".join(str(name) for name in unreadable)
            raise ConfigError(
                "plugin file ownership cannot be proven because installed RECORD data is unreadable / "
                f"已安装 RECORD 数据不可读，无法证明插件文件归属: {names}"
            )
        return {
            str(path): tuple(str(name) for name in names)
            for path, names in rows["owners"].items()
            if isinstance(names, list)
        }

    def _distribution_inventory(self, distribution: str) -> tuple[dict[str, object], ...]:
        return self._inventory(target_distribution=distribution).get(
            canonicalize_name(distribution),
            (),
        )

    def _pip_install(self, wheel: Path) -> None:
        self._run_command(
            [
                self.environment().python,
                "-I",
                "-m",
                "pip",
                "install",
                "--isolated",
                "--no-index",
                "--no-deps",
                "--no-cache-dir",
                "--no-input",
                "--disable-pip-version-check",
                "--force-reinstall",
                str(wheel),
            ],
            action="plugin install / 插件安装",
        )

    def _pip_uninstall(self, distribution: str, *, allow_missing: bool = False) -> None:
        try:
            self._run_command(
                [
                    self.environment().python,
                    "-I",
                    "-m",
                    "pip",
                    "uninstall",
                    "--isolated",
                    "--no-input",
                    "--disable-pip-version-check",
                    "--yes",
                    distribution,
                ],
                action="plugin remove / 插件卸载",
            )
        except ConfigError:
            if not allow_missing:
                raise

    def _run_command(self, command: list[str], *, action: str) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                command,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=180,
                env=build_subprocess_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ConfigError(f"{action} timed out / 操作超时") from exc
        if completed.returncode != 0:
            detail = " ".join((completed.stderr or completed.stdout).split())[-1200:]
            raise ConfigError(f"{action} failed / 操作失败: {detail or 'no output'}")
        return completed

    def _run_json(self, command: list[str]) -> object:
        completed = self._run_command(command, action="plugin environment probe / 插件环境探测")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ConfigError("plugin probe returned invalid JSON / 插件探测返回无效 JSON") from exc

    @contextmanager
    def _locked(self) -> Iterator[None]:
        ensure_private_directory(self.state_dir)
        lock = FileLock(self.lock_path, mode="exclusive")
        try:
            try:
                lock.acquire()
            except FileLockBusy as exc:
                raise ConfigError("plugin environment is busy / 插件环境正被其他操作占用") from exc
            except (FileLockUnavailable, FileLockError) as exc:
                raise ConfigError(f"plugin environment lock unavailable / 插件环境锁不可用: {exc}") from exc
            yield
        finally:
            lock.release()

    def _load_ledger(self, environment: EnvironmentInfo) -> dict[str, object]:
        if not self.ledger_path.exists():
            return self.empty_ledger(environment)
        ledger = self._read_json(self.ledger_path, "plugin ledger")
        schema_version = ledger.get("schema_version")
        if schema_version == 1:
            ledger = self._migrate_ledger_v1(ledger)
        elif schema_version != LEDGER_SCHEMA_VERSION:
            raise ConfigError("unsupported plugin ledger schema / 不支持的插件账本 schema")
        stored_environment = ledger.get("environment")
        if not isinstance(stored_environment, dict) or stored_environment.get("fingerprint") != environment.fingerprint:
            raise ConfigError("plugin ledger belongs to a different environment / 插件账本属于其他环境")
        self._ledger_plugins(ledger)
        return ledger

    @staticmethod
    def _ledger_plugins(ledger: dict[str, object]) -> dict[str, object]:
        plugins = ledger.get("plugins")
        if not isinstance(plugins, dict):
            raise ConfigError("invalid plugin ledger / 插件账本无效")
        return plugins

    @staticmethod
    def _legacy_record(value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            raise ConfigError("invalid managed plugin record / 受管插件记录无效")
        required = (
            "driver_id",
            "distribution",
            "normalized_distribution",
            "version",
            "entry_point",
            "wheel_sha256",
            "metadata_sha256",
            "record_sha256",
            "wheel_cache",
            "installed_files_sha256",
            "installed_record_sha256",
        )
        if any(not isinstance(value.get(field), str) for field in required):
            raise ConfigError("invalid managed plugin record / 受管插件记录无效")
        return {field: str(value[field]) for field in required}

    def _migrate_ledger_v1(self, ledger: dict[str, object]) -> dict[str, object]:
        migrated: dict[str, object] = {}
        for driver_id, raw_record in self._ledger_plugins(ledger).items():
            legacy = self._legacy_record(raw_record)
            if driver_id != legacy["driver_id"]:
                raise ConfigError("invalid plugin ledger / 插件账本无效")
            normalized = legacy["normalized_distribution"]
            if normalized in migrated:
                raise ConfigError("legacy plugin ledger has duplicate distributions / 旧插件账本存在重复分发")
            migrated[normalized] = self._legacy_record_to_record(legacy)
        return {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "environment": ledger.get("environment"),
            "generation": ledger.get("generation", 0),
            "plugins": migrated,
        }

    def _migrate_journal_v1(self, journal: dict[str, object]) -> dict[str, object]:
        before = journal.get("before_ledger")
        package = journal.get("package")
        if not isinstance(before, dict) or not isinstance(package, dict):
            raise ConfigError("invalid plugin transaction journal / 插件事务日志无效")
        migrated = dict(journal)
        migrated["schema_version"] = JOURNAL_SCHEMA_VERSION
        migrated["before_ledger"] = self._migrate_ledger_v1(before)
        migrated["package"] = self._legacy_record_to_record(self._legacy_record(package))
        return migrated

    @staticmethod
    def _legacy_record_to_record(legacy: dict[str, str]) -> dict[str, object]:
        return {
            "distribution": legacy["distribution"],
            "normalized_distribution": legacy["normalized_distribution"],
            "version": legacy["version"],
            "entry_points": [
                {
                    "driver_id": legacy["driver_id"],
                    "value": legacy["entry_point"],
                }
            ],
            "wheel_sha256": legacy["wheel_sha256"],
            "metadata_sha256": legacy["metadata_sha256"],
            "record_sha256": legacy["record_sha256"],
            "wheel_cache": legacy["wheel_cache"],
            "installed_files_sha256": legacy["installed_files_sha256"],
            "installed_record_sha256": legacy["installed_record_sha256"],
        }

    @staticmethod
    def _record(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ConfigError("invalid managed plugin record / 受管插件记录无效")
        required = (
            "distribution",
            "normalized_distribution",
            "version",
            "wheel_sha256",
            "metadata_sha256",
            "record_sha256",
            "wheel_cache",
            "installed_files_sha256",
            "installed_record_sha256",
        )
        if any(not isinstance(value.get(field), str) for field in required):
            raise ConfigError("invalid managed plugin record / 受管插件记录无效")
        entries = value.get("entry_points")
        if not isinstance(entries, list) or not entries:
            raise ConfigError("invalid managed plugin record / 受管插件记录无效")
        points: list[WheelEntryPoint] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ConfigError("invalid managed plugin record / 受管插件记录无效")
            driver_id = entry.get("driver_id")
            entry_point = entry.get("value")
            if (
                not isinstance(driver_id, str)
                or not driver_id
                or driver_id.strip() != driver_id
                or any(character.isspace() for character in driver_id)
                or not isinstance(entry_point, str)
                or not entry_point.strip()
                or ":" not in entry_point
            ):
                raise ConfigError("invalid managed plugin record / 受管插件记录无效")
            points.append(WheelEntryPoint(driver_id, entry_point))
        ordered = tuple(sorted(points, key=lambda entry: entry.driver_id))
        if tuple(points) != ordered or len({entry.driver_id for entry in points}) != len(points):
            raise ConfigError("invalid managed plugin record / 受管插件记录无效")
        record = {field: str(value[field]) for field in required}
        if record["normalized_distribution"] != canonicalize_name(record["distribution"]):
            raise ConfigError("invalid managed plugin record / 受管插件记录无效")
        record["entry_points"] = [
            {"driver_id": entry.driver_id, "value": entry.value}
            for entry in points
        ]
        return record

    @staticmethod
    def _record_entry_points(record: dict[str, object]) -> tuple[WheelEntryPoint, ...]:
        return tuple(
            WheelEntryPoint(str(entry["driver_id"]), str(entry["value"]))
            for entry in record["entry_points"]
        )

    @classmethod
    def _record_entry_point_pairs(cls, record: dict[str, object]) -> tuple[tuple[str, str], ...]:
        return tuple((entry.driver_id, entry.value) for entry in cls._record_entry_points(record))

    def _record_for_distribution(
        self,
        ledger: dict[str, object],
        normalized_distribution: str,
    ) -> dict[str, object]:
        raw_record = self._ledger_plugins(ledger).get(normalized_distribution)
        if raw_record is None:
            raise ConfigError("managed plugin not found / 未找到受管插件")
        return self._record(raw_record)

    def _record_for_driver_id(
        self,
        ledger: dict[str, object],
        driver_id: str,
    ) -> dict[str, object]:
        for raw_record in self._ledger_plugins(ledger).values():
            record = self._record(raw_record)
            if any(entry.driver_id == driver_id for entry in self._record_entry_points(record)):
                return record
        raise ConfigError(f"installed plugin not found / 未找到已安装插件: {driver_id}")

    def _assert_replacement_entry_points(
        self,
        package: PluginPackage,
        record: dict[str, object],
    ) -> None:
        expected = set(self._record_entry_point_pairs(record))
        actual = {(entry.driver_id, entry.value) for entry in package.entry_points}
        if not expected <= actual:
            raise ConfigError(
                "replacement entry points do not preserve managed plugin / "
                "替换包的 entry point 未保留受管插件"
            )

    def _assert_additional_entry_points_available(
        self,
        package: PluginPackage,
        record: dict[str, object],
        ledger: dict[str, object],
    ) -> None:
        existing_driver_ids = {
            entry.driver_id for entry in self._record_entry_points(record)
        }
        additional_driver_ids = set(package.driver_ids) - existing_driver_ids
        if not additional_driver_ids:
            return
        for raw_record in self._ledger_plugins(ledger).values():
            candidate = self._record(raw_record)
            if candidate["normalized_distribution"] == record["normalized_distribution"]:
                continue
            if additional_driver_ids.intersection(
                entry.driver_id for entry in self._record_entry_points(candidate)
            ):
                raise ConfigError("managed driver ID is already installed / 受管的驱动 ID 已安装")
        for normalized, matches in self._inventory().items():
            if normalized == record["normalized_distribution"]:
                continue
            for item in matches:
                if any(
                    str(entry["name"]) in additional_driver_ids
                    for entry in item.get("entry_points", ())
                ):
                    raise ConfigError("unmanaged driver ID is already installed / 未受管的同名驱动已安装")

    def _with_record(
        self,
        ledger: dict[str, object],
        record: dict[str, object],
    ) -> dict[str, object]:
        updated = json.loads(json.dumps(ledger))
        updated["generation"] = int(updated.get("generation", 0)) + 1
        self._ledger_plugins(updated)[record["normalized_distribution"]] = record
        return updated

    def _without_record(
        self,
        ledger: dict[str, object],
        normalized_distribution: str,
    ) -> dict[str, object]:
        updated = json.loads(json.dumps(ledger))
        updated["generation"] = int(updated.get("generation", 0)) + 1
        del self._ledger_plugins(updated)[normalized_distribution]
        return updated

    def _journal(
        self,
        *,
        environment: EnvironmentInfo,
        operation: str,
        stage: str,
        before_ledger: dict[str, object],
        package: object,
    ) -> dict[str, object]:
        return {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "environment": environment.to_json(),
            "operation": operation,
            "stage": stage,
            "before_ledger": before_ledger,
            "package": package,
        }

    def _assert_no_pending_journal(self) -> None:
        if self.journal_path.exists():
            raise ConfigError(
                "recovery required: unfinished plugin transaction exists / "
                "需要恢复：存在未完成的插件事务"
            )

    def _validate_journal(
        self,
        journal: dict[str, object],
        environment: EnvironmentInfo,
    ) -> None:
        if journal.get("schema_version") != JOURNAL_SCHEMA_VERSION:
            raise ConfigError("unsupported plugin journal schema / 不支持的插件事务 schema")
        stored = journal.get("environment")
        if not isinstance(stored, dict) or stored.get("fingerprint") != environment.fingerprint:
            raise ConfigError("plugin journal belongs to a different environment / 插件事务属于其他环境")

    def _update_journal(self, journal: dict[str, object], stage: str) -> None:
        journal["stage"] = stage
        self._write_json(self.journal_path, journal)

    def _remove_journal(self) -> None:
        self.journal_path.unlink(missing_ok=True)
        self._fsync_directory(self.state_dir)

    def _read_json(self, path: Path, label: str) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"invalid {label} / {label} 无效") from exc
        if not isinstance(payload, dict):
            raise ConfigError(f"invalid {label} / {label} 无效")
        return payload

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        atomic_write_json(path, payload, indent=2)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        # Kept as a compatibility helper for callers/tests.  The shared
        # atomic writer performs the platform-appropriate durability step.
        if os.name != "nt":
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
