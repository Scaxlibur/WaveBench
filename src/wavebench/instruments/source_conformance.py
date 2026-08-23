"""Source V2 publication conformance manifests and package binding."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path, PurePosixPath
import re
from typing import Any

from packaging.version import InvalidVersion, Version

from wavebench.errors import ConfigError

from .source_extensions import (
    SOURCE_CONTRACT_VERSION,
    SourceFeature,
    SourceFeatureDirection,
    source_v2_digest,
)


SOURCE_CONFORMANCE_SCHEMA = "wavebench.source.conformance.v1"
SOURCE_CONFORMANCE_SCHEME = "wavebench.source.a0-a5.v1"
SOURCE_CONFORMANCE_WHEEL_BINDING_SCHEME = "wavebench.source.wheel-binding.v1"
SOURCE_CONFORMANCE_DIRECTORY = "wavebench-source-conformance"
MAX_SOURCE_CONFORMANCE_BYTES = 1024 * 1024

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED_FIELDS = frozenset(
    {
        "schema",
        "manifest_id",
        "conformance_scheme",
        "claimed_level",
        "capability",
        "feature",
        "direction",
        "model",
        "firmware_id",
        "option_ids",
        "channels",
        "core_version",
        "plugin_version",
        "wheel_sha256",
        "descriptor_digest",
        "source_contract_version",
        "fixture",
        "safety_limits",
        "budget",
        "results",
        "session_health",
        "final_state",
        "coverage",
        "limitations",
        "evidence_digest",
    }
)


class SourceConformanceLevel(StrEnum):
    A0 = "A0"
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"
    A5 = "A5"


@dataclass(frozen=True, slots=True)
class SourceConformanceManifest:
    manifest_id: str
    claimed_level: SourceConformanceLevel
    capability: str
    feature: SourceFeature
    direction: SourceFeatureDirection
    model: str
    firmware_id: str
    option_ids: tuple[str, ...]
    channels: tuple[int, ...]
    core_version: str
    plugin_version: str
    wheel_sha256: str
    descriptor_digest: str
    fixture: Mapping[str, object]
    safety_limits: Mapping[str, object]
    budget: Mapping[str, object]
    results: Mapping[str, object]
    session_health: Mapping[str, object]
    final_state: Mapping[str, object]
    coverage: tuple[str, ...]
    limitations: tuple[str, ...]
    evidence_digest: str


def source_conformance_canonical_json(value: object) -> str:
    """Serialize JSON data with the Source conformance canonical form."""

    return json.dumps(
        _normalize_json(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def source_conformance_evidence_digest(document: Mapping[str, object]) -> str:
    """Digest a manifest document while omitting only its self-digest field."""

    payload = dict(document)
    payload.pop("evidence_digest", None)
    return "sha256:" + sha256(
        source_conformance_canonical_json(payload).encode("utf-8")
    ).hexdigest()


def source_conformance_wheel_binding_digest(
    members: Iterable[tuple[str, bytes]],
    *,
    dist_info: str,
) -> str:
    """Bind immutable wheel members without the manifest/RECORD self-reference."""

    conformance_prefix = f"{dist_info}/{SOURCE_CONFORMANCE_DIRECTORY}/"
    record_path = f"{dist_info}/RECORD"
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for path, payload in members:
        if path in seen:
            raise ValueError("wheel binding members must have unique paths")
        seen.add(path)
        if path == record_path or path.startswith(conformance_prefix) or path.endswith("/"):
            continue
        rows.append(
            {
                "path": path,
                "sha256": "sha256:" + sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        )
    rows.sort(key=lambda item: str(item["path"]))
    payload = {
        "scheme": SOURCE_CONFORMANCE_WHEEL_BINDING_SCHEME,
        "members": rows,
    }
    return "sha256:" + sha256(
        source_conformance_canonical_json(payload).encode("utf-8")
    ).hexdigest()


def parse_source_conformance_manifest(document: object) -> SourceConformanceManifest:
    """Parse and verify one ``wavebench.source.conformance.v1`` document."""

    if not isinstance(document, dict) or any(not isinstance(key, str) for key in document):
        raise ConfigError("Source conformance manifest must be a JSON object")
    missing = sorted(_REQUIRED_FIELDS - set(document))
    if missing:
        raise ConfigError(
            "Source conformance manifest is missing required fields: " + ", ".join(missing)
        )
    if document["schema"] != SOURCE_CONFORMANCE_SCHEMA:
        raise ConfigError("Source conformance manifest uses an unsupported schema")
    if document["conformance_scheme"] != SOURCE_CONFORMANCE_SCHEME:
        raise ConfigError("Source conformance manifest uses an unsupported level scheme")
    if document["source_contract_version"] != SOURCE_CONTRACT_VERSION:
        raise ConfigError("Source conformance manifest uses an unsupported Source contract")

    manifest_id = _require_token(document["manifest_id"], "manifest_id")
    capability = _require_token(document["capability"], "capability")
    if not capability.startswith("source."):
        raise ConfigError("Source conformance capability must be a Source capability")
    claimed_level = _require_enum(
        SourceConformanceLevel,
        document["claimed_level"],
        "claimed_level",
    )
    feature = _require_enum(SourceFeature, document["feature"], "feature")
    direction = _require_enum(
        SourceFeatureDirection,
        document["direction"],
        "direction",
    )
    model = _require_text(document["model"], "model")
    firmware_id = _require_text(document["firmware_id"], "firmware_id")
    option_ids = _require_token_list(document["option_ids"], "option_ids")
    channels = _require_channel_list(document["channels"])
    core_version = _require_version(document["core_version"], "core_version")
    plugin_version = _require_version(document["plugin_version"], "plugin_version")
    wheel_sha256 = _require_sha256(document["wheel_sha256"], "wheel_sha256")
    descriptor_digest = _require_sha256(
        document["descriptor_digest"],
        "descriptor_digest",
    )
    fixture = _require_summary(document["fixture"], "fixture")
    safety_limits = _require_summary(document["safety_limits"], "safety_limits")
    budget = _require_summary(document["budget"], "budget")
    results = _require_summary(document["results"], "results")
    session_health = _require_summary(document["session_health"], "session_health")
    final_state = _require_summary(document["final_state"], "final_state")
    coverage = _require_text_list(document["coverage"], "coverage")
    limitations = _require_text_list(document["limitations"], "limitations")
    evidence_digest = _require_sha256(document["evidence_digest"], "evidence_digest")
    expected_digest = source_conformance_evidence_digest(document)
    if evidence_digest != expected_digest:
        raise ConfigError("Source conformance evidence_digest does not match the manifest")

    return SourceConformanceManifest(
        manifest_id=manifest_id,
        claimed_level=claimed_level,
        capability=capability,
        feature=feature,
        direction=direction,
        model=model,
        firmware_id=firmware_id,
        option_ids=option_ids,
        channels=channels,
        core_version=core_version,
        plugin_version=plugin_version,
        wheel_sha256=wheel_sha256,
        descriptor_digest=descriptor_digest,
        fixture=fixture,
        safety_limits=safety_limits,
        budget=budget,
        results=results,
        session_health=session_health,
        final_state=final_state,
        coverage=coverage,
        limitations=limitations,
        evidence_digest=evidence_digest,
    )


def validate_source_conformance_distribution(
    descriptor: object,
    distribution: object | None,
) -> tuple[SourceConformanceManifest, ...]:
    """Resolve descriptor evidence only from its installed distribution."""

    extensions = getattr(descriptor, "source_extensions", None)
    if extensions is None:
        return ()
    referenced: list[tuple[object | None, str]] = []
    for feature in extensions.features:
        referenced.extend((feature, ref) for ref in feature.evidence_refs if ref.startswith("dist-info:"))
    for constraint in extensions.safety_profile.constraints:
        referenced.extend(
            (None, ref) for ref in constraint.evidence_refs if ref.startswith("dist-info:")
        )
    if not referenced:
        return ()
    if distribution is None:
        raise ConfigError("Source conformance evidence requires distribution metadata")

    files = tuple(getattr(distribution, "files", ()) or ())
    by_path = {str(item).replace("\\", "/"): item for item in files}
    metadata_paths = sorted(
        path for path in by_path if path.endswith(".dist-info/METADATA")
    )
    if len(metadata_paths) != 1:
        raise ConfigError("Source conformance distribution has no unique dist-info owner")
    dist_info = metadata_paths[0].rsplit("/", 1)[0]
    descriptor_digest = source_v2_digest(extensions)
    manifests: list[SourceConformanceManifest] = []
    parsed_by_ref: dict[str, SourceConformanceManifest] = {}

    for owner, ref in referenced:
        manifest = parsed_by_ref.get(ref)
        if manifest is None:
            relative = _conformance_relative_path(ref)
            member_path = f"{dist_info}/{relative}"
            package_path = by_path.get(member_path)
            if package_path is None:
                raise ConfigError("Source conformance evidence is not owned by this distribution")
            try:
                target = Path(distribution.locate_file(package_path))
                if not target.is_file() or target.stat().st_size > MAX_SOURCE_CONFORMANCE_BYTES:
                    raise ConfigError("Source conformance evidence file is missing or too large")
                document = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ConfigError("Source conformance evidence is not valid UTF-8 JSON") from exc
            manifest = parse_source_conformance_manifest(document)
            if PurePosixPath(relative).name != f"{manifest.manifest_id}.json":
                raise ConfigError("Source conformance filename does not match manifest_id")
            _validate_manifest_descriptor_binding(
                manifest,
                descriptor=descriptor,
                descriptor_digest=descriptor_digest,
            )
            parsed_by_ref[ref] = manifest
            manifests.append(manifest)
        if owner is not None:
            if manifest.feature is not owner.feature:
                raise ConfigError("Source conformance evidence is attached to the wrong feature")
            if manifest.direction not in owner.directions:
                raise ConfigError("Source conformance evidence claims an undeclared direction")
            if not set(manifest.channels) <= set(owner.channels):
                raise ConfigError("Source conformance evidence claims undeclared channels")
            applicability = owner.applicability
            if applicability.models and manifest.model not in applicability.models:
                raise ConfigError("Source conformance evidence is outside model applicability")
            if applicability.firmware_ids and manifest.firmware_id not in applicability.firmware_ids:
                raise ConfigError("Source conformance evidence is outside firmware applicability")
            if applicability.option_ids and not set(applicability.option_ids) <= set(
                manifest.option_ids
            ):
                raise ConfigError("Source conformance evidence is outside option applicability")
    return tuple(manifests)


def _validate_manifest_descriptor_binding(
    manifest: SourceConformanceManifest,
    *,
    descriptor: object,
    descriptor_digest: str,
) -> None:
    if manifest.capability not in getattr(descriptor, "capabilities", ()):
        raise ConfigError("Source conformance evidence claims an undeclared capability")
    if manifest.model not in getattr(descriptor, "models", ()):
        raise ConfigError("Source conformance evidence claims an undeclared model")
    if Version(manifest.plugin_version) != Version(str(getattr(descriptor, "version", ""))):
        raise ConfigError("Source conformance plugin_version does not match the distribution")
    try:
        minimum = Version(str(getattr(descriptor, "wavebench_min_version", "")))
        maximum = Version(str(getattr(descriptor, "wavebench_max_version", "")))
    except InvalidVersion as exc:  # pragma: no cover - descriptor validation runs first
        raise ConfigError("Source descriptor has an invalid core version interval") from exc
    if Version(manifest.core_version) < minimum or Version(manifest.core_version) >= maximum:
        raise ConfigError("Source conformance core_version is outside the descriptor interval")
    if manifest.descriptor_digest != descriptor_digest:
        raise ConfigError("Source conformance descriptor_digest does not match the descriptor")


def _conformance_relative_path(ref: str) -> str:
    relative = ref.removeprefix("dist-info:")
    path = PurePosixPath(relative)
    if (
        not ref.startswith("dist-info:")
        or path.is_absolute()
        or path.parts[:1] != (SOURCE_CONFORMANCE_DIRECTORY,)
        or len(path.parts) != 2
        or path.suffix != ".json"
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ConfigError("Source conformance evidence ref is not a canonical dist-info path")
    return relative


def _normalize_json(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Source conformance JSON cannot contain non-finite floats")
        return value
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("Source conformance JSON object keys must be strings")
        return {key: _normalize_json(value[key]) for key in sorted(value)}
    raise TypeError(f"unsupported Source conformance JSON value: {type(value).__name__}")


def _require_token(value: object, label: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN.fullmatch(value) is None:
        raise ConfigError(f"Source conformance {label} must be a short safe token")
    return value


def _require_text(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > 256
        or any(ord(character) < 32 for character in value)
    ):
        raise ConfigError(f"Source conformance {label} must be non-empty safe text")
    return value


def _require_enum(enum_type: type[StrEnum], value: object, label: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Source conformance {label} is invalid") from exc


def _require_version(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"Source conformance {label} must use PEP 440")
    try:
        return str(Version(value))
    except InvalidVersion as exc:
        raise ConfigError(f"Source conformance {label} must use PEP 440") from exc


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ConfigError(f"Source conformance {label} must be sha256:<64 lowercase hex>")
    return value


def _require_token_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"Source conformance {label} must be a JSON array")
    result = tuple(_require_token(item, label) for item in value)
    if len(set(result)) != len(result) or tuple(sorted(result)) != result:
        raise ConfigError(f"Source conformance {label} must be sorted and unique")
    return result


def _require_channel_list(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in value
    ):
        raise ConfigError("Source conformance channels must contain positive integers")
    result = tuple(value)
    if len(set(result)) != len(result) or tuple(sorted(result)) != result:
        raise ConfigError("Source conformance channels must be sorted and unique")
    return result


def _require_summary(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not value or any(not isinstance(key, str) for key in value):
        raise ConfigError(f"Source conformance {label} must be a non-empty JSON object")
    normalized = _normalize_json(value)
    assert isinstance(normalized, dict)
    return normalized


def _require_text_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"Source conformance {label} must be a non-empty JSON array")
    result = tuple(_require_text(item, label) for item in value)
    if len(set(result)) != len(result) or tuple(sorted(result)) != result:
        raise ConfigError(f"Source conformance {label} must be sorted and unique")
    return result


__all__ = [
    "MAX_SOURCE_CONFORMANCE_BYTES",
    "SOURCE_CONFORMANCE_DIRECTORY",
    "SOURCE_CONFORMANCE_SCHEMA",
    "SOURCE_CONFORMANCE_SCHEME",
    "SOURCE_CONFORMANCE_WHEEL_BINDING_SCHEME",
    "SourceConformanceLevel",
    "SourceConformanceManifest",
    "parse_source_conformance_manifest",
    "source_conformance_canonical_json",
    "source_conformance_evidence_digest",
    "source_conformance_wheel_binding_digest",
    "validate_source_conformance_distribution",
]
