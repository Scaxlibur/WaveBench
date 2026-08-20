"""Experimental scope-extension contracts from the Draft R1.3 RFC.

This module is deliberately not re-exported by :mod:`wavebench.instruments`.
The models are available to core conformance tests, but the corresponding
capabilities remain undiscoverable until the RFC acceptance gates are met.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import re
from typing import Literal, Protocol, runtime_checkable
import zlib

import numpy as np

from wavebench.transport.contracts import BinaryResponseFraming

from .contracts import InstrumentDriver


SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES = 262_144
SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES = 262_144
SCOPE_SCREENSHOT_BINARY_QUERY_MAX_COUNT = 1
SCOPE_SCREENSHOT_BINARY_RESYNCHRONIZATION_MAX_BYTES = 0
SCOPE_TRACE_BINARY_RESPONSE_MAX_BYTES = 8_388_608
SCOPE_TRACE_BINARY_OPERATION_MAX_BYTES = 67_108_864
SCOPE_TRACE_BINARY_QUERY_MAX_COUNT = 256
SCOPE_TRACE_BINARY_RESYNCHRONIZATION_MAX_BYTES = 65_536
SCOPE_PROFILE_OPERATION_TIMEOUT_MS = 5_000
SCOPE_SCREENSHOT_OPERATION_TIMEOUT_MS = 5_000
SCOPE_ACQUISITION_OPERATION_TIMEOUT_MS = 30_000
SCOPE_TRACE_OPERATION_TIMEOUT_MS = 60_000
SCOPE_TRACE_MAX_POINTS = 8_388_608


_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _strict_int(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ValueError(f"{label} must be >= {minimum}")
        raise ValueError(f"{label} must be in {minimum}..{maximum}")
    return value


def _finite(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _literal(value: object, allowed: set[str], *, label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"unsupported {label}: {value!r}")
    return value


def _unique_tuple(values: object, *, label: str) -> tuple[object, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{label} must be a tuple")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must not contain duplicates")
    return values


def _safe_token(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a short ASCII safe token")
    return value


def _optional_safe_token(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    return _safe_token(value, label=label)


def _hex_bytes(value: object, *, label: str) -> bytes:
    if not isinstance(value, str) or value != value.lower() or len(value) % 2:
        raise ValueError(f"{label} must be lowercase even-length hexadecimal")
    if len(value) > 32 or re.fullmatch(r"[0-9a-f]*", value) is None:
        raise ValueError(f"{label} must encode at most 16 bytes")
    return bytes.fromhex(value)


def _validate_prefix_and_subsequence(
    *,
    expected: tuple[str, ...],
    attempted: tuple[str, ...],
    completed: tuple[str, ...],
    status: str,
) -> None:
    _unique_tuple(attempted, label="attempted_fields")
    _unique_tuple(completed, label="restored_fields")
    if expected[: len(attempted)] != attempted:
        raise ValueError("attempted_fields must be a restore-order prefix")
    completed_iter = iter(attempted)
    if any(field_name not in completed_iter for field_name in completed):
        raise ValueError("restored_fields must be an ordered attempted-field subsequence")
    if status == "completed" and (attempted != expected or completed != expected):
        raise ValueError("completed restore results must cover every restore field")
    if status == "not_attempted" and (attempted or completed):
        raise ValueError("not-attempted restore results cannot contain fields")


ScreenshotMenuMode = Literal["device", "include", "exclude"]
ScreenshotColorMode = Literal["device", "color", "monochrome", "inverted"]
ScopeScreenshotStateField = Literal["scope.display_menu", "scope.display_color"]
_SCREENSHOT_FIELDS = {"scope.display_menu", "scope.display_color"}


@dataclass(frozen=True, slots=True)
class ScopeScreenshotRequest:
    format: Literal["png"] = "png"
    menu_mode: ScreenshotMenuMode = "device"
    color_mode: ScreenshotColorMode = "device"

    def __post_init__(self) -> None:
        _literal(self.format, {"png"}, label="screenshot format")
        _literal(self.menu_mode, {"device", "include", "exclude"}, label="menu mode")
        _literal(
            self.color_mode,
            {"device", "color", "monochrome", "inverted"},
            label="color mode",
        )


@dataclass(frozen=True, slots=True)
class ScopeScreenshotVariant:
    request: ScopeScreenshotRequest
    media_type: Literal["image/png"]
    framing: BinaryResponseFraming
    response_max_bytes: int
    operation_max_bytes: int
    resynchronization_max_bytes: int
    changed_fields: tuple[ScopeScreenshotStateField, ...]
    restore_order: tuple[ScopeScreenshotStateField, ...]
    snapshot_max_steps: int
    restore_max_steps: int
    verify_max_steps: int
    query_max_count: Literal[1] = 1
    transport_trailing_hex: str = ""
    content_trailing_hex: str = ""
    width_px: tuple[int, int] | None = None
    height_px: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, ScopeScreenshotRequest):
            raise TypeError("screenshot variant request has an invalid type")
        _literal(self.media_type, {"image/png"}, label="screenshot media type")
        object.__setattr__(self, "framing", BinaryResponseFraming(self.framing))
        _strict_int(
            self.response_max_bytes,
            label="response_max_bytes",
            minimum=1,
            maximum=SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES,
        )
        _strict_int(
            self.operation_max_bytes,
            label="operation_max_bytes",
            minimum=1,
            maximum=SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES,
        )
        if self.operation_max_bytes != self.response_max_bytes:
            raise ValueError("screenshot operation and response limits must be equal")
        _strict_int(
            self.resynchronization_max_bytes,
            label="resynchronization_max_bytes",
            minimum=0,
            maximum=SCOPE_SCREENSHOT_BINARY_RESYNCHRONIZATION_MAX_BYTES,
        )
        if self.query_max_count != 1 or isinstance(self.query_max_count, bool):
            raise ValueError("screenshot query_max_count must be exactly 1")
        changed = _unique_tuple(self.changed_fields, label="changed_fields")
        restore = _unique_tuple(self.restore_order, label="restore_order")
        if not set(changed) <= _SCREENSHOT_FIELDS or set(restore) != set(changed):
            raise ValueError("screenshot restore_order must cover exactly the changed fields")
        for field_name in changed:
            if not isinstance(field_name, str):
                raise TypeError("screenshot fields must be strings")
        steps = (self.snapshot_max_steps, self.restore_max_steps, self.verify_max_steps)
        if changed:
            for label, value in zip(
                ("snapshot_max_steps", "restore_max_steps", "verify_max_steps"),
                steps,
                strict=True,
            ):
                _strict_int(value, label=label, minimum=1, maximum=32)
        elif steps != (0, 0, 0):
            raise ValueError("stateless screenshot variants must use zero recovery steps")
        transport_trailing = _hex_bytes(
            self.transport_trailing_hex,
            label="transport_trailing_hex",
        )
        _hex_bytes(self.content_trailing_hex, label="content_trailing_hex")
        if self.framing is BinaryResponseFraming.MESSAGE and transport_trailing:
            raise ValueError("message-framed screenshots cannot declare transport trailing bytes")
        for label, bounds in (("width_px", self.width_px), ("height_px", self.height_px)):
            if bounds is None:
                continue
            if not isinstance(bounds, tuple) or len(bounds) != 2:
                raise ValueError(f"{label} must contain minimum and maximum")
            lower = _strict_int(bounds[0], label=f"{label}.minimum", minimum=1)
            upper = _strict_int(bounds[1], label=f"{label}.maximum", minimum=1)
            if lower > upper:
                raise ValueError(f"{label} minimum cannot exceed maximum")


@dataclass(frozen=True, slots=True)
class ScopeScreenshotProfile:
    variants: tuple[ScopeScreenshotVariant, ...]
    source: Literal["descriptor", "queried", "combined"] = "descriptor"

    def __post_init__(self) -> None:
        if not isinstance(self.variants, tuple) or not self.variants:
            raise ValueError("screenshot profile variants must be a non-empty tuple")
        if any(not isinstance(item, ScopeScreenshotVariant) for item in self.variants):
            raise TypeError("screenshot profile variants have an invalid type")
        requests = tuple(item.request for item in self.variants)
        if len(set(requests)) != len(requests):
            raise ValueError("screenshot profile requests must be unique")
        _literal(self.source, {"descriptor", "queried", "combined"}, label="profile source")

    def require_public_source(self) -> None:
        if self.source == "queried":
            raise ValueError("queried-only screenshot profiles cannot be public")

    def select(self, request: ScopeScreenshotRequest) -> ScopeScreenshotVariant:
        matches = [variant for variant in self.variants if variant.request == request]
        if len(matches) != 1:
            raise ValueError("screenshot request does not match exactly one profile variant")
        return matches[0]


@dataclass(frozen=True, slots=True)
class ScopeScreenshotStateSnapshot:
    captured_fields: tuple[ScopeScreenshotStateField, ...]
    menu_state_token: str | None = None
    color_state_token: str | None = None

    def __post_init__(self) -> None:
        fields = _unique_tuple(self.captured_fields, label="captured_fields")
        if not set(fields) <= _SCREENSHOT_FIELDS:
            raise ValueError("screenshot snapshot contains an unsupported field")
        menu = _optional_safe_token(self.menu_state_token, label="menu_state_token")
        color = _optional_safe_token(self.color_state_token, label="color_state_token")
        if ("scope.display_menu" in fields) != (menu is not None):
            raise ValueError("menu token presence must match captured fields")
        if ("scope.display_color" in fields) != (color is not None):
            raise ValueError("color token presence must match captured fields")


@dataclass(frozen=True, slots=True)
class ScopeScreenshotBaseline:
    context_id: str
    session_epoch: str
    baseline_nonce: str
    snapshot: ScopeScreenshotStateSnapshot
    restore_order: tuple[ScopeScreenshotStateField, ...]

    def __post_init__(self) -> None:
        _safe_token(self.context_id, label="context_id")
        _safe_token(self.session_epoch, label="session_epoch")
        _safe_token(self.baseline_nonce, label="baseline_nonce")
        if not isinstance(self.snapshot, ScopeScreenshotStateSnapshot):
            raise TypeError("screenshot baseline snapshot has an invalid type")
        order = _unique_tuple(self.restore_order, label="restore_order")
        if set(order) != set(self.snapshot.captured_fields):
            raise ValueError("screenshot baseline order must cover captured fields exactly")


@dataclass(frozen=True, slots=True)
class ScopeScreenshotRestoreResult:
    status: Literal["completed", "failed", "not_attempted"]
    attempted_fields: tuple[ScopeScreenshotStateField, ...]
    restored_fields: tuple[ScopeScreenshotStateField, ...]
    error_code: str | None = None

    def __post_init__(self) -> None:
        _literal(self.status, {"completed", "failed", "not_attempted"}, label="restore status")
        attempted = _unique_tuple(self.attempted_fields, label="attempted_fields")
        restored = _unique_tuple(self.restored_fields, label="restored_fields")
        if not set(attempted + restored) <= _SCREENSHOT_FIELDS:
            raise ValueError("screenshot restore fields are invalid")
        _optional_safe_token(self.error_code, label="error_code")

    def validate_for(self, baseline: ScopeScreenshotBaseline) -> None:
        _literal(self.status, {"completed", "failed", "not_attempted"}, label="restore status")
        _optional_safe_token(self.error_code, label="error_code")
        _validate_prefix_and_subsequence(
            expected=baseline.restore_order,
            attempted=self.attempted_fields,
            completed=self.restored_fields,
            status=self.status,
        )


@dataclass(frozen=True, slots=True)
class ScopeScreenshotVerification:
    status: Literal["verified", "mismatch", "unavailable"]
    verified_fields: tuple[ScopeScreenshotStateField, ...]
    mismatched_fields: tuple[ScopeScreenshotStateField, ...]
    error_code: str | None = None

    def __post_init__(self) -> None:
        _literal(self.status, {"verified", "mismatch", "unavailable"}, label="verification status")
        verified = _unique_tuple(self.verified_fields, label="verified_fields")
        mismatched = _unique_tuple(self.mismatched_fields, label="mismatched_fields")
        if set(verified) & set(mismatched) or not set(verified + mismatched) <= _SCREENSHOT_FIELDS:
            raise ValueError("screenshot verification fields are inconsistent")
        _optional_safe_token(self.error_code, label="error_code")
        if self.status == "verified" and mismatched:
            raise ValueError("verified screenshot state cannot contain mismatches")
        if self.status == "mismatch" and not mismatched:
            raise ValueError("mismatched screenshot verification requires mismatched fields")
        if self.status == "unavailable" and (verified or mismatched):
            raise ValueError("unavailable screenshot verification cannot claim fields")


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if (
        len(data) < 57
        or len(data) > SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES
        or not data.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise ValueError("screenshot data is not a PNG")
    offset = 8
    width: int | None = None
    height: int | None = None
    saw_idat = False
    chunk_index = 0
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("screenshot PNG contains a truncated chunk")
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("screenshot PNG chunk length exceeds the payload")
        if len(chunk_type) != 4 or any(
            not (65 <= byte <= 90 or 97 <= byte <= 122) for byte in chunk_type
        ):
            raise ValueError("screenshot PNG contains an invalid chunk type")
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = int.from_bytes(data[offset + 8 + length : end], "big")
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise ValueError("screenshot PNG chunk CRC is invalid")
        if chunk_index == 0:
            if chunk_type != b"IHDR" or length != 13:
                raise ValueError("screenshot PNG must begin with a 13-byte IHDR")
            width = int.from_bytes(chunk_data[0:4], "big")
            height = int.from_bytes(chunk_data[4:8], "big")
            if width < 1 or height < 1:
                raise ValueError("screenshot PNG dimensions must be positive")
        elif chunk_type == b"IHDR":
            raise ValueError("screenshot PNG contains more than one IHDR")
        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            if length != 0 or end != len(data) or not saw_idat:
                raise ValueError("screenshot PNG has an invalid IEND boundary")
            assert width is not None and height is not None
            return width, height
        offset = end
        chunk_index += 1
    raise ValueError("screenshot PNG is missing IEND")


@dataclass(frozen=True, slots=True)
class ScopeScreenshot:
    data: bytes
    media_type: Literal["image/png"]
    width_px: int
    height_px: int
    requested: ScopeScreenshotRequest
    effective: ScopeScreenshotRequest
    framing: BinaryResponseFraming

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise TypeError("screenshot data must be bytes")
        _literal(self.media_type, {"image/png"}, label="screenshot media type")
        width = _strict_int(self.width_px, label="width_px", minimum=1)
        height = _strict_int(self.height_px, label="height_px", minimum=1)
        if not isinstance(self.requested, ScopeScreenshotRequest) or not isinstance(
            self.effective, ScopeScreenshotRequest
        ):
            raise TypeError("screenshot requests have an invalid type")
        object.__setattr__(self, "framing", BinaryResponseFraming(self.framing))
        if _png_dimensions(self.data) != (width, height):
            raise ValueError("screenshot dimensions do not match the PNG IHDR")


@dataclass(frozen=True, slots=True)
class ScopeEmbeddedScreenshotContract:
    request: ScopeScreenshotRequest
    changed_fields: tuple[ScopeScreenshotStateField, ...]
    verification_fields: tuple[ScopeScreenshotStateField, ...]
    cleanup_verification_fields: tuple[ScopeScreenshotStateField, ...]
    output_fields: tuple[Literal["output.screenshot"], ...] = ("output.screenshot",)
    failure_policy: Literal["fail_parent"] = "fail_parent"
    artifact_key: Literal["screenshot"] = "screenshot"

    def __post_init__(self) -> None:
        if not isinstance(self.request, ScopeScreenshotRequest):
            raise TypeError("embedded screenshot request has an invalid type")
        changed = _unique_tuple(self.changed_fields, label="changed_fields")
        verified = _unique_tuple(self.verification_fields, label="verification_fields")
        cleanup = _unique_tuple(
            self.cleanup_verification_fields,
            label="cleanup_verification_fields",
        )
        if not set(changed) <= _SCREENSHOT_FIELDS:
            raise ValueError("embedded screenshot changed fields are invalid")
        if set(verified) != set(changed) or set(cleanup) != set(changed):
            raise ValueError("embedded screenshot recovery fields must cover changed state")
        if self.output_fields != ("output.screenshot",):
            raise ValueError("embedded screenshot output field is fixed")
        _literal(self.failure_policy, {"fail_parent"}, label="screenshot failure policy")
        _literal(self.artifact_key, {"screenshot"}, label="screenshot artifact key")


ScopeAcquisitionPhase = Literal[
    "unknown",
    "stopped",
    "ready",
    "arming",
    "waiting",
    "acquiring",
    "rolling",
    "stopping",
    "complete",
    "error",
]
ScopeTriggerMode = Literal["auto", "normal", "single", "roll", "unknown"]
ScopeContinuousTriggerMode = Literal["auto", "normal", "roll"]
ScopeSingleBaselineStage = Literal["configured_pre_arm", "original_atomic_arm"]
ScopeSingleArmSemantics = Literal["configure_then_arm", "atomic_configure_and_arm"]
ScopeAcquisitionIdentitySemantics = Literal["unique_within_session_epoch", "unknown"]
ScopeAcquisitionSettingField = Literal["scope.trigger", "scope.acquisition"]
ScopeAcquisitionRestoreField = Literal[
    "scope.run_state",
    "scope.trigger",
    "scope.acquisition",
]
ScopeCompletionProof = Literal["count_delta_with_epoch", "identity_delta", "state_transition"]
_ACQUISITION_SETTING_FIELDS = {"scope.trigger", "scope.acquisition"}
_ACQUISITION_RESTORE_FIELDS = {"scope.run_state", *_ACQUISITION_SETTING_FIELDS}
_ACQUISITION_PHASES = {
    "unknown",
    "stopped",
    "ready",
    "arming",
    "waiting",
    "acquiring",
    "rolling",
    "stopping",
    "complete",
    "error",
}


@dataclass(frozen=True, slots=True)
class ScopeAcquisitionControlProfile:
    supported_continuous_modes: tuple[ScopeContinuousTriggerMode, ...]
    single_arm_semantics: ScopeSingleArmSemantics
    arm_resets_acquisition_count: bool
    failure_restore_order: tuple[ScopeAcquisitionSettingField, ...]
    snapshot_max_steps: int
    restore_max_steps: int
    verify_max_steps: int
    identity_semantics: ScopeAcquisitionIdentitySemantics
    atomic_arm_preserves_count_mode_semantics: bool = False

    def __post_init__(self) -> None:
        modes = _unique_tuple(
            self.supported_continuous_modes,
            label="supported_continuous_modes",
        )
        if not modes or not set(modes) <= {"auto", "normal", "roll"}:
            raise ValueError("supported continuous modes are invalid")
        _literal(
            self.single_arm_semantics,
            {"configure_then_arm", "atomic_configure_and_arm"},
            label="single arm semantics",
        )
        if not isinstance(self.arm_resets_acquisition_count, bool) or not isinstance(
            self.atomic_arm_preserves_count_mode_semantics, bool
        ):
            raise TypeError("acquisition profile flags must be bool")
        restore = _unique_tuple(self.failure_restore_order, label="failure_restore_order")
        if set(restore) != _ACQUISITION_SETTING_FIELDS:
            raise ValueError("failure_restore_order must contain trigger and acquisition once")
        _strict_int(self.snapshot_max_steps, label="snapshot_max_steps", minimum=3, maximum=64)
        _strict_int(self.restore_max_steps, label="restore_max_steps", minimum=3, maximum=64)
        _strict_int(self.verify_max_steps, label="verify_max_steps", minimum=3, maximum=64)
        _literal(
            self.identity_semantics,
            {"unique_within_session_epoch", "unknown"},
            label="identity semantics",
        )
        if self.single_arm_semantics == "configure_then_arm" and (
            self.atomic_arm_preserves_count_mode_semantics
        ):
            raise ValueError("configure-then-arm cannot claim atomic-arm count semantics")
        if self.single_arm_semantics == "atomic_configure_and_arm" and (
            self.atomic_arm_preserves_count_mode_semantics
            and self.arm_resets_acquisition_count
        ):
            raise ValueError("an arm that resets count cannot preserve count semantics")


@dataclass(frozen=True, slots=True)
class ScopeAcquisitionRunState:
    phase: ScopeAcquisitionPhase
    trigger_mode: ScopeTriggerMode
    raw_state: str
    acquisition_count: int | None = None
    counter_epoch: str | None = None
    acquisition_identity: str | None = None

    def __post_init__(self) -> None:
        _literal(self.phase, _ACQUISITION_PHASES, label="acquisition phase")
        _literal(
            self.trigger_mode,
            {"auto", "normal", "single", "roll", "unknown"},
            label="trigger mode",
        )
        if (
            not isinstance(self.raw_state, str)
            or not self.raw_state
            or len(self.raw_state) > 128
            or not self.raw_state.isprintable()
            or "\n" in self.raw_state
            or "\r" in self.raw_state
        ):
            raise ValueError("raw acquisition state must be a short printable token")
        if self.acquisition_count is not None:
            _strict_int(self.acquisition_count, label="acquisition_count", minimum=0)
        _optional_safe_token(self.counter_epoch, label="counter_epoch")
        _optional_safe_token(self.acquisition_identity, label="acquisition_identity")


@dataclass(frozen=True, slots=True)
class ScopeAcquisitionControlSnapshot:
    run_state: ScopeAcquisitionRunState
    trigger_state_token: str
    acquisition_state_token: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_state, ScopeAcquisitionRunState):
            raise TypeError("acquisition snapshot run state has an invalid type")
        _safe_token(self.trigger_state_token, label="trigger_state_token")
        _safe_token(self.acquisition_state_token, label="acquisition_state_token")


@dataclass(frozen=True, slots=True)
class ScopeAcquisitionControlBaseline:
    context_id: str
    session_epoch: str
    baseline_nonce: str
    snapshot: ScopeAcquisitionControlSnapshot
    restore_order: tuple[ScopeAcquisitionRestoreField, ...]

    def __post_init__(self) -> None:
        _safe_token(self.context_id, label="context_id")
        _safe_token(self.session_epoch, label="session_epoch")
        _safe_token(self.baseline_nonce, label="baseline_nonce")
        if not isinstance(self.snapshot, ScopeAcquisitionControlSnapshot):
            raise TypeError("acquisition baseline snapshot has an invalid type")
        order = _unique_tuple(self.restore_order, label="restore_order")
        if not order or order[0] != "scope.run_state" or set(order) != _ACQUISITION_RESTORE_FIELDS:
            raise ValueError("acquisition restore order must start with run state and cover all fields")


@dataclass(frozen=True, slots=True)
class ScopeBaselineRestoreResult:
    status: Literal["completed", "failed", "not_attempted"]
    attempted_fields: tuple[ScopeAcquisitionRestoreField, ...]
    restored_fields: tuple[ScopeAcquisitionRestoreField, ...]
    error_code: str | None = None

    def __post_init__(self) -> None:
        _literal(self.status, {"completed", "failed", "not_attempted"}, label="restore status")
        attempted = _unique_tuple(self.attempted_fields, label="attempted_fields")
        restored = _unique_tuple(self.restored_fields, label="restored_fields")
        if not set(attempted + restored) <= _ACQUISITION_RESTORE_FIELDS:
            raise ValueError("acquisition restore fields are invalid")
        _optional_safe_token(self.error_code, label="error_code")

    def validate_for(self, baseline: ScopeAcquisitionControlBaseline) -> None:
        _literal(self.status, {"completed", "failed", "not_attempted"}, label="restore status")
        _optional_safe_token(self.error_code, label="error_code")
        _validate_prefix_and_subsequence(
            expected=baseline.restore_order,
            attempted=self.attempted_fields,
            completed=self.restored_fields,
            status=self.status,
        )


@dataclass(frozen=True, slots=True)
class ScopeBaselineVerification:
    status: Literal["verified", "mismatch", "unavailable"]
    verified_fields: tuple[ScopeAcquisitionRestoreField, ...]
    mismatched_fields: tuple[ScopeAcquisitionRestoreField, ...]
    error_code: str | None = None

    def __post_init__(self) -> None:
        _literal(self.status, {"verified", "mismatch", "unavailable"}, label="verification status")
        verified = _unique_tuple(self.verified_fields, label="verified_fields")
        mismatched = _unique_tuple(self.mismatched_fields, label="mismatched_fields")
        if set(verified) & set(mismatched) or not set(verified + mismatched) <= _ACQUISITION_RESTORE_FIELDS:
            raise ValueError("acquisition verification fields are inconsistent")
        _optional_safe_token(self.error_code, label="error_code")
        if self.status == "verified" and mismatched:
            raise ValueError("verified acquisition state cannot contain mismatches")
        if self.status == "mismatch" and not mismatched:
            raise ValueError("mismatched acquisition verification requires mismatched fields")
        if self.status == "unavailable" and (verified or mismatched):
            raise ValueError("unavailable acquisition verification cannot claim fields")


@dataclass(frozen=True, slots=True)
class ScopeContinuousAcquisitionRequest:
    trigger_mode: ScopeContinuousTriggerMode

    def __post_init__(self) -> None:
        _literal(self.trigger_mode, {"auto", "normal", "roll"}, label="continuous trigger mode")


@dataclass(frozen=True, slots=True)
class ScopeAcquisitionCompletion:
    state: ScopeAcquisitionRunState
    original_state: ScopeAcquisitionRunState
    proof_baseline_state: ScopeAcquisitionRunState
    proof_baseline_stage: ScopeSingleBaselineStage
    proof: ScopeCompletionProof
    baseline_count: int | None = None
    completed_count: int | None = None
    baseline_identity: str | None = None
    completed_identity: str | None = None
    observed_states: tuple[ScopeAcquisitionRunState, ...] = ()

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, ScopeAcquisitionRunState)
            for value in (self.state, self.original_state, self.proof_baseline_state)
        ):
            raise TypeError("acquisition completion states have an invalid type")
        _literal(
            self.proof_baseline_stage,
            {"configured_pre_arm", "original_atomic_arm"},
            label="proof baseline stage",
        )
        _literal(
            self.proof,
            {"count_delta_with_epoch", "identity_delta", "state_transition"},
            label="completion proof",
        )
        if not isinstance(self.observed_states, tuple) or not self.observed_states:
            raise ValueError("completion proof must retain observed states")
        if any(not isinstance(item, ScopeAcquisitionRunState) for item in self.observed_states):
            raise TypeError("observed acquisition states have an invalid type")
        if self.observed_states[-1] != self.state or self.state.phase not in {"complete", "stopped"}:
            raise ValueError("completion proof must end in the reported complete/stopped state")
        for label, value in (
            ("baseline_count", self.baseline_count),
            ("completed_count", self.completed_count),
        ):
            if value is not None:
                _strict_int(value, label=label, minimum=0)
        _optional_safe_token(self.baseline_identity, label="baseline_identity")
        _optional_safe_token(self.completed_identity, label="completed_identity")
        if self.baseline_count is not None and (
            self.baseline_count != self.proof_baseline_state.acquisition_count
        ):
            raise ValueError("baseline_count must match proof_baseline_state")
        if self.completed_count is not None and self.completed_count != self.state.acquisition_count:
            raise ValueError("completed_count must match final state")
        if self.baseline_identity is not None and (
            self.baseline_identity != self.proof_baseline_state.acquisition_identity
        ):
            raise ValueError("baseline_identity must match proof_baseline_state")
        if self.completed_identity is not None and (
            self.completed_identity != self.state.acquisition_identity
        ):
            raise ValueError("completed_identity must match final state")


def validate_acquisition_completion(
    completion: ScopeAcquisitionCompletion,
    *,
    baseline: ScopeAcquisitionControlBaseline,
    profile: ScopeAcquisitionControlProfile,
) -> None:
    """Validate proof semantics that depend on the core-owned profile/baseline."""

    if completion.original_state != baseline.snapshot.run_state:
        raise ValueError("completion original state does not match the core baseline")
    if profile.single_arm_semantics == "configure_then_arm":
        expected_stage = "configured_pre_arm"
    else:
        expected_stage = "original_atomic_arm"
    if completion.proof_baseline_stage != expected_stage:
        raise ValueError("completion proof stage does not match the acquisition profile")
    if expected_stage == "original_atomic_arm" and (
        completion.proof_baseline_state != completion.original_state
    ):
        raise ValueError("atomic-arm proof baseline must equal the original state")
    phases = tuple(item.phase for item in completion.observed_states)
    transition_seen = any(
        item.phase in {"arming", "waiting", "acquiring"}
        or (
            item.phase == "ready"
            and item.trigger_mode == "single"
            and item != completion.proof_baseline_state
        )
        for item in completion.observed_states[:-1]
    )
    if completion.proof == "identity_delta":
        if profile.identity_semantics != "unique_within_session_epoch":
            raise ValueError("identity proof requires unique-within-epoch semantics")
        if (
            completion.baseline_identity is None
            or completion.completed_identity is None
            or completion.baseline_identity == completion.completed_identity
        ):
            raise ValueError("identity proof requires different non-empty identities")
    elif completion.proof == "count_delta_with_epoch":
        if profile.arm_resets_acquisition_count:
            raise ValueError("count proof is invalid when arm resets acquisition count")
        if (
            completion.baseline_count is None
            or completion.completed_count is None
            or completion.completed_count <= completion.baseline_count
            or completion.proof_baseline_state.counter_epoch is None
            or completion.state.counter_epoch != completion.proof_baseline_state.counter_epoch
            or not transition_seen
        ):
            raise ValueError("count proof requires an increasing count, stable epoch and transition")
        if expected_stage == "original_atomic_arm" and (
            completion.original_state.trigger_mode != "single"
            or not profile.atomic_arm_preserves_count_mode_semantics
        ):
            raise ValueError("atomic-arm count proof lacks preserved single-mode semantics")
    elif not transition_seen:
        raise ValueError("state-transition proof does not contain a valid new acquisition transition")
    if phases[-1] not in {"complete", "stopped"}:
        raise ValueError("completion proof has an invalid terminal phase")


ScopeTraceKind = Literal["analog", "digital", "math", "reference", "spectrum"]
ScopeAxisKind = Literal["time", "frequency", "index", "unknown"]
ScopeAxisUnit = Literal["s", "Hz", "1", "unknown"]
ScopeTraceUnit = Literal["v", "mv", "db", "dbm", "1", "unknown"]
ScopeTraceMagnitudeSemantics = Literal["absolute", "relative", "linear", "unknown"]
ScopeTraceOperation = Literal[
    "identity",
    "reference_copy",
    "fft_magnitude",
    "fft_phase",
    "device_other",
    "unknown",
]
ScopeTraceTransferField = Literal[
    "scope.run_state",
    "scope.waveform_source",
    "scope.waveform_mode",
    "scope.query_response_header",
    "scope.waveform_format",
    "scope.waveform_byte_order",
    "scope.waveform_points",
    "scope.waveform_transfer_window",
]
_TRACE_KINDS = {"analog", "digital", "math", "reference", "spectrum"}
_TRACE_FETCHABLE_KINDS = {"analog", "digital", "reference"}
_TRACE_TRANSFER_FIELDS = {
    "scope.run_state",
    "scope.waveform_source",
    "scope.waveform_mode",
    "scope.query_response_header",
    "scope.waveform_format",
    "scope.waveform_byte_order",
    "scope.waveform_points",
    "scope.waveform_transfer_window",
}
_TRACE_TOKEN_ATTRS = {
    "scope.run_state": "run_state_token",
    "scope.waveform_source": "waveform_source_token",
    "scope.waveform_mode": "waveform_mode_token",
    "scope.query_response_header": "query_response_header_token",
    "scope.waveform_format": "waveform_format_token",
    "scope.waveform_byte_order": "waveform_byte_order_token",
    "scope.waveform_points": "waveform_points_token",
    "scope.waveform_transfer_window": "waveform_transfer_window_token",
}


@dataclass(frozen=True, slots=True)
class ScopeTraceRef:
    kind: ScopeTraceKind
    index: int | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        _literal(self.kind, _TRACE_KINDS, label="trace kind")
        if (self.index is None) == (self.name is None):
            raise ValueError("trace reference must provide exactly one of index or name")
        if self.index is not None:
            if self.kind == "digital":
                _strict_int(self.index, label="digital trace index", minimum=0, maximum=15)
            else:
                _strict_int(self.index, label="trace index", minimum=1, maximum=65_535)
        if self.name is not None and (
            not isinstance(self.name, str)
            or not 1 <= len(self.name) <= 64
            or self.name.strip() != self.name
            or not self.name.isprintable()
        ):
            raise ValueError("trace name must be 1..64 printable trimmed code points")


@dataclass(frozen=True, slots=True)
class ScopeAxisMetadata:
    kind: ScopeAxisKind
    unit: ScopeAxisUnit
    start: float | None
    increment: float | None
    points: int

    def __post_init__(self) -> None:
        _literal(self.kind, {"time", "frequency", "index", "unknown"}, label="axis kind")
        _literal(self.unit, {"s", "Hz", "1", "unknown"}, label="axis unit")
        points = _strict_int(self.points, label="axis points", minimum=1, maximum=SCOPE_TRACE_MAX_POINTS)
        if self.kind == "unknown":
            if self.unit != "unknown" or self.start is not None or self.increment is not None:
                raise ValueError("unknown axes cannot claim units or scaling")
            return
        expected_unit = {"time": "s", "frequency": "Hz", "index": "1"}[self.kind]
        if self.unit != expected_unit:
            raise ValueError("axis kind and unit are inconsistent")
        start = _finite(self.start, label="axis start")
        increment = _finite(self.increment, label="axis increment")
        if increment <= 0:
            raise ValueError("axis increment must be positive")
        if not isfinite(start + increment * (points - 1)):
            raise ValueError("axis final coordinate must be finite")
        if self.kind == "frequency" and start < 0:
            raise ValueError("frequency axes cannot start below zero")


@dataclass(frozen=True, slots=True)
class ScopeTraceMetadata:
    source: ScopeTraceRef
    x_axis: ScopeAxisMetadata
    y_unit: ScopeTraceUnit
    y_semantics: ScopeTraceMagnitudeSemantics
    value_encoding: Literal["real", "digital_bitmask"]
    y_increment: float | None = None
    y_origin: float | None = None
    y_resolution_bits: int | None = None
    operation: ScopeTraceOperation = "unknown"
    inputs: tuple[ScopeTraceRef, ...] = ()
    digital_channels: tuple[int, ...] = ()
    fetchable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source, ScopeTraceRef) or not isinstance(
            self.x_axis, ScopeAxisMetadata
        ):
            raise TypeError("trace metadata source or axis has an invalid type")
        _literal(self.y_unit, {"v", "mv", "db", "dbm", "1", "unknown"}, label="trace unit")
        _literal(
            self.y_semantics,
            {"absolute", "relative", "linear", "unknown"},
            label="trace magnitude semantics",
        )
        _literal(self.value_encoding, {"real", "digital_bitmask"}, label="value encoding")
        _literal(
            self.operation,
            {"identity", "reference_copy", "fft_magnitude", "fft_phase", "device_other", "unknown"},
            label="trace operation",
        )
        if not isinstance(self.fetchable, bool):
            raise TypeError("trace fetchable must be bool")
        inputs = _unique_tuple(self.inputs, label="trace inputs")
        if any(not isinstance(item, ScopeTraceRef) for item in inputs):
            raise TypeError("trace inputs have an invalid type")
        channels = _unique_tuple(self.digital_channels, label="digital_channels")
        for channel in channels:
            _strict_int(channel, label="digital channel", minimum=0, maximum=15)
        scaling_values = (self.y_increment, self.y_origin)
        if (self.y_increment is None) != (self.y_origin is None):
            raise ValueError("trace y increment and origin must be present together")
        if self.y_increment is not None:
            if _finite(self.y_increment, label="y_increment") == 0:
                raise ValueError("trace y increment cannot be zero")
            _finite(self.y_origin, label="y_origin")
        if self.y_resolution_bits is not None:
            _strict_int(self.y_resolution_bits, label="y_resolution_bits", minimum=1, maximum=64)
        if self.value_encoding == "digital_bitmask":
            if (
                self.source.kind != "digital"
                or self.y_unit != "1"
                or self.y_semantics != "unknown"
                or scaling_values != (None, None)
                or self.y_resolution_bits is not None
                or self.source.index is None
                or channels != (self.source.index,)
            ):
                raise ValueError("digital trace metadata is inconsistent")
        elif channels:
            raise ValueError("real trace metadata cannot contain digital channels")
        if self.source.kind == "digital" and self.value_encoding != "digital_bitmask":
            raise ValueError("digital traces require bitmask encoding")
        if self.source.kind != "digital" and self.value_encoding != "real":
            raise ValueError("non-digital traces require real encoding")
        expected_semantics = {
            "dbm": "absolute",
            "db": "relative",
            "v": "linear",
            "mv": "linear",
            "1": "unknown",
            "unknown": "unknown",
        }[self.y_unit]
        if self.y_semantics != expected_semantics:
            raise ValueError("trace unit and magnitude semantics are inconsistent")
        if self.source.kind in {"analog", "digital"} and (
            self.operation != "identity" or inputs
        ):
            raise ValueError("analog and digital traces must use identity without inputs")
        if self.source.kind == "reference":
            if self.operation == "identity" and inputs:
                raise ValueError("identity reference traces cannot contain inputs")
            if self.operation == "reference_copy" and len(inputs) != 1:
                raise ValueError("reference_copy requires exactly one input")
            if self.operation not in {"identity", "reference_copy"}:
                raise ValueError("reference trace operation is unsupported")
        if self.operation in {"device_other", "unknown"} and inputs:
            raise ValueError("unknown/device operations cannot claim inputs")
        if self.fetchable:
            if self.source.kind not in _TRACE_FETCHABLE_KINDS or self.x_axis.kind != "time":
                raise ValueError("R1.3 fetchable traces are analog/digital/reference time traces")
            if self.source.kind == "digital":
                if self.value_encoding != "digital_bitmask":
                    raise ValueError("fetchable digital traces require bitmask encoding")
            elif (
                self.value_encoding != "real"
                or self.y_unit not in {"v", "mv"}
                or self.y_semantics != "linear"
                or self.operation not in {"identity", "reference_copy"}
            ):
                raise ValueError("fetchable real trace metadata is outside R1.3")


@dataclass(frozen=True, slots=True)
class ScopeTraceData:
    metadata: ScopeTraceMetadata
    values: np.ndarray

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, ScopeTraceMetadata) or not self.metadata.fetchable:
            raise ValueError("trace data requires fetchable metadata")
        values = np.asarray(self.values)
        if values.ndim != 1 or values.size < 1 or values.size != self.metadata.x_axis.points:
            raise ValueError("trace values must be a non-empty 1-D array matching axis points")
        if self.metadata.value_encoding == "digital_bitmask":
            if values.dtype.kind != "u" or values.dtype.itemsize > 2:
                raise ValueError("digital trace values must use an unsigned <=16-bit dtype")
            channel = self.metadata.digital_channels[0]
            allowed = np.logical_or(values == 0, values == (1 << channel))
            if not bool(np.all(allowed)):
                raise ValueError("digital trace contains values outside its single-bit encoding")
            copied = np.array(values, dtype=np.uint16, copy=True)
        else:
            if values.dtype.kind == "c":
                raise ValueError("complex trace values are not supported")
            try:
                copied = np.array(values, dtype=np.float64, copy=True)
            except (TypeError, ValueError) as exc:
                raise ValueError("real trace values must be numeric") from exc
            if not bool(np.all(np.isfinite(copied))):
                raise ValueError("real trace values must be finite")
        copied.setflags(write=False)
        object.__setattr__(self, "values", copied)


@dataclass(frozen=True, slots=True)
class ScopeTraceTransferStateSnapshot:
    captured_fields: tuple[ScopeTraceTransferField, ...]
    run_state_token: str | None = None
    waveform_source_token: str | None = None
    waveform_mode_token: str | None = None
    query_response_header_token: str | None = None
    waveform_format_token: str | None = None
    waveform_byte_order_token: str | None = None
    waveform_points_token: str | None = None
    waveform_transfer_window_token: str | None = None

    def __post_init__(self) -> None:
        fields = _unique_tuple(self.captured_fields, label="captured_fields")
        if not set(fields) <= _TRACE_TRANSFER_FIELDS:
            raise ValueError("trace transfer snapshot contains unsupported fields")
        for field_name, attr_name in _TRACE_TOKEN_ATTRS.items():
            token = _optional_safe_token(getattr(self, attr_name), label=attr_name)
            if (field_name in fields) != (token is not None):
                raise ValueError(f"{attr_name} presence must match captured fields")


@dataclass(frozen=True, slots=True)
class ScopeTraceTransferBaseline:
    context_id: str
    session_epoch: str
    baseline_nonce: str
    snapshot: ScopeTraceTransferStateSnapshot
    restore_order: tuple[ScopeTraceTransferField, ...]

    def __post_init__(self) -> None:
        _safe_token(self.context_id, label="context_id")
        _safe_token(self.session_epoch, label="session_epoch")
        _safe_token(self.baseline_nonce, label="baseline_nonce")
        if not isinstance(self.snapshot, ScopeTraceTransferStateSnapshot):
            raise TypeError("trace transfer baseline snapshot has an invalid type")
        order = _unique_tuple(self.restore_order, label="restore_order")
        if set(order) != set(self.snapshot.captured_fields):
            raise ValueError("trace transfer restore order must cover captured fields exactly")


@dataclass(frozen=True, slots=True)
class ScopeTraceTransferRestoreResult:
    status: Literal["completed", "failed", "not_attempted"]
    attempted_fields: tuple[ScopeTraceTransferField, ...]
    restored_fields: tuple[ScopeTraceTransferField, ...]
    error_code: str | None = None

    def __post_init__(self) -> None:
        _literal(self.status, {"completed", "failed", "not_attempted"}, label="restore status")
        attempted = _unique_tuple(self.attempted_fields, label="attempted_fields")
        restored = _unique_tuple(self.restored_fields, label="restored_fields")
        if not set(attempted + restored) <= _TRACE_TRANSFER_FIELDS:
            raise ValueError("trace transfer restore fields are invalid")
        _optional_safe_token(self.error_code, label="error_code")

    def validate_for(self, baseline: ScopeTraceTransferBaseline) -> None:
        _literal(self.status, {"completed", "failed", "not_attempted"}, label="restore status")
        _optional_safe_token(self.error_code, label="error_code")
        _validate_prefix_and_subsequence(
            expected=baseline.restore_order,
            attempted=self.attempted_fields,
            completed=self.restored_fields,
            status=self.status,
        )


@dataclass(frozen=True, slots=True)
class ScopeTraceTransferVerification:
    status: Literal["verified", "mismatch", "unavailable"]
    verified_fields: tuple[ScopeTraceTransferField, ...]
    mismatched_fields: tuple[ScopeTraceTransferField, ...]
    error_code: str | None = None

    def __post_init__(self) -> None:
        _literal(self.status, {"verified", "mismatch", "unavailable"}, label="verification status")
        verified = _unique_tuple(self.verified_fields, label="verified_fields")
        mismatched = _unique_tuple(self.mismatched_fields, label="mismatched_fields")
        if set(verified) & set(mismatched) or not set(verified + mismatched) <= _TRACE_TRANSFER_FIELDS:
            raise ValueError("trace transfer verification fields are inconsistent")
        _optional_safe_token(self.error_code, label="error_code")
        if self.status == "verified" and mismatched:
            raise ValueError("verified transfer state cannot contain mismatches")
        if self.status == "mismatch" and not mismatched:
            raise ValueError("mismatched transfer verification requires mismatched fields")
        if self.status == "unavailable" and (verified or mismatched):
            raise ValueError("unavailable transfer verification cannot claim fields")


@dataclass(frozen=True, slots=True)
class ScopeTraceProfile:
    fetchable_kinds: tuple[Literal["analog", "digital", "reference"], ...]
    max_points: int
    restore_order: tuple[ScopeTraceTransferField, ...]
    snapshot_max_steps: int
    restore_max_steps: int
    verify_max_steps: int
    source_index_max: int = 65_535

    def __post_init__(self) -> None:
        kinds = _unique_tuple(self.fetchable_kinds, label="fetchable_kinds")
        if not kinds or not set(kinds) <= _TRACE_FETCHABLE_KINDS:
            raise ValueError("trace profile fetchable kinds are outside R1.3")
        _strict_int(
            self.max_points,
            label="max_points",
            minimum=1,
            maximum=SCOPE_TRACE_MAX_POINTS,
        )
        restore = _unique_tuple(self.restore_order, label="restore_order")
        if not restore or not set(restore) <= _TRACE_TRANSFER_FIELDS:
            raise ValueError("trace profile restore order must contain transfer fields")
        for label, value in (
            ("snapshot_max_steps", self.snapshot_max_steps),
            ("restore_max_steps", self.restore_max_steps),
            ("verify_max_steps", self.verify_max_steps),
        ):
            _strict_int(value, label=label, minimum=len(restore), maximum=64)
        _strict_int(
            self.source_index_max,
            label="source_index_max",
            minimum=1,
            maximum=65_535,
        )


ErrorCheckPolicy = Literal["required", "if_supported", "disabled"]
ErrorCheckTiming = Literal["before", "after", "before_and_after"]
InstrumentErrorPolicy = Literal["fail", "record_and_continue"]


@dataclass(frozen=True, slots=True)
class ErrorCheckSpec:
    policy: ErrorCheckPolicy
    timing: ErrorCheckTiming = "before_and_after"
    max_records: int = 16
    on_instrument_error: InstrumentErrorPolicy = "fail"

    def __post_init__(self) -> None:
        _literal(self.policy, {"required", "if_supported", "disabled"}, label="error policy")
        _literal(self.timing, {"before", "after", "before_and_after"}, label="error timing")
        _strict_int(self.max_records, label="max_records", minimum=1, maximum=256)
        _literal(
            self.on_instrument_error,
            {"fail", "record_and_continue"},
            label="instrument error policy",
        )


@dataclass(frozen=True, slots=True)
class DriverErrorRecord:
    code: str | int | None
    message: str
    severity: Literal["info", "warning", "error", "fatal", "unknown"]
    source: str

    def __post_init__(self) -> None:
        if isinstance(self.code, bool) or not isinstance(self.code, (str, int, type(None))):
            raise ValueError("driver error code has an invalid type")
        if isinstance(self.code, str):
            _safe_token(self.code, label="driver error code")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("driver error message must be non-empty")
        _literal(
            self.severity,
            {"info", "warning", "error", "fatal", "unknown"},
            label="driver error severity",
        )
        _safe_token(self.source, label="driver error source")


@dataclass(frozen=True, slots=True)
class ErrorDrainResult:
    records: tuple[DriverErrorRecord, ...]
    terminated: bool
    query_count: int
    overflow_record: DriverErrorRecord | None = None

    def validate_for(self, *, max_records: int) -> None:
        limit = _strict_int(max_records, label="max_records", minimum=1, maximum=256)
        if not isinstance(self.records, tuple) or any(
            not isinstance(item, DriverErrorRecord) for item in self.records
        ):
            raise TypeError("error drain records have an invalid type")
        if not isinstance(self.terminated, bool):
            raise TypeError("error drain terminated must be bool")
        _strict_int(self.query_count, label="query_count", minimum=1, maximum=limit + 1)
        if self.terminated:
            if self.overflow_record is not None or self.query_count != len(self.records) + 1:
                raise ValueError("terminated error drain has inconsistent evidence")
        elif (
            len(self.records) != limit
            or self.query_count != limit + 1
            or not isinstance(self.overflow_record, DriverErrorRecord)
        ):
            raise ValueError("unterminated error drain must retain its overflow record")


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    code: str | int | None
    message: str
    message_redacted: bool
    severity: Literal["info", "warning", "error", "fatal", "unknown"]
    source: str
    observed_at_utc: str
    correlation_id: str | None

    def __post_init__(self) -> None:
        if isinstance(self.code, bool) or not isinstance(self.code, (str, int, type(None))):
            raise ValueError("error code has an invalid type")
        if isinstance(self.code, str):
            _safe_token(self.code, label="error code")
        if (
            not isinstance(self.message, str)
            or not 1 <= len(self.message) <= 512
            or not self.message.isprintable()
        ):
            raise ValueError("error message must be 1..512 printable code points")
        if not isinstance(self.message_redacted, bool):
            raise TypeError("message_redacted must be bool")
        _literal(
            self.severity,
            {"info", "warning", "error", "fatal", "unknown"},
            label="error severity",
        )
        _safe_token(self.source, label="error source")
        if not isinstance(self.observed_at_utc, str) or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z",
            self.observed_at_utc,
        ) is None:
            raise ValueError("observed_at_utc must be an RFC 3339 UTC timestamp")
        if self.correlation_id is not None:
            _safe_token(self.correlation_id, label="correlation_id")


@dataclass(frozen=True, slots=True)
class ScopeDescriptorExtensions:
    screenshot_profile: ScopeScreenshotProfile | None = None
    acquisition_control_profile: ScopeAcquisitionControlProfile | None = None
    trace_profile: ScopeTraceProfile | None = None

    def __post_init__(self) -> None:
        for label, value, expected in (
            ("screenshot_profile", self.screenshot_profile, ScopeScreenshotProfile),
            (
                "acquisition_control_profile",
                self.acquisition_control_profile,
                ScopeAcquisitionControlProfile,
            ),
            ("trace_profile", self.trace_profile, ScopeTraceProfile),
        ):
            if value is not None and not isinstance(value, expected):
                raise TypeError(f"{label} has an invalid type")
        if self.screenshot_profile is not None:
            self.screenshot_profile.require_public_source()


@runtime_checkable
class ScopeScreenshotProfileDriver(InstrumentDriver, Protocol):
    def get_screenshot_profile(self) -> ScopeScreenshotProfile: ...


@runtime_checkable
class ScopeScreenshotDriver(InstrumentDriver, Protocol):
    def snapshot_screenshot_state(
        self,
        fields: tuple[ScopeScreenshotStateField, ...],
    ) -> ScopeScreenshotStateSnapshot: ...

    def capture_screenshot(
        self,
        request: ScopeScreenshotRequest,
        *,
        baseline: ScopeScreenshotBaseline | None,
    ) -> ScopeScreenshot: ...

    def restore_screenshot_state(
        self,
        baseline: ScopeScreenshotBaseline,
    ) -> ScopeScreenshotRestoreResult: ...

    def verify_screenshot_state_restored(
        self,
        fields: tuple[ScopeScreenshotStateField, ...],
        baseline: ScopeScreenshotBaseline,
    ) -> ScopeScreenshotStateSnapshot: ...


@runtime_checkable
class ScopeAcquisitionRunStateDriver(InstrumentDriver, Protocol):
    def get_acquisition_run_state(self) -> ScopeAcquisitionRunState: ...


@runtime_checkable
class ScopeAcquisitionControlRecoveryDriver(InstrumentDriver, Protocol):
    def snapshot_acquisition_control(self) -> ScopeAcquisitionControlSnapshot: ...

    def restore_acquisition_control(
        self,
        baseline: ScopeAcquisitionControlBaseline,
    ) -> ScopeBaselineRestoreResult: ...

    def verify_acquisition_control_restored(
        self,
        baseline: ScopeAcquisitionControlBaseline,
    ) -> ScopeAcquisitionControlSnapshot: ...


@runtime_checkable
class ScopeAcquisitionControlDriver(
    ScopeAcquisitionRunStateDriver,
    ScopeAcquisitionControlRecoveryDriver,
    Protocol,
):
    def start_continuous(
        self,
        *,
        trigger_mode: ScopeContinuousTriggerMode,
        baseline: ScopeAcquisitionControlBaseline,
    ) -> ScopeAcquisitionRunState: ...

    def stop_acquisition(self) -> ScopeAcquisitionRunState: ...

    def acquire_single(
        self,
        *,
        baseline: ScopeAcquisitionControlBaseline,
        deadline: float,
    ) -> ScopeAcquisitionCompletion: ...


@runtime_checkable
class ScopeTraceTransferRecoveryDriver(InstrumentDriver, Protocol):
    def snapshot_trace_transfer_state(
        self,
        fields: tuple[ScopeTraceTransferField, ...],
    ) -> ScopeTraceTransferStateSnapshot: ...

    def restore_trace_transfer_state(
        self,
        baseline: ScopeTraceTransferBaseline,
    ) -> ScopeTraceTransferRestoreResult: ...

    def verify_trace_transfer_state_restored(
        self,
        baseline: ScopeTraceTransferBaseline,
    ) -> ScopeTraceTransferStateSnapshot: ...


@runtime_checkable
class ScopeTraceMetadataDriver(InstrumentDriver, Protocol):
    def get_trace_metadata(self, source: ScopeTraceRef) -> ScopeTraceMetadata: ...


@runtime_checkable
class ScopeTraceDriver(
    ScopeTraceMetadataDriver,
    ScopeTraceTransferRecoveryDriver,
    Protocol,
):
    def fetch_trace(
        self,
        source: ScopeTraceRef,
        *,
        points: str | int = "dmax",
        baseline: ScopeTraceTransferBaseline | None,
    ) -> ScopeTraceData: ...


@runtime_checkable
class ScopeErrorDrainDriver(InstrumentDriver, Protocol):
    def drain_errors(self, *, max_records: int) -> ErrorDrainResult: ...


__all__ = [name for name in globals() if name.startswith("Scope") or name.startswith("Error")]
