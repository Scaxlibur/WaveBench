"""WaveBench × Jev 第 0 批 seam 草案：纯函数边界 + 可注入 client。

约束（对应 README.md v2）：
- 只用标准库；**本模块内没有任何网络代码**。
- build_state / build_questions / preview_request / apply_* / record 全为确定性纯函数。
- 模型版本固定，不使用 `jev-latest` 别名。
- 模型输出永不直接变成命令字符串；命令来自代码持有的 catalog。
- 数据外发必须先过三级门：显式开启、字段白名单、发送前预览 + 同意。
- artifact 只写进对应 run 的 `decisions/`，绝不修改 Core 拥有的文件。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Collection, Mapping, Protocol, Sequence

ADVISOR_SCHEMA = "wavebench.decision.v1"
DEFAULT_MODEL = "jev-1.13.0"  # 固定版本：官方文档说明别名会随发布漂移
NOT_STATED = "not_stated"
DECISIONS_DIRNAME = "decisions"


class SeamError(RuntimeError):
    """seam 内部契约被违反（不是网络/服务错误）。"""


class UnknownOptionError(SeamError):
    """模型返回了 catalog 之外的选项 id：fail-closed，不使用该结论。"""


# --------------------------------------------------------------------------- #
# 状态：可信字段与不可信 span 分离，且字段必须在白名单内
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class UntrustedSpan:
    source: str  # 例如 "instrument.response" / "operator.utterance" / "commands.log"
    text: str


@dataclass(frozen=True)
class DecisionState:
    fields: Mapping[str, Any] = field(default_factory=dict)
    untrusted: tuple[UntrustedSpan, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "fields": dict(self.fields),
            "untrusted": [{"source": span.source, "text": span.text} for span in self.untrusted],
        }


def assert_registered_fields(state: DecisionState, allowed: Collection[str]) -> None:
    """字段级白名单：出现未注册字段就拒绝，避免顺手把新字段送出机器。"""
    unknown = sorted(set(state.fields) - set(allowed))
    if unknown:
        raise SeamError(f"unregistered state field(s): {', '.join(unknown)}")


def bucket_cycles(estimated_cycles: float | None) -> str:
    """数字在代码里分桶，绝不把原始测量值丢给模型做数值判断。"""
    if estimated_cycles is None:
        return NOT_STATED
    if estimated_cycles < 2.0:
        return "few_cycles"
    if estimated_cycles > 25.0:
        return "many_cycles"
    return "ok"


def bucket_points_per_cycle(points_per_cycle: float | None) -> str:
    if points_per_cycle is None:
        return NOT_STATED
    return "dense" if points_per_cycle >= 20.0 else "sparse"


# --------------------------------------------------------------------------- #
# 问题：选项集来自代码拥有的 catalog
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class QuestionSpec:
    question_id: str
    kind: str  # "choice" | "score" | "noul"
    instructions: str
    criteria: Mapping[str, str] | tuple[str, ...] | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.kind, "instructions": self.instructions}
        if self.criteria is not None:
            payload["criteria"] = self.criteria
        return payload


@dataclass(frozen=True)
class RoutingOption:
    option_id: str
    summary: str  # 给模型看的 criteria 文本
    command_template: str  # 只留在代码里
    mutates_instrument: bool


def build_routing_question(
    options: Sequence[RoutingOption],
    *,
    instructions: str = "Which WaveBench entry point best matches the request?",
) -> QuestionSpec:
    if not options:
        raise SeamError("routing catalog must not be empty")
    criteria: dict[str, str] = {}
    for option in options:
        if option.option_id in criteria:
            raise SeamError(f"duplicate option id: {option.option_id}")
        criteria[option.option_id] = option.summary
    return QuestionSpec("route", "choice", instructions, criteria)


def build_questions(*specs: QuestionSpec) -> dict[str, QuestionSpec]:
    questions: dict[str, QuestionSpec] = {}
    for spec in specs:
        if spec.question_id in questions:
            raise SeamError(f"duplicate question id: {spec.question_id}")
        questions[spec.question_id] = spec
    return questions


# --------------------------------------------------------------------------- #
# 数据边界：发送前预览 + 显式同意
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PreviewPayload:
    """即将离开本机的完整请求体。发给操作员看，不联网。"""

    payload: Mapping[str, Any]
    payload_sha256: str
    payload_bytes: int
    untrusted_sources: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "payload": self.payload,
            "payload_sha256": self.payload_sha256,
            "payload_bytes": self.payload_bytes,
            "untrusted_sources": list(self.untrusted_sources),
        }


def preview_request(
    *,
    model: str,
    state: Mapping[str, Any],
    questions: Mapping[str, Any],
) -> PreviewPayload:
    body = {"model": model, "state": state, "questions": questions}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    sources: list[str] = []
    for span in state.get("untrusted", ()) or ():
        source = str(span.get("source", ""))
        if source:
            sources.append(source)
    return PreviewPayload(
        payload=body,
        payload_sha256=hashlib.sha256(encoded).hexdigest(),
        payload_bytes=len(encoded),
        untrusted_sources=tuple(sorted(set(sources))),
    )


@dataclass(frozen=True)
class ConsentDecision:
    """操作员对"把这份 state 发到外部服务"的显式决定。

    决定 4c：默认按次确认；可登记"本 run 内有效"；同意绑定 endpoint 集合与允许字段集，
    两者任一变化都必须重新确认。
    """

    granted: bool
    valid_for: str = "invocation"  # "invocation" | "run"
    run_id: str | None = None  # valid_for == "run" 时必填
    endpoint_hosts: tuple[str, ...] = ()
    allowed_state_fields: tuple[str, ...] = ()
    granted_by: str | None = None
    granted_at: str | None = None

    def __post_init__(self) -> None:
        if self.valid_for not in ("invocation", "run"):
            raise ValueError(f"consent valid_for must be 'invocation' or 'run', got {self.valid_for!r}")
        if self.granted and self.valid_for == "run" and not self.run_id:
            raise ValueError("run-scoped consent requires run_id")

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


def consent_covers(
    consent: ConsentDecision,
    *,
    endpoint_hosts: Sequence[str],
    allowed_state_fields: Sequence[str],
    run_id: str | None,
) -> tuple[bool, str | None]:
    """判断已有同意是否覆盖本次调用；返回 (是否覆盖, 失效原因)。

    同意被绑定到 endpoint 集合与允许字段集：插件声明发生变化即自动要求重新确认，
    避免"几个月前的配置悄然扩大外发范围"。
    """
    if not consent.granted:
        return False, "external_state_consent_not_granted"
    if set(consent.endpoint_hosts) != set(endpoint_hosts):
        return False, "consent_invalidated_by_endpoint_change"
    if set(consent.allowed_state_fields) != set(allowed_state_fields):
        return False, "consent_invalidated_by_allowed_field_change"
    if consent.valid_for == "invocation":
        return True, None
    if run_id is None:
        return False, "run_scoped_consent_requires_run_target"
    if consent.run_id != run_id:
        return False, "consent_invalidated_by_run_change"
    return True, None


# --------------------------------------------------------------------------- #
# artifact 落点：挂在对应 run 目录下，附加式
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class DecisionTarget:
    run_dir: Path
    run_json_sha256: str | None = None

    @property
    def run_id(self) -> str:
        return Path(self.run_dir).name

    def as_payload(self) -> dict[str, Any]:
        return {
            "run_dir": str(self.run_dir).replace("\\", "/"),
            "run_json_sha256": self.run_json_sha256,
        }


def artifact_path(target: DecisionTarget, *, advisor_id: str, timestamp: str) -> Path:
    return Path(target.run_dir) / DECISIONS_DIRNAME / f"{timestamp}-{advisor_id}.json"


def write_artifact(artifact: "DecisionArtifact", path: Path) -> Path:
    """独占创建：同名不覆盖；只新增文件，不改 Core 拥有的 artifact。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(artifact.to_json())
    return path


# --------------------------------------------------------------------------- #
# 答案与阈值：概率 -> 动作，阈值随结果记录
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Answer:
    question_id: str
    kind: str
    value: Any  # choice id 字符串 / score 数值 / noul 概率
    confidence: float | None = None
    probabilities: Mapping[str, float] | None = None


def parse_answers(raw: Mapping[str, Any]) -> dict[str, Answer]:
    """把服务响应解析为 typed answers；未知类型直接报错，不做兜底猜测。"""
    body = raw.get("answers")
    if not isinstance(body, Mapping):
        raise SeamError("response has no 'answers' object")
    answers: dict[str, Answer] = {}
    for question_id, entry in body.items():
        if not isinstance(entry, Mapping):
            raise SeamError(f"answer {question_id!r} is not an object")
        kind = str(entry.get("type", ""))
        probabilities = entry.get("probabilities")
        normalized = (
            {str(key): float(value) for key, value in probabilities.items()}
            if isinstance(probabilities, Mapping)
            else None
        )
        if kind == "choice":
            value: Any = entry.get("choice")
        elif kind == "score":
            value = entry.get("score")
        elif kind == "noul":
            value = entry.get("noul")
        else:
            raise SeamError(f"unknown answer type for {question_id!r}: {kind!r}")
        answers[question_id] = Answer(
            question_id=question_id,
            kind=kind,
            value=value,
            confidence=_optional_float(entry.get("confidence")),
            probabilities=normalized,
        )
    return answers


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass(frozen=True)
class ThresholdPolicy:
    accept: float = 0.60
    review: float = 0.35

    def as_record(self) -> dict[str, float]:
        return {"accept": self.accept, "review": self.review}


def apply_choice_threshold(
    answer: Answer,
    options: Sequence[RoutingOption],
    policy: ThresholdPolicy,
) -> list[dict[str, Any]]:
    """把 Choice 概率映射成建议对象。低于 accept 给人工复核，低于 review 什么都不给。"""
    if answer.kind != "choice":
        raise SeamError(f"expected choice answer, got {answer.kind!r}")
    by_id = {option.option_id: option for option in options}
    choice = str(answer.value)
    if choice not in by_id:
        raise UnknownOptionError(f"option {choice!r} is not in the catalog")
    probability = float((answer.probabilities or {}).get(choice, 0.0))
    if probability < policy.review:
        return []
    if probability < policy.accept:
        return [
            {
                "id": f"{answer.question_id}_needs_review",
                "priority": "high",
                "action": "human_review",
                "reason": (
                    f"choice probability {probability:.2f} < accept {policy.accept:.2f}; "
                    "route to a human instead of acting"
                ),
                "command": None,
                "parameters": {"option_id": choice},
                "mutates_instrument_if_applied": False,
                "raw_scpi": False,
            }
        ]
    option = by_id[choice]
    return [
        {
            "id": answer.question_id,
            "priority": "high" if option.mutates_instrument else "normal",
            "action": option.option_id,
            "reason": f"choice probability {probability:.2f} >= accept {policy.accept:.2f}",
            "command": option.command_template,
            "parameters": {},
            "mutates_instrument_if_applied": option.mutates_instrument,
            "raw_scpi": False,
        }
    ]


# --------------------------------------------------------------------------- #
# client：唯一的可注入依赖；真实 adapter 不参与单测
# --------------------------------------------------------------------------- #

class SystemOneClient(Protocol):
    def system_one(
        self,
        *,
        model: str,
        state: Mapping[str, Any],
        questions: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class DecisionArtifact:
    status: str  # "ok" | "refused" | "unavailable" | "invalid"
    reason: str | None
    requested_model: str
    reported_model: str | None
    target: Mapping[str, Any] | None
    consent: Mapping[str, Any] | None
    state: Mapping[str, Any]
    questions: Mapping[str, Any]
    answers: Mapping[str, Any]
    thresholds: Mapping[str, Any]
    recommendations: tuple[dict[str, Any], ...]
    duration_ms: int
    schema: str = ADVISOR_SCHEMA
    advisory_only: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "advisory_only": self.advisory_only,
            "status": self.status,
            "reason": self.reason,
            "target": self.target,
            "advisory": {
                "requested_model": self.requested_model,
                "reported_model": self.reported_model,
                "duration_ms": self.duration_ms,
            },
            "consent": self.consent,
            "state": self.state,
            "questions": self.questions,
            "answers": self.answers,
            "thresholds": self.thresholds,
            "recommendations": list(self.recommendations),
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, ensure_ascii=False, sort_keys=True)


class Advisor:
    """唯一持有 client 的门面。所有失败都降级为 artifact，绝不抛给调用方。"""

    def __init__(
        self,
        client: SystemOneClient | None,
        *,
        allowed_fields: Collection[str],
        endpoint_hosts: Sequence[str] = (),
        model: str = DEFAULT_MODEL,
        timer: Callable[[], float] | None = None,
    ) -> None:
        self._client = client
        self._allowed_fields = frozenset(allowed_fields)
        self._endpoint_hosts = tuple(endpoint_hosts)
        self._model = model
        self._timer = timer

    def advise(
        self,
        *,
        state: DecisionState,
        questions: Mapping[str, QuestionSpec],
        policy: ThresholdPolicy,
        options: Sequence[RoutingOption],
        consent: ConsentDecision,
        target: DecisionTarget | None = None,
        question_id: str = "route",
    ) -> DecisionArtifact:
        state_payload = state.as_payload()
        questions_payload = {qid: spec.as_payload() for qid, spec in questions.items()}
        preview = preview_request(
            model=self._model, state=state_payload, questions=questions_payload
        )
        target_payload = target.as_payload() if target is not None else None
        consent_payload = consent.as_payload(preview)

        try:
            assert_registered_fields(state, self._allowed_fields)
        except SeamError as exc:
            return self._artifact(
                "refused", f"unregistered_state_field: {exc}", state_payload, questions_payload,
                policy, (), 0, None, consent_payload, target_payload,
            )
        covered, refusal = consent_covers(
            consent,
            endpoint_hosts=self._endpoint_hosts,
            allowed_state_fields=sorted(self._allowed_fields),
            run_id=target.run_id if target is not None else None,
        )
        if not covered:
            # 未同意，或同意已因 endpoint/字段/run 变化失效：不发起任何网络调用
            return self._artifact(
                "refused", refusal, state_payload, questions_payload,
                policy, (), 0, None, consent_payload, target_payload,
            )
        if self._client is None:
            return self._artifact(
                "unavailable", "advisor_not_configured", state_payload, questions_payload,
                policy, (), 0, None, consent_payload, target_payload,
            )

        started = self._timer() if self._timer else None
        try:
            raw = self._client.system_one(
                model=self._model, state=state_payload, questions=questions_payload
            )
        except Exception as exc:  # 网络 / 限流 / 认证：一律降级，不影响确定性路径
            return self._artifact(
                "unavailable", f"advisor_call_failed: {type(exc).__name__}", state_payload,
                questions_payload, policy, (), self._elapsed_ms(started), None,
                consent_payload, target_payload,
            )
        duration_ms = self._elapsed_ms(started)
        reported_model = raw.get("model")
        reported_model = str(reported_model) if isinstance(reported_model, str) else None
        try:
            answers = parse_answers(raw)
            if question_id not in answers:
                raise SeamError(f"response is missing question {question_id!r}")
            recommendations = tuple(
                apply_choice_threshold(answers[question_id], options, policy)
            )
        except SeamError as exc:
            return self._artifact(
                "invalid", f"invalid_advisor_response: {exc}", state_payload, questions_payload,
                policy, (), duration_ms, reported_model, consent_payload, target_payload,
            )
        return self._artifact(
            "ok", None, state_payload, questions_payload, policy, recommendations,
            duration_ms, reported_model, consent_payload, target_payload,
            answers={
                qid: {
                    "type": answer.kind,
                    "value": answer.value,
                    "confidence": answer.confidence,
                    "probabilities": dict(answer.probabilities or {}),
                }
                for qid, answer in answers.items()
            },
        )

    def _elapsed_ms(self, started: float | None) -> int:
        if started is None or self._timer is None:
            return 0
        return int((self._timer() - started) * 1000)

    def _artifact(
        self,
        status: str,
        reason: str | None,
        state_payload: Mapping[str, Any],
        questions_payload: Mapping[str, Any],
        policy: ThresholdPolicy,
        recommendations: tuple[dict[str, Any], ...],
        duration_ms: int,
        reported_model: str | None,
        consent_payload: Mapping[str, Any],
        target_payload: Mapping[str, Any] | None,
        *,
        answers: Mapping[str, Any] | None = None,
    ) -> DecisionArtifact:
        return DecisionArtifact(
            status=status,
            reason=reason,
            requested_model=self._model,
            reported_model=reported_model,
            target=target_payload,
            consent=consent_payload,
            state=state_payload,
            questions=questions_payload,
            answers=answers or {},
            thresholds=policy.as_record(),
            recommendations=recommendations,
            duration_ms=duration_ms,
        )
