from __future__ import annotations

from dataclasses import replace
import json
from pathlib import PurePosixPath

import pytest

from tests.source_v2_fixtures import source_descriptor, source_extensions
from wavebench.errors import ConfigError
from wavebench.instruments.source_conformance import (
    SOURCE_CONFORMANCE_DIRECTORY,
    SOURCE_CONFORMANCE_SCHEMA,
    SOURCE_CONFORMANCE_SCHEME,
    SourceConformanceLevel,
    parse_source_conformance_manifest,
    source_conformance_evidence_digest,
    source_conformance_wheel_binding_digest,
    validate_source_conformance_distribution,
)
from wavebench.instruments.source_extensions import SOURCE_CONTRACT_VERSION, source_v2_digest


def _manifest_document(
    *,
    descriptor_digest: str = "sha256:" + "1" * 64,
    manifest_id: str = "example-basic-read-a1",
) -> dict[str, object]:
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
        "plugin_version": "0.8.0",
        "wheel_sha256": "sha256:" + "2" * 64,
        "descriptor_digest": descriptor_digest,
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
    return document


def test_manifest_parser_verifies_required_identity_and_digest() -> None:
    document = _manifest_document()

    manifest = parse_source_conformance_manifest(document)

    assert manifest.claimed_level is SourceConformanceLevel.A1
    assert manifest.capability == "source.snapshot_v2"
    assert manifest.coverage == ("basic read contract",)

    document["results"] = {"queries": 4}
    with pytest.raises(ConfigError, match="evidence_digest"):
        parse_source_conformance_manifest(document)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("conformance_scheme", "A1", "level scheme"),
        ("limitations", [], "limitations"),
        ("channels", [2, 1], "channels"),
        ("wheel_sha256", "2" * 64, "wheel_sha256"),
    ],
)
def test_manifest_parser_rejects_ambiguous_or_incomplete_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    document = _manifest_document()
    document[field] = value
    document["evidence_digest"] = source_conformance_evidence_digest(document)

    with pytest.raises(ConfigError, match=message):
        parse_source_conformance_manifest(document)


def test_wheel_binding_omits_only_conformance_and_record() -> None:
    dist_info = "example-0.8.0.dist-info"
    baseline = (
        ("example/__init__.py", b"plugin"),
        (f"{dist_info}/METADATA", b"metadata"),
        (f"{dist_info}/RECORD", b"first record"),
        (
            f"{dist_info}/{SOURCE_CONFORMANCE_DIRECTORY}/first.json",
            b"first manifest",
        ),
    )
    changed_self_reference = (
        baseline[0],
        baseline[1],
        (f"{dist_info}/RECORD", b"second record"),
        (
            f"{dist_info}/{SOURCE_CONFORMANCE_DIRECTORY}/second.json",
            b"second manifest",
        ),
    )

    first = source_conformance_wheel_binding_digest(baseline, dist_info=dist_info)
    second = source_conformance_wheel_binding_digest(
        reversed(changed_self_reference),
        dist_info=dist_info,
    )
    changed_code = source_conformance_wheel_binding_digest(
        (("example/__init__.py", b"changed"), *baseline[1:]),
        dist_info=dist_info,
    )

    assert first == second
    assert first != changed_code


class _Distribution:
    def __init__(self, root, files: tuple[PurePosixPath, ...]) -> None:
        self.root = root
        self.files = files

    def locate_file(self, path: PurePosixPath):
        return self.root / path


def test_descriptor_evidence_resolves_only_from_own_distribution(tmp_path) -> None:
    evidence_ref = f"dist-info:{SOURCE_CONFORMANCE_DIRECTORY}/example-basic-read-a1.json"
    extensions = source_extensions()
    basic = replace(extensions.features[0], evidence_refs=(evidence_ref,))
    extensions = replace(extensions, features=(basic, extensions.features[1]))
    descriptor = source_descriptor(extensions=extensions)
    dist_info = "wavebench_example-0.8.0.dist-info"
    manifest_path = PurePosixPath(
        dist_info,
        SOURCE_CONFORMANCE_DIRECTORY,
        "example-basic-read-a1.json",
    )
    metadata_path = PurePosixPath(dist_info, "METADATA")
    document = _manifest_document(descriptor_digest=source_v2_digest(extensions))
    target = tmp_path / manifest_path
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(document), encoding="utf-8")
    (tmp_path / metadata_path).write_text("Name: wavebench-example\n", encoding="utf-8")
    distribution = _Distribution(tmp_path, (metadata_path, manifest_path))

    manifests = validate_source_conformance_distribution(descriptor, distribution)

    assert tuple(item.manifest_id for item in manifests) == ("example-basic-read-a1",)

    foreign = _Distribution(tmp_path, (metadata_path,))
    with pytest.raises(ConfigError, match="not owned"):
        validate_source_conformance_distribution(descriptor, foreign)


def test_descriptor_evidence_rejects_descriptor_and_feature_mismatch(tmp_path) -> None:
    evidence_ref = f"dist-info:{SOURCE_CONFORMANCE_DIRECTORY}/example-basic-read-a1.json"
    extensions = source_extensions()
    output = replace(extensions.features[1], evidence_refs=(evidence_ref,))
    extensions = replace(extensions, features=(extensions.features[0], output))
    descriptor = source_descriptor(extensions=extensions)
    dist_info = "wavebench_example-0.8.0.dist-info"
    manifest_path = PurePosixPath(
        dist_info,
        SOURCE_CONFORMANCE_DIRECTORY,
        "example-basic-read-a1.json",
    )
    metadata_path = PurePosixPath(dist_info, "METADATA")
    document = _manifest_document(descriptor_digest=source_v2_digest(extensions))
    target = tmp_path / manifest_path
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(document), encoding="utf-8")
    (tmp_path / metadata_path).write_text("Name: wavebench-example\n", encoding="utf-8")
    distribution = _Distribution(tmp_path, (metadata_path, manifest_path))

    with pytest.raises(ConfigError, match="wrong feature"):
        validate_source_conformance_distribution(descriptor, distribution)
