import subprocess
import sys
import tarfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_sdist_excludes_runtime_data_and_instrument_reference_material(tmp_path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "hatchling",
            "build",
            "-t",
            "sdist",
            "-d",
            str(tmp_path),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    artifacts = list(tmp_path.glob("wavebench-*.tar.gz"))
    assert len(artifacts) == 1
    with tarfile.open(artifacts[0]) as archive:
        members = archive.getnames()
    relative_members = [member.partition("/")[2] for member in members]

    assert any("/doc/project/" in member for member in members)
    assert not any(member.startswith("data/") for member in relative_members)
    assert not any("/doc/instruments/" in member for member in members)
