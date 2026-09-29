"""advisor 外部状态外发的同意门（Core 拥有）。

契约见 `docs/project/rfcs/WaveBench_advisor插件RFC.md`：

- 默认关闭，由 `[advisor] enabled` 与调用方的显式 opt-in 共同开启；
- 发送前必须能产出完整预览（payload、字节数、sha256、逐条 untrusted 来源），预览不联网；
- 同意绑定 endpoint 集合与允许字段集，任一变化即失效；
- 非交互场景一律拒绝；
- API key 永不进入 artifact、日志或错误信息。

本模块只用标准库，且不含任何网络代码：拒绝路径下调用方没有任何可外发的通道。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Collection, Mapping, Sequence

ADVISOR_CONSENT_SCHEMA = "wavebench.advisor_consent.v1"

STATUS_GRANTED = "granted"
STATUS_REFUSED = "refused"
STATUS_PREVIEW_ONLY = "preview_only"

REASON_DISABLED = "advisor_disabled"
REASON_NOT_GRANTED = "external_state_consent_not_granted"
REASON_PREVIEW_ONLY = "preview_only"
REASON_ENDPOINT_NOT_REGISTERED = "endpoint_not_registered"
REASON_FIELD_NOT_REGISTERED = "state_field_not_registered"
REASON_ENDPOINT_CHANGED = "consent_invalidated_by_endpoint_change"
REASON_FIELD_CHANGED = "consent_invalidated_by_allowed_field_change"
REASON_RUN_REQUIRED = "run_scoped_consent_requires_run_target"
REASON_RUN_CHANGED = "consent_invalidated_by_run_change"

VALID_SCOPES = ("invocation", "run")


def normalize_names(values: Sequence[str], *, label: str) -> tuple[str, ...]:
    """校验并规格化名字集合（去重、排序），保持产物可比对。"""
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be a sequence of names, not a string")
    for value in values:
        if not isinstance(value, str) or not value or value.strip() != value:
            raise ValueError(f"{label} must contain non-empty, trimmed names")
    return tuple(sorted(set(values)))


@dataclass(frozen=True)
class PreviewPayload:
    """即将离开本机的完整请求体；给操作员看，不联网。"""

    payload: Mapping[str, Any]
    payload_sha256: str
    payload_bytes: int
    untrusted_sources: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "payload": dict(self.payload),
            "payload_sha256": self.payload_sha256,
            "payload_bytes": self.payload_bytes,
            "untrusted_sources": list(self.untrusted_sources),
        }


def build_preview(
    *,
    model: str,
    state: Mapping[str, Any],
    questions: Mapping[str, Any],
) -> PreviewPayload:
    """构造预览：内容、字节数与 sha256 都由本函数确定，调用方不能跳过。"""
    body = {"model": model, "state": dict(state), "questions": dict(questions)}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    sources: list[str] = []
    for span in state.get("untrusted", ()) or ():
        if isinstance(span, Mapping):
            source = str(span.get("source", ""))
            if source:
                sources.append(source)
    return PreviewPayload(
        payload=body,
        payload_sha256=hashlib.sha256(encoded).hexdigest(),
        payload_bytes=len(encoded),
        untrusted_sources=tuple(sorted(set(sources))),
    )


def unregistered_state_fields(fields: Collection[str], allowed: Collection[str]) -> tuple[str, ...]:
    """字段级白名单检查：出现未注册字段就拒绝，避免顺手把新字段送出机器。"""
    return tuple(sorted(set(fields) - set(allowed)))


@dataclass(frozen=True)
class ConsentDecision:
    """操作员对「把这份 state 发到外部服务」的显式决定。

    默认按次确认；`valid_for="run"` 时同意只对同一 `run_id` 有效。
    """

    granted: bool
    valid_for: str = "invocation"
    run_id: str | None = None
    endpoint_hosts: tuple[str, ...] = ()
    allowed_state_fields: tuple[str, ...] = ()
    granted_by: str | None = None
    granted_at: str | None = None

    def __post_init__(self) -> None:
        if self.valid_for not in VALID_SCOPES:
            raise ValueError(
                f"consent valid_for must be one of {', '.join(VALID_SCOPES)}, got {self.valid_for!r}"
            )
        if self.granted and self.valid_for == "run" and not self.run_id:
            raise ValueError("run-scoped consent requires run_id")
        object.__setattr__(
            self, "endpoint_hosts", normalize_names(self.endpoint_hosts, label="endpoint_hosts")
        )
        object.__setattr__(
            self,
            "allowed_state_fields",
            normalize_names(self.allowed_state_fields, label="allowed_state_fields"),
        )

    def as_payload(self, preview: PreviewPayload) -> dict[str, Any]:
        return {
            "granted": self.granted,
            "valid_for": self.valid_for,
            "run_id": self.run_id,
            "granted_by": self.granted_by,
            "granted_at": self.granted_at,
            "endpoint_hosts": list(self.endpoint_hosts),
            "allowed_state_fields": list(self.allowed_state_fields),
            "payload_sha256": preview.payload_sha256,
            "payload_bytes": preview.payload_bytes,
            "accepted_data_leaves_machine": self.granted,
        }


@dataclass(frozen=True)
class ConsentOutcome:
    """同意门的结论；`status != "granted"` 时调用方必须不发起外发。"""

    status: str
    reason: str | None
    preview: PreviewPayload
    decision: ConsentDecision | None = None
    endpoint_hosts: tuple[str, ...] = ()
    allowed_state_fields: tuple[str, ...] = ()

    @property
    def granted(self) -> bool:
        return self.status == STATUS_GRANTED

    def as_payload(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema": ADVISOR_CONSENT_SCHEMA,
            "status": self.status,
            "reason": self.reason,
            "endpoint_hosts": list(self.endpoint_hosts),
            "allowed_state_fields": list(self.allowed_state_fields),
            "payload_sha256": self.preview.payload_sha256,
            "payload_bytes": self.preview.payload_bytes,
            "accepted_data_leaves_machine": self.granted,
        }
        if self.decision is not None:
            record.update(
                {
                    "granted": self.decision.granted,
                    "valid_for": self.decision.valid_for,
                    "run_id": self.decision.run_id,
                    "granted_by": self.decision.granted_by,
                    "granted_at": self.decision.granted_at,
                }
            )
        else:
            record.update({"granted": False, "valid_for": None, "run_id": None,
                           "granted_by": None, "granted_at": None})
        return record


def consent_covers(
    consent: ConsentDecision,
    *,
    endpoint_hosts: Sequence[str],
    allowed_state_fields: Sequence[str],
    run_id: str | None,
) -> tuple[bool, str | None]:
    """判断已有同意是否覆盖本次调用；返回 (是否覆盖, 失效原因)。"""
    if not consent.granted:
        return False, REASON_NOT_GRANTED
    if set(consent.endpoint_hosts) != set(endpoint_hosts):
        return False, REASON_ENDPOINT_CHANGED
    if set(consent.allowed_state_fields) != set(allowed_state_fields):
        return False, REASON_FIELD_CHANGED
    if consent.valid_for == "invocation":
        return True, None
    if run_id is None:
        return False, REASON_RUN_REQUIRED
    if consent.run_id != run_id:
        return False, REASON_RUN_CHANGED
    return True, None


def resolve_consent(
    *,
    preview: PreviewPayload,
    endpoint_hosts: Sequence[str],
    state_fields: Collection[str],
    enabled: bool,
    registered_endpoint_hosts: Sequence[str] = (),
    registered_state_fields: Collection[str] = (),
    run_id: str | None = None,
    preview_only: bool = False,
    accepted: bool = False,
    valid_for: str = "invocation",
    granted_by: str | None = None,
    granted_at: str | None = None,
) -> ConsentOutcome:
    """Core 侧的同意门。

    拒绝顺序（对应 RFC 验收门里"外部调用次数必须为 0"的五种情况）：未开启 → 未注册字段 →
    未注册 endpoint → 仅预览 → 未确认。"仅预览"与"拒绝"都不返回可用同意，调用方不得外发。
    """
    hosts = normalize_names(endpoint_hosts, label="endpoint_hosts")
    fields = normalize_names(tuple(state_fields), label="state_fields")

    def refused(reason: str) -> ConsentOutcome:
        return ConsentOutcome(
            status=STATUS_REFUSED,
            reason=reason,
            preview=preview,
            endpoint_hosts=hosts,
            allowed_state_fields=fields,
        )

    if not enabled:
        return refused(REASON_DISABLED)

    unregistered = unregistered_state_fields(fields, registered_state_fields)
    if registered_state_fields and unregistered:
        return refused(f"{REASON_FIELD_NOT_REGISTERED}: {', '.join(unregistered)}")

    if registered_endpoint_hosts:
        allowed_hosts = set(normalize_names(registered_endpoint_hosts, label="registered_endpoint_hosts"))
        unknown_hosts = tuple(sorted(set(hosts) - allowed_hosts))
        if unknown_hosts:
            return refused(f"{REASON_ENDPOINT_NOT_REGISTERED}: {', '.join(unknown_hosts)}")

    if preview_only:
        return ConsentOutcome(
            status=STATUS_PREVIEW_ONLY,
            reason=REASON_PREVIEW_ONLY,
            preview=preview,
            endpoint_hosts=hosts,
            allowed_state_fields=fields,
        )

    if not accepted:
        return refused(REASON_NOT_GRANTED)

    if valid_for not in VALID_SCOPES:
        return refused(REASON_NOT_GRANTED)
    if valid_for == "run" and not run_id:
        return refused(REASON_RUN_REQUIRED)

    decision = ConsentDecision(
        granted=True,
        valid_for=valid_for,
        run_id=run_id if valid_for == "run" else None,
        endpoint_hosts=hosts,
        allowed_state_fields=fields,
        granted_by=granted_by,
        granted_at=granted_at,
    )
    return ConsentOutcome(
        status=STATUS_GRANTED,
        reason=None,
        preview=preview,
        decision=decision,
        endpoint_hosts=hosts,
        allowed_state_fields=fields,
    )


__all__ = [
    "ADVISOR_CONSENT_SCHEMA",
    "REASON_DISABLED",
    "REASON_ENDPOINT_CHANGED",
    "REASON_ENDPOINT_NOT_REGISTERED",
    "REASON_FIELD_CHANGED",
    "REASON_FIELD_NOT_REGISTERED",
    "REASON_NOT_GRANTED",
    "REASON_PREVIEW_ONLY",
    "REASON_RUN_CHANGED",
    "REASON_RUN_REQUIRED",
    "STATUS_GRANTED",
    "STATUS_PREVIEW_ONLY",
    "STATUS_REFUSED",
    "VALID_SCOPES",
    "ConsentDecision",
    "ConsentOutcome",
    "PreviewPayload",
    "build_preview",
    "consent_covers",
    "normalize_names",
    "resolve_consent",
    "unregistered_state_fields",
]
