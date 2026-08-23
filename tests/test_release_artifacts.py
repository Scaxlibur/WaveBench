import os
import subprocess
import sys
import sysconfig
import tarfile
import venv
from pathlib import Path
from zipfile import ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def _build_artifact(*, target: str, output_dir: Path) -> Path:
    _run(
        [sys.executable, "-m", "hatchling", "build", "-t", target, "-d", str(output_dir)],
        cwd=PROJECT_ROOT,
    )
    suffix = "*.whl" if target == "wheel" else "*.tar.gz"
    artifacts = list(output_dir.glob(f"wavebench-{suffix}"))
    assert len(artifacts) == 1
    return artifacts[0]


def _venv_python(root: Path) -> Path:
    environment = root / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_purelib(python: Path) -> Path:
    return Path(
        _run([str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"])
        .stdout.strip()
    )


def _prepare_artifact_venv(root: Path) -> tuple[Path, Path]:
    """Create a clean target environment with only host runtime dependencies bridged in.

    The candidate WaveBench distribution itself is never bridged: its package
    must come from the wheel or sdist under test.  Sharing already-installed
    dependencies keeps this a strictly offline package-layout/entry-point
    smoke test instead of an attempt to resolve packages from an index.
    """

    python = _venv_python(root)
    purelib = _venv_purelib(python)
    host_purelib = Path(sysconfig.get_paths()["purelib"])
    (purelib / "wavebench-test-runtime-dependencies.pth").write_text(
        f"{host_purelib}\n",
        encoding="utf-8",
    )
    return python, purelib


def _install_artifact(python: Path, artifact: Path) -> None:
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
            "--no-build-isolation",
            "--disable-pip-version-check",
            str(artifact),
        ]
    )


def _assert_offline_artifact_smoke(python: Path, purelib: Path) -> None:
    origin = Path(
        _run(
            [
                str(python),
                "-I",
                "-c",
                "import pathlib, wavebench; print(pathlib.Path(wavebench.__file__).resolve())",
            ]
        ).stdout.strip()
    )
    assert origin.is_relative_to(purelib)

    for command in (
        ("--help",),
        ("run", "schema"),
        ("run", "template", "--list"),
        ("lock", "status", "TCPIP::192.0.2.10::INSTR"),
    ):
        _run([str(python), "-I", "-m", "wavebench", *command])


def test_sdist_excludes_runtime_data_and_vendor_manuals(tmp_path: Path) -> None:
    artifact = _build_artifact(target="sdist", output_dir=tmp_path)
    with tarfile.open(artifact) as archive:
        members = archive.getnames()
    relative_members = [member.partition("/")[2] for member in members]

    assert any("/docs/project/" in member for member in members)
    assert not any(member.startswith("data/") for member in relative_members)
    assert not any("/docs/instruments/" in member for member in members)


def test_wheel_and_sdist_install_into_clean_offline_runtime(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    wheel = _build_artifact(target="wheel", output_dir=artifacts)
    sdist = _build_artifact(target="sdist", output_dir=artifacts)

    with ZipFile(wheel) as archive:
        members = set(archive.namelist())
    assert "wavebench/__init__.py" in members
    assert "wavebench/cli.py" in members
    assert any(member.endswith(".dist-info/METADATA") for member in members)
    assert any(member.endswith(".dist-info/entry_points.txt") for member in members)

    for label, artifact in (("wheel", wheel), ("sdist", sdist)):
        python, purelib = _prepare_artifact_venv(tmp_path / label)
        _install_artifact(python, artifact)
        _assert_offline_artifact_smoke(python, purelib)
