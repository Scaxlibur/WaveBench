"""advisor decision artifact（Core 拥有）：schema `wavebench.decision.v1`。

契约见 `docs/project/rfcs/WaveBench_advisor插件RFC.md`：

- 写入对应 run 目录的 `decisions/`，附加式，绝不修改 `run.json` / `summary.csv` / `steps/*`；
- 文件名含 UTC 时间戳与 advisor id，独占创建；
- 无 run 目录时不落盘；
- "概率 → 动作"的阈值策略由 Core 配置拥有，插件不得自带阈值；
- 产物只做建议，不参与 `run.json.status`、质量门、`auto_recover` 或 capability 判定。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

ADVISOR_SCHEMA = "wavebench.decision.v1"
DECISIONS_DIRNAME = "decisions"

ARTIFACT_STATUSES = ("ok", "refused", "preview_only", "unavailable", "invalid")
# 文件名安全：只允许字母、数字、下划线、点与连字符（冒号在 Windows 上是非法字符）。
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


class DecisionArtifactError(ValueError):
    """artifact 契约被违反（不是网络或服务错误）。"""


@dataclass(frozen=True)
class ThresholdPolicy:
    """概率 → 动作的阈值；低于 accept 给人工复核，低于 review 不给建议。"""

    accept: float = 0.60
    review: float = 0.35

    def __post_init__(self) -> None:
        for label, value in (("accept", self.accept), ("review", self.review)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DecisionArtifactError(f"threshold {label} must be a number")
            if not 0.0 <= float(value) <= 1.0:
                raise DecisionArtifactError(f"threshold {label} must be within 0..1")
        if self.review > self.accept:
            raise DecisionArtifactError("review threshold must not exceed accept threshold")

    def as_record(self) -> dict[str, float]:
        return {"accept": float(self.accept), "review": float(self.review)}


@dataclass(frozen=True)
class DecisionTarget:
    """artifact 的落点：对应 run 目录，以及可选的 run.json 指纹。"""

    run_dir: Path
    run_json_sha256: str | None = None

    @property
    def run_id(self) -> str:
        return Path(self.run_dir).name

    def as_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir).replace("\\", "/"),
            "run_json_sha256": self.run_json_sha256,
        }


@dataclass(frozen=True)
class DecisionArtifact:
    """`wavebench.decision.v1` 的正文；任何候选结论都只作为建议留痕。"""

    status: str
    requested_model: str
    reason: str | None = None
    reported_model: str | None = None
    target: Mapping[str, Any] | None = None
    consent: Mapping[str, Any] | None = None
    state: Mapping[str, Any] = field(default_factory=dict)
    questions: Mapping[str, Any] = field(default_factory=dict)
    answers: Mapping[str, Any] = field(default_factory=dict)
    thresholds: Mapping[str, Any] = field(default_factory=dict)
    recommendations: tuple[Mapping[str, Any], ...] = ()
    duration_ms: int = 0
    schema: str = ADVISOR_SCHEMA
    advisory_only: bool = True

    def __post_init__(self) -> None:
        if self.status not in ARTIFACT_STATUSES:
            raise DecisionArtifactError(
                f"artifact status must be one of {', '.join(ARTIFACT_STATUSES)}, got {self.status!r}"
            )
        if not self.advisory_only:
            raise DecisionArtifactError("decision artifacts must stay advisory-only")
        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, int):
            raise DecisionArtifactError("duration_ms must be an integer")
        if self.duration_ms < 0:
            raise DecisionArtifactError("duration_ms must be >= 0")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "advisory_only": self.advisory_only,
            "status": self.status,
            "reason": self.reason,
            "target": dict(self.target) if self.target is not None else None,
            "advisory": {
                "requested_model": self.requested_model,
                "reported_model": self.reported_model,
                "duration_ms": self.duration_ms,
            },
            "consent": dict(self.consent) if self.consent is not None else None,
            "state": dict(self.state),
            "questions": dict(self.questions),
            "answers": dict(self.answers),
            "thresholds": dict(self.thresholds),
            "recommendations": [dict(item) for item in self.recommendations],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, ensure_ascii=False, sort_keys=True)


def utc_stamp(moment: datetime | None = None) -> str:
    """文件名用的 UTC 时间戳（无冒号，Windows 路径安全）。"""
    return (moment or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def decisions_dir(run_dir: str | Path) -> Path:
    return Path(run_dir) / DECISIONS_DIRNAME


def artifact_path(run_dir: str | Path, *, advisor_id: str, timestamp: str) -> Path:
    for label, value in (("advisor_id", advisor_id), ("timestamp", timestamp)):
        if not isinstance(value, str) or not _SAFE_TOKEN.match(value):
            raise DecisionArtifactError(
                f"{label} must match {_SAFE_TOKEN.pattern} to stay a safe file name"
            )
    return decisions_dir(run_dir) / f"{timestamp}-{advisor_id}.json"


def write_artifact(
    artifact: DecisionArtifact,
    run_dir: str | Path,
    *,
    advisor_id: str,
    timestamp: str | None = None,
) -> Path | None:
    """独占创建 artifact；无 run 目录时不落盘（返回 None）。

    产物永远是新文件：不覆盖任何既有文件，也不触碰 Core 拥有的 `run.json` / `summary.csv` /
    `steps/*`，因此不需要走原子替换路径；文件名带 UTC 时间戳，重名即视为编程错误。
    """
    run = Path(run_dir)
    if not run.is_dir():
        return None
    path = artifact_path(run, advisor_id=advisor_id, timestamp=timestamp or utc_stamp())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(artifact.to_json())
    return path


__all__ = [
    "ADVISOR_SCHEMA",
    "ARTIFACT_STATUSES",
    "DECISIONS_DIRNAME",
    "DecisionArtifact",
    "DecisionArtifactError",
    "DecisionTarget",
    "ThresholdPolicy",
    "artifact_path",
    "decisions_dir",
    "utc_stamp",
    "write_artifact",
]
