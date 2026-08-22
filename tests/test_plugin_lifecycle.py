from __future__ import annotations

from base64 import urlsafe_b64encode
import csv
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import venv
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from wavebench.errors import ConfigError
from wavebench.plugins.lifecycle import PluginLifecycle


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def _target_venv(root: Path) -> Path:
    target = root / "venv"
    venv.EnvBuilder(with_pip=True).create(target)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    purelib = _run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]
    ).stdout.strip()
    Path(purelib, "wavebench-test-runtime.pth").write_text(
        str(Path(__file__).resolve().parents[1] / "src")
        + "\n"
        + sysconfig.get_paths()["purelib"]
        + "\n",
        encoding="utf-8",
    )
    return python


def _source_v2_descriptor_module(
    *,
    driver_id: str,
    capabilities: tuple[str, ...],
    minimum_version: str,
    maximum_version: str,
) -> bytes:
    return f'''from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.source_extensions import (
    SOURCE_CONTRACT_VERSION,
    SourceAmplitudeUnit,
    SourceBasicCapabilityProfile,
    SourceConstraintApplicability,
    SourceDescriptorExtensions,
    SourceFacetQueryContract,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureCapability,
    SourceFeatureDirection,
    SourceFieldId,
    SourceFrequencyMode,
    SourceOutputCapabilityProfile,
    SourceQueryContract,
    SourceQueryEffect,
    SourceSafetyProfile,
    SourceTopologyContract,
    SourceWaveformKind,
    SupportState,
)


class Driver:
    def close(self):
        pass

    def execute_source_query_plan_v2(self, plan):
        raise RuntimeError("query fixture must not execute during postflight")


def descriptor():
    applicability = SourceConstraintApplicability()
    features = (
        SourceFeatureCapability(
            feature=SourceFeature.BASIC,
            support=SupportState.SUPPORTED,
            directions=(SourceFeatureDirection.READ,),
            scope=SourceFacetScope.CHANNEL,
            channels=(1,),
            applicability=applicability,
            profile=SourceBasicCapabilityProfile(
                waveform_kinds=(SourceWaveformKind.SINE,),
                frequency_modes=(SourceFrequencyMode.FIXED,),
                amplitude_units=(SourceAmplitudeUnit.VPP,),
                offset_readable=True,
                phase_readable=True,
                square_duty_readable=False,
            ),
        ),
        SourceFeatureCapability(
            feature=SourceFeature.OUTPUT,
            support=SupportState.SUPPORTED,
            directions=(SourceFeatureDirection.READ,),
            scope=SourceFacetScope.CHANNEL,
            channels=(1,),
            applicability=applicability,
            profile=SourceOutputCapabilityProfile(
                output_readable=True,
                display_load_readable=False,
                polarity_readable=True,
            ),
        ),
    )
    query_contract = SourceQueryContract(
        anchor_fields=(
            SourceFieldId.BASIC,
            SourceFieldId.OUTPUT,
            SourceFieldId.IDENTITY,
        ),
        facets=(
            SourceFacetQueryContract(
                feature=SourceFeature.BASIC,
                scope=SourceFacetScope.CHANNEL,
                fields=(SourceFieldId.BASIC,),
                activation_any=(),
                effect=SourceQueryEffect.PURE_READ,
                max_queries=1,
                required=True,
            ),
            SourceFacetQueryContract(
                feature=SourceFeature.BASIC,
                scope=SourceFacetScope.INSTRUMENT,
                fields=(SourceFieldId.IDENTITY,),
                activation_any=(),
                effect=SourceQueryEffect.PURE_READ,
                max_queries=1,
                required=True,
            ),
            SourceFacetQueryContract(
                feature=SourceFeature.OUTPUT,
                scope=SourceFacetScope.CHANNEL,
                fields=(SourceFieldId.OUTPUT,),
                activation_any=(),
                effect=SourceQueryEffect.PURE_READ,
                max_queries=1,
                required=True,
            ),
        ),
        max_queries=3,
        timeout_ms=1000,
    )
    return InstrumentDescriptor(
        driver_id={driver_id!r},
        kind="source",
        display_name="Example Source V2",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities={capabilities!r},
        idn_patterns=("EXAMPLE,SOURCE",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda context: Driver(),
        wavebench_min_version={minimum_version!r},
        wavebench_max_version={maximum_version!r},
        source_extensions=SourceDescriptorExtensions(
            contract_version=SOURCE_CONTRACT_VERSION,
            topology=SourceTopologyContract((1,)),
            features=features,
            query_contract=query_contract,
            safety_profile=SourceSafetyProfile(),
        ),
    )
'''.encode()


def _plugin_wheel(
    root: Path,
    *,
    version: str,
    driver_id: str = "example.scope",
    distribution: str = "wavebench-example-scope",
    kind: str = "scope",
    capabilities: tuple[str, ...] = ("scope.idn",),
    broken_descriptor: bool = False,
    include_entry_point: bool = True,
    requires_dist: str | tuple[str, ...] = "wavebench>=0.8,<0.9",
    source_v2: bool = False,
    wavebench_min_version: str = "0.8.0",
    wavebench_max_version: str = "0.9.0",
) -> Path:
    filename_name = distribution.replace("-", "_")
    dist_info = f"{filename_name}-{version}.dist-info"
    package_name = "wavebench_example_scope"
    path = root / f"{filename_name}-{version}-py3-none-any.whl"
    dependency_lines = "".join(
        f"Requires-Dist: {dependency}\n"
        for dependency in ((requires_dist,) if isinstance(requires_dist, str) else requires_dist)
    )
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {distribution}\n"
        f"Version: {version}\n"
        "Requires-Python: >=3.11\n"
        f"{dependency_lines}\n"
    ).encode()
    if broken_descriptor:
        package = b"def descriptor():\n    raise RuntimeError('broken descriptor')\n"
    elif source_v2:
        package = _source_v2_descriptor_module(
            driver_id=driver_id,
            capabilities=capabilities,
            minimum_version=wavebench_min_version,
            maximum_version=wavebench_max_version,
        )
    else:
        package = f'''from wavebench.instruments.api import InstrumentDescriptor


class Driver:
    def idn(self):
        return "EXAMPLE,SCOPE"

    def close(self):
        pass


def descriptor():
    return InstrumentDescriptor(
        driver_id={driver_id!r},
        kind={kind!r},
        display_name="Example Instrument",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities={capabilities!r},
        idn_patterns=("EXAMPLE,SCOPE",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda context: Driver(),
    )
'''.encode()
    members = {
        f"{dist_info}/METADATA": metadata,
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        f"{package_name}/__init__.py": package,
    }
    if include_entry_point:
        members[f"{dist_info}/entry_points.txt"] = (
            f"[wavebench.instruments]\n{driver_id} = {package_name}:descriptor\n"
        ).encode()
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


def _raw_pip_install(python: Path, wheel: Path) -> None:
    _run(
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "install",
            "--isolated",
            "--no-index",
            "--no-deps",
            "--disable-pip-version-check",
            str(wheel),
        ]
    )


def _plugin_source(root: Path) -> Path:
    source = root / "source-plugin"
    package = source / "src" / "wavebench_source_scope"
    package.mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        """
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "wavebench-source-scope"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["wavebench>=0.8,<0.9"]

[project.entry-points."wavebench.instruments"]
"example.source-scope" = "wavebench_source_scope:descriptor"

[tool.hatch.build.targets.wheel]
packages = ["src/wavebench_source_scope"]
""",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        '''from wavebench.instruments.api import InstrumentDescriptor


class Driver:
    def idn(self):
        return "EXAMPLE,SOURCE-SCOPE"

    def close(self):
        pass


def descriptor():
    return InstrumentDescriptor(
        driver_id="example.source-scope",
        kind="scope",
        display_name="Example Source Scope",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=("scope.idn",),
        idn_patterns=("EXAMPLE,SOURCE-SCOPE",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda context: Driver(),
    )
''',
        encoding="utf-8",
    )
    return source


def test_lifecycle_rejects_system_python():
    system_python = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
    lifecycle = PluginLifecycle(python_executable=system_python)

    with pytest.raises(ConfigError, match="virtual environment"):
        lifecycle.installed()


def test_lifecycle_preserves_venv_launcher_path(tmp_path):
    python = _target_venv(tmp_path)
    launcher = python.parent / "wavebench-python-link"
    try:
        launcher.symlink_to(python.name)
    except OSError as exc:
        if os.name == "nt":
            pytest.skip(f"Windows symlink privilege unavailable: {exc}")
        raise
    lifecycle = PluginLifecycle(python_executable=launcher)

    environment = lifecycle.environment()

    assert environment.prefix == str(python.parents[1])
    assert Path(environment.python) == launcher


def test_lifecycle_cross_checks_source_v2_wheel_and_descriptor_versions(tmp_path):
    python = _target_venv(tmp_path)
    good = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        driver_id="example.source-v2",
        distribution="wavebench-example-source-v2",
        kind="source",
        capabilities=("source.snapshot_v2",),
        source_v2=True,
        wavebench_min_version="0.8.24",
        wavebench_max_version="0.9.0",
        requires_dist="wavebench>=0.8.24,<0.9",
    )
    bad_floor = _plugin_wheel(
        tmp_path,
        version="0.1.1",
        driver_id="example.source-v2",
        distribution="wavebench-example-source-v2",
        kind="source",
        capabilities=("source.snapshot_v2",),
        source_v2=True,
        wavebench_min_version="0.8.24",
        wavebench_max_version="0.9.0",
        requires_dist="wavebench>=0.8,<0.9",
    )
    lifecycle = PluginLifecycle(python_executable=python)

    assert lifecycle.install(good).status == "installed"
    assert lifecycle.info("example.source-v2").status == "healthy"
    assert lifecycle.remove("example.source-v2").status == "removed"

    with pytest.raises(ConfigError, match="explicitly include >=0.8.24,<0.9.0"):
        lifecycle.install(bad_floor)

    assert lifecycle.installed() == ()
    assert not lifecycle.journal_path.exists()


def test_dry_run_does_not_modify_target_venv(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(tmp_path, version="0.1.0")
    lifecycle = PluginLifecycle(python_executable=python)

    result = lifecycle.install(wheel, dry_run=True)

    assert result.status == "would-install"
    assert not (python.parents[1] / ".wavebench").exists()
    assert lifecycle.installed() == ()


def test_dry_run_refuses_unmanaged_distribution_without_writing_state(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(tmp_path, version="0.1.0")
    _raw_pip_install(python, wheel)
    lifecycle = PluginLifecycle(python_executable=python)

    with pytest.raises(ConfigError, match="unmanaged"):
        lifecycle.install(wheel, dry_run=True)

    assert not lifecycle.state_dir.exists()


def test_lifecycle_allows_only_the_declared_builtin_migration_canonical(tmp_path):
    python = _target_venv(tmp_path)
    dg4202_capabilities = (
        "source.idn",
        "source.errors",
        "source.status",
        "source.set_frequency",
        "source.set_function",
        "source.set_amplitude_vpp",
        "source.set_square_duty_cycle",
        "source.output",
        "source.arbitrary_probe",
        "source.arbitrary_upload",
    )
    migration = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        driver_id="rigol.dg4202",
        distribution="wavebench-rigol-dg4000",
        kind="source",
        capabilities=dg4202_capabilities,
    )
    dm3000_migration = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        driver_id="rigol.dm3000",
        distribution="wavebench-rigol-dm3000",
        kind="dmm",
        capabilities=("dmm.idn",),
    )
    dp800_migration = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        driver_id="rigol.dp800",
        distribution="wavebench-rigol-dp800",
        kind="power",
        capabilities=("power.idn",),
    )
    rtm2032_migration = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        driver_id="rohde-schwarz.rtm2032",
        distribution="wavebench-rohde-schwarz-rtm2000",
        kind="scope",
        capabilities=("scope.idn",),
    )
    forbidden = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        driver_id="rigol.ds1104",
        distribution="wavebench-rigol-ds1104-override",
    )
    lifecycle = PluginLifecycle(python_executable=python)

    assert lifecycle.install(migration, dry_run=True).status == "would-install"
    assert lifecycle.install(dm3000_migration, dry_run=True).status == "would-install"
    assert lifecycle.install(dp800_migration, dry_run=True).status == "would-install"
    assert lifecycle.install(rtm2032_migration, dry_run=True).status == "would-install"
    with pytest.raises(ConfigError, match="conflicts with built-in"):
        lifecycle.install(forbidden, dry_run=True)


def test_migration_install_routes_canonical_and_remove_restores_builtin(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        driver_id="rigol.dg4202",
        distribution="wavebench-rigol-dg4000",
        kind="source",
        capabilities=("source.idn",),
    )
    lifecycle = PluginLifecycle(python_executable=python)
    resolve_script = """
from wavebench.instruments.registry import build_instrument_registry
registry = build_instrument_registry()
canonical = registry.resolve('rigol.dg4202', expected_kind='source')
alias = registry.resolve('dg4202', expected_kind='source')
print(canonical.origin, canonical.distribution, alias.origin, alias.driver_id)
"""

    assert lifecycle.install(wheel).status == "installed"
    installed = _run([str(python), "-I", "-c", resolve_script]).stdout.strip()
    assert installed == "entry_point wavebench-rigol-dg4000 builtin rigol.dg4202"

    assert lifecycle.remove("rigol.dg4202").status == "removed"
    removed = _run([str(python), "-I", "-c", resolve_script]).stdout.strip()
    assert removed == "builtin wavebench builtin rigol.dg4202"


def test_dm3000_migration_install_routes_canonical_and_preserves_aliases(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        driver_id="rigol.dm3000",
        distribution="wavebench-rigol-dm3000",
        kind="dmm",
        capabilities=("dmm.idn",),
    )
    lifecycle = PluginLifecycle(python_executable=python)
    resolve_script = """
from wavebench.instruments.registry import build_instrument_registry
registry = build_instrument_registry()
canonical = registry.resolve('rigol.dm3000', expected_kind='dmm')
dm3000_alias = registry.resolve('dm3000', expected_kind='dmm')
dm3058_alias = registry.resolve('dm3058', expected_kind='dmm')
print(
    canonical.origin,
    canonical.distribution,
    dm3000_alias.origin,
    dm3058_alias.origin,
    canonical.driver_id,
)
"""

    assert lifecycle.install(wheel).status == "installed"
    installed = _run([str(python), "-I", "-c", resolve_script]).stdout.strip()
    assert installed == (
        "entry_point wavebench-rigol-dm3000 builtin builtin rigol.dm3000"
    )

    assert lifecycle.remove("rigol.dm3000").status == "removed"
    removed = _run([str(python), "-I", "-c", resolve_script]).stdout.strip()
    assert removed == "builtin wavebench builtin builtin rigol.dm3000"


def test_dp800_migration_install_routes_canonical_and_preserves_alias(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        driver_id="rigol.dp800",
        distribution="wavebench-rigol-dp800",
        kind="power",
        capabilities=("power.idn",),
    )
    lifecycle = PluginLifecycle(python_executable=python)
    resolve_script = """
from wavebench.instruments.registry import build_instrument_registry
registry = build_instrument_registry()
canonical = registry.resolve('rigol.dp800', expected_kind='power')
alias = registry.resolve('dp800', expected_kind='power')
print(canonical.origin, canonical.distribution, alias.origin, canonical.driver_id)
"""

    assert lifecycle.install(wheel).status == "installed"
    installed = _run([str(python), "-I", "-c", resolve_script]).stdout.strip()
    assert installed == "entry_point wavebench-rigol-dp800 builtin rigol.dp800"

    assert lifecycle.remove("rigol.dp800").status == "removed"
    removed = _run([str(python), "-I", "-c", resolve_script]).stdout.strip()
    assert removed == "builtin wavebench builtin rigol.dp800"


def test_rtm2032_migration_install_routes_canonical_and_preserves_alias(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        driver_id="rohde-schwarz.rtm2032",
        distribution="wavebench-rohde-schwarz-rtm2000",
        kind="scope",
        capabilities=("scope.idn",),
    )
    lifecycle = PluginLifecycle(python_executable=python)
    resolve_script = """
from wavebench.instruments.registry import build_instrument_registry
registry = build_instrument_registry()
canonical = registry.resolve('rohde-schwarz.rtm2032', expected_kind='scope')
alias = registry.resolve('rtm2032', expected_kind='scope')
print(canonical.origin, canonical.distribution, alias.origin, canonical.driver_id)
"""

    assert lifecycle.install(wheel).status == "installed"
    installed = _run([str(python), "-I", "-c", resolve_script]).stdout.strip()
    assert installed == (
        "entry_point wavebench-rohde-schwarz-rtm2000 builtin "
        "rohde-schwarz.rtm2032"
    )

    assert lifecycle.remove("rohde-schwarz.rtm2032").status == "removed"
    removed = _run([str(python), "-I", "-c", resolve_script]).stdout.strip()
    assert removed == "builtin wavebench builtin rohde-schwarz.rtm2032"


def test_install_status_and_remove_round_trip(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(tmp_path, version="0.1.0")
    lifecycle = PluginLifecycle(python_executable=python)

    installed = lifecycle.install(wheel)
    statuses = lifecycle.installed()

    assert installed.status == "installed"
    assert [(item.driver_id, item.version, item.status) for item in statuses] == [
        ("example.scope", "0.1.0", "healthy")
    ]
    assert lifecycle.info("example.scope").wheel_sha256 == sha256(wheel.read_bytes()).hexdigest()
    assert lifecycle.remove("example.scope").status == "removed"
    assert lifecycle.installed() == ()


def test_source_directory_install_keeps_built_wheel_alive_until_cached(tmp_path):
    python = _target_venv(tmp_path)
    source = _plugin_source(tmp_path)
    lifecycle = PluginLifecycle(python_executable=python)

    installed = lifecycle.install(source)

    assert installed.driver_id == "example.source-scope"
    assert lifecycle.info("example.source-scope").status == "healthy"
    assert lifecycle.remove("example.source-scope").status == "removed"


def test_install_refuses_to_take_over_unmanaged_distribution(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(tmp_path, version="0.1.0")
    _raw_pip_install(python, wheel)
    lifecycle = PluginLifecycle(python_executable=python)

    assert lifecycle.installed()[0].status == "unmanaged"
    with pytest.raises(ConfigError, match="unmanaged"):
        lifecycle.install(wheel)


def test_install_refuses_file_owned_by_another_distribution(tmp_path):
    python = _target_venv(tmp_path)
    foreign = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        distribution="foreign-scope-plugin",
        include_entry_point=False,
    )
    target = _plugin_wheel(tmp_path, version="0.1.0")
    _raw_pip_install(python, foreign)
    lifecycle = PluginLifecycle(python_executable=python)

    with pytest.raises(ConfigError, match="overlaps files"):
        lifecycle.install(target, dry_run=True)

    assert not lifecycle.state_dir.exists()


def test_install_refuses_unreadable_distribution_ownership(tmp_path):
    python = _target_venv(tmp_path)
    foreign = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        distribution="foreign-scope-plugin",
        include_entry_point=False,
    )
    target = _plugin_wheel(tmp_path, version="0.1.0")
    _raw_pip_install(python, foreign)
    purelib = Path(
        _run(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]
        ).stdout.strip()
    )
    record = next(purelib.glob("foreign_scope_plugin-*.dist-info/RECORD"))
    record.write_bytes(record.read_bytes() + b"\n")
    lifecycle = PluginLifecycle(python_executable=python)

    with pytest.raises(ConfigError, match="ownership cannot be proven"):
        lifecycle.install(target, dry_run=True)

    assert not lifecycle.state_dir.exists()


def test_install_refuses_distribution_without_record(tmp_path):
    python = _target_venv(tmp_path)
    foreign = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        distribution="foreign-scope-plugin",
        include_entry_point=False,
    )
    target = _plugin_wheel(tmp_path, version="0.1.0")
    _raw_pip_install(python, foreign)
    purelib = Path(
        _run(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]
        ).stdout.strip()
    )
    next(purelib.glob("foreign_scope_plugin-*.dist-info/RECORD")).unlink()
    lifecycle = PluginLifecycle(python_executable=python)

    with pytest.raises(ConfigError, match="ownership cannot be proven"):
        lifecycle.install(target, dry_run=True)

    assert not lifecycle.state_dir.exists()


def test_remove_refuses_files_shared_with_out_of_band_distribution(tmp_path):
    python = _target_venv(tmp_path)
    managed = _plugin_wheel(tmp_path, version="0.1.0")
    lifecycle = PluginLifecycle(python_executable=python)
    lifecycle.install(managed)
    foreign = _plugin_wheel(
        tmp_path,
        version="0.1.0",
        distribution="foreign-scope-plugin",
        include_entry_point=False,
    )
    _raw_pip_install(python, foreign)

    installed = lifecycle.info("example.scope")
    assert installed.status == "broken"
    assert "shared ownership" in installed.detail
    with pytest.raises(ConfigError, match="healthy"):
        lifecycle.remove("example.scope", dry_run=True)

    assert lifecycle.info("example.scope").status == "broken"


def test_upgrade_downgrade_and_failed_upgrade_roll_back(tmp_path):
    python = _target_venv(tmp_path)
    v1 = _plugin_wheel(tmp_path, version="0.1.0")
    v2 = _plugin_wheel(tmp_path, version="0.2.0")
    broken = _plugin_wheel(tmp_path, version="0.3.0", broken_descriptor=True)
    lifecycle = PluginLifecycle(python_executable=python)
    lifecycle.install(v1)

    assert lifecycle.upgrade(v2).status == "upgraded"
    assert lifecycle.info("example.scope").version == "0.2.0"
    assert lifecycle.downgrade(v1).status == "downgraded"
    assert lifecycle.info("example.scope").version == "0.1.0"

    with pytest.raises(ConfigError, match="postflight"):
        lifecycle.upgrade(broken)

    restored = lifecycle.info("example.scope")
    assert restored.version == "0.1.0"
    assert restored.status == "healthy"
    assert not lifecycle.journal_path.exists()


def test_out_of_band_change_is_reported_as_missing_or_drifted(tmp_path):
    python = _target_venv(tmp_path)
    v1 = _plugin_wheel(tmp_path, version="0.1.0")
    v2 = _plugin_wheel(tmp_path, version="0.2.0")
    lifecycle = PluginLifecycle(python_executable=python)
    lifecycle.install(v1)

    _raw_pip_install(python, v2)
    assert lifecycle.info("example.scope").status == "drifted"
    with pytest.raises(ConfigError, match="healthy"):
        lifecycle.remove("example.scope")

    _run([str(python), "-I", "-m", "pip", "uninstall", "--yes", "wavebench-example-scope"])
    assert lifecycle.info("example.scope").status == "missing"


def test_same_version_file_tampering_is_reported_as_drifted(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(tmp_path, version="0.1.0")
    lifecycle = PluginLifecycle(python_executable=python)
    lifecycle.install(wheel)
    purelib = Path(lifecycle.environment().purelib)
    (purelib / "wavebench_example_scope/__init__.py").write_text(
        "# out-of-band change\n",
        encoding="utf-8",
    )

    assert lifecycle.info("example.scope").status == "drifted"
    with pytest.raises(ConfigError, match="healthy"):
        lifecycle.remove("example.scope")


def test_record_tampering_is_reported_as_drifted(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(tmp_path, version="0.1.0")
    lifecycle = PluginLifecycle(python_executable=python)
    lifecycle.install(wheel)
    purelib = Path(lifecycle.environment().purelib)
    record = next(purelib.glob("wavebench_example_scope-*.dist-info/RECORD"))
    record.write_bytes(record.read_bytes() + b"\n")

    assert lifecycle.info("example.scope").status == "drifted"


def test_missing_entry_point_is_reported_as_drifted_not_missing(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(tmp_path, version="0.1.0")
    lifecycle = PluginLifecycle(python_executable=python)
    lifecycle.install(wheel)
    purelib = Path(lifecycle.environment().purelib)
    entry_points = next(purelib.glob("wavebench_example_scope-*.dist-info/entry_points.txt"))
    entry_points.unlink()

    installed = lifecycle.info("example.scope")

    assert installed.status == "drifted"


def test_prepared_journal_blocks_mutation_and_can_be_recovered(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(tmp_path, version="0.1.0")
    lifecycle = PluginLifecycle(python_executable=python)
    environment = lifecycle.environment()
    lifecycle.state_dir.mkdir(mode=0o700)
    with lifecycle._inspected_input(wheel) as package:
        cached = lifecycle._cache_wheel(package)
        record = lifecycle._package_record(package, cached)
    lifecycle.journal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment": environment.to_json(),
                "operation": "install",
                "stage": "prepared",
                "before_ledger": lifecycle.empty_ledger(environment),
                "package": record,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="recovery required"):
        lifecycle.install(wheel)

    assert lifecycle.recover().status == "recovered-before-mutation"
    assert not lifecycle.journal_path.exists()


def test_recover_commits_exact_desired_state_after_pip_finished(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(tmp_path, version="0.1.0")
    lifecycle = PluginLifecycle(python_executable=python)
    environment = lifecycle.environment()
    before = lifecycle.empty_ledger(environment)
    with lifecycle._inspected_input(wheel) as package:
        cached = lifecycle._cache_wheel(package)
        record = lifecycle._package_record(package, cached)
    journal = lifecycle._journal(
        environment=environment,
        operation="install",
        stage="pip_finished",
        before_ledger=before,
        package=record,
    )
    lifecycle.state_dir.mkdir(mode=0o700, exist_ok=True)
    lifecycle._write_json(lifecycle.journal_path, journal)
    lifecycle._pip_install(cached)

    result = lifecycle.recover()

    assert result.status == "recovered-to-desired"
    assert lifecycle.info("example.scope").status == "healthy"
    assert not lifecycle.journal_path.exists()


def test_recover_refuses_unknown_partial_state(tmp_path):
    python = _target_venv(tmp_path)
    wheel = _plugin_wheel(tmp_path, version="0.1.0")
    lifecycle = PluginLifecycle(python_executable=python)
    environment = lifecycle.environment()
    before = lifecycle.empty_ledger(environment)
    with lifecycle._inspected_input(wheel) as package:
        cached = lifecycle._cache_wheel(package)
        record = lifecycle._package_record(package, cached)
    lifecycle._pip_install(cached)
    purelib = Path(environment.purelib)
    (purelib / "wavebench_example_scope/__init__.py").write_text(
        "# partial state\n",
        encoding="utf-8",
    )
    journal = lifecycle._journal(
        environment=environment,
        operation="install",
        stage="pip_started",
        before_ledger=before,
        package=record,
    )
    lifecycle.state_dir.mkdir(mode=0o700, exist_ok=True)
    lifecycle._write_json(lifecycle.journal_path, journal)

    with pytest.raises(ConfigError, match="manual inspection"):
        lifecycle.recover()

    assert lifecycle.journal_path.exists()
