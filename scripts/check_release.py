#!/usr/bin/env python3
"""Check release identity, notes and distribution metadata before publication."""

import argparse
from email.parser import BytesParser
from pathlib import Path
import tarfile
import tomllib
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


def check_release(root: Path, artifacts: Path, tag: str | None = None) -> str:
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"tag {tag!r} does not match project version {version}")
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    locked = [p["version"] for p in lock["package"] if p["name"] == "wavebench"]
    if locked != [version]:
        raise ValueError(f"uv.lock WaveBench version differs: {locked}")
    if tag is not None:
        notes = root / "docs" / "releases" / f"v{version}.md"
        if not notes.read_text(encoding="utf-8").startswith(f"# WaveBench v{version} 发布说明\n"):
            raise ValueError("release notes heading does not match project version")
        if f"docs/releases/v{version}.md" not in (root / "CHANGELOG.md").read_text(encoding="utf-8"):
            raise ValueError("CHANGELOG must link to the release notes")
    wheels = list(artifacts.glob("*.whl"))
    sdists = list(artifacts.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("expected exactly one wheel and one sdist")
    with ZipFile(wheels[0]) as archive:
        names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise ValueError("wheel must contain exactly one METADATA")
        wheel_metadata = archive.read(names[0])
    with tarfile.open(sdists[0]) as archive:
        names = [n for n in archive.getnames() if n.count("/") == 1 and n.endswith("/PKG-INFO")]
        if len(names) != 1:
            raise ValueError("sdist must contain exactly one root PKG-INFO")
        member = archive.extractfile(names[0])
        if member is None:
            raise ValueError("sdist PKG-INFO must be a file")
        sdist_metadata = member.read()
    for payload in (wheel_metadata, sdist_metadata):
        metadata = BytesParser().parsebytes(payload)
        if metadata["Name"] != "wavebench" or metadata["Version"] != version:
            raise ValueError(f"distribution identity differs: {metadata['Name']} {metadata['Version']}")
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--tag")
    args = parser.parse_args()
    print(f"release identity verified: {check_release(ROOT, args.artifacts, args.tag)}")
