"""Core 阶段 1 骨架：内置确定性 advisor（`rule_advisor`）。

它是 advisor 类别里的第二个实现（第一个是外部厂商插件），用途：
- 让新类别在 CI 与离线下可测；
- 让"外部 advisor 必须打败确定性基线"变成一条可执行对比；
- 给第三方一个最小可读样例。

实现上它满足与真实服务相同的 client 协议（同一答案载荷形状），因此整条
`build_state -> questions -> answers -> thresholds -> artifact` 链路完全复用。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from advisor_api import AdvisorPlugin, EgressDeclaration

RULE_ADVISOR_ID = "builtin.rule_advisor"
RULE_ADVISOR_MODEL_ID = "builtin.rule_advisor"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _state_tokens(state: Mapping[str, Any]) -> set[str]:
    parts: list[str] = []
    for value in (state.get("fields") or {}).values():
        parts.append(str(value))
    for span in state.get("untrusted") or ():
        parts.append(str(span.get("text", "")))
    return _tokens(" ".join(parts))


def _choice_answer(spec: Mapping[str, Any], state_tokens: set[str]) -> dict[str, Any]:
    criteria = spec.get("criteria")
    if not isinstance(criteria, Mapping) or not criteria:
        raise ValueError("choice question needs a non-empty criteria mapping")
    scores = {str(key): len(_tokens(str(text)) & state_tokens) for key, text in criteria.items()}
    total = sum(scores.values())
    if total:
        probabilities = {key: value / total for key, value in scores.items()}
    else:
        share = 1.0 / len(scores)
        probabilities = dict.fromkeys(scores, share)
    # 并列时取字典序最小的选项 id，保证确定性
    best = max(sorted(probabilities), key=lambda key: probabilities[key])
    return {
        "type": "choice",
        "choice": best,
        "confidence": probabilities[best],
        "probabilities": probabilities,
    }


def _noul_answer(spec: Mapping[str, Any], state_tokens: set[str]) -> dict[str, Any]:
    instruction_tokens = _tokens(str(spec.get("instructions", "")))
    if not instruction_tokens:
        raise ValueError("noul question needs instructions")
    return {"type": "noul", "noul": len(instruction_tokens & state_tokens) / len(instruction_tokens)}


def _score_answer(spec: Mapping[str, Any], state_tokens: set[str]) -> dict[str, Any]:
    criteria = spec.get("criteria")
    if not isinstance(criteria, Sequence) or isinstance(criteria, (str, bytes)) or len(criteria) < 2:
        raise ValueError("score question needs at least two criteria levels")
    scores = [len(_tokens(str(level)) & state_tokens) for level in criteria]
    total = sum(scores)
    probabilities = {
        str(index): (value / total if total else 1.0 / len(scores))
        for index, value in enumerate(scores)
    }
    best = max(range(len(scores)), key=lambda index: (scores[index], -index))
    return {
        "type": "score",
        "score": float(best),
        "confidence": probabilities[str(best)],
        "legend": {str(index): str(level) for index, level in enumerate(criteria)},
        "probabilities": probabilities,
    }


@dataclass(frozen=True)
class RuleAdvisorClient:
    """与真实服务相同的调用形状，但不联网、不依赖任何 SDK。"""

    model_id: str = RULE_ADVISOR_MODEL_ID

    def system_one(
        self,
        *,
        model: str,
        state: Mapping[str, Any],
        questions: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        state_tokens = _state_tokens(state)
        answers: dict[str, Any] = {}
        for question_id, spec in questions.items():
            kind = str(spec.get("type", ""))
            if kind == "choice":
                answers[question_id] = _choice_answer(spec, state_tokens)
            elif kind == "noul":
                answers[question_id] = _noul_answer(spec, state_tokens)
            elif kind == "score":
                answers[question_id] = _score_answer(spec, state_tokens)
            else:
                raise ValueError(f"rule advisor does not support question type {kind!r}")
        return {
            "model": self.model_id,
            "answers": answers,
            "usage": {"input_tokens": len(state_tokens), "output_tokens": 0},
        }


def rule_advisor_plugin() -> AdvisorPlugin:
    return AdvisorPlugin(
        advisor_id=RULE_ADVISOR_ID,
        display_name="Builtin rule advisor",
        provider="wavebench",
        capabilities=("advisor.route",),
        summary="Deterministic keyword-overlap baseline for closed-set routing.",
        egress=EgressDeclaration(
            transmits_off_machine=False,
            purpose="Local baseline; the state never leaves this machine.",
        ),
    )
