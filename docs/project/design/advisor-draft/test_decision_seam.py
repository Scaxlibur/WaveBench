"""第 0 批 seam 的离线单测：不联网、不需要 API key、不需要 typesafe-sdk。"""

from __future__ import annotations

import json

import pytest

from decision_seam import (
    ADVISOR_SCHEMA,
    DEFAULT_MODEL,
    Advisor,
    ConsentDecision,
    DecisionState,
    DecisionTarget,
    RoutingOption,
    SeamError,
    ThresholdPolicy,
    UnknownOptionError,
    UntrustedSpan,
    apply_choice_threshold,
    artifact_path,
    build_questions,
    build_routing_question,
    bucket_cycles,
    bucket_points_per_cycle,
    consent_covers,
    parse_answers,
    preview_request,
    write_artifact,
)

ALLOWED_FIELDS = ("run_status", "cycles_bucket", "points_per_cycle_bucket")
ENDPOINTS = ("api.typesafe.ai",)
RUN_DIR_NAME = "20260928_0740_loop_gain"

OPTIONS = (
    RoutingOption(
        option_id="run.template.source_scope_sine",
        summary="Start a source+scope sine experiment from a built-in template.",
        command_template="python -m wavebench run template source-scope-sine --output {plan}",
        mutates_instrument=False,
    ),
    RoutingOption(
        option_id="capture.inspect",
        summary="Inspect an existing offline capture package.",
        command_template="python -m wavebench capture inspect {capture_dir}",
        mutates_instrument=False,
    ),
    RoutingOption(
        option_id="scope.observe.fetch",
        summary="Explicitly read scope waveforms (write path: may stop acquisition).",
        command_template="python -m wavebench observe --channel {channel} --fetch-waveform",
        mutates_instrument=True,
    ),
)

UTTERANCE = "示波器上看着像三角波但有点歪，帮我看看"


class FakeClient:
    """记录调用次数：用来断言"未同意 / 未注册字段 / 预览"时真的没有外发。"""

    def __init__(self, answer: dict | None = None) -> None:
        self.calls: list[dict] = []
        self._answer = answer or {
            "type": "choice",
            "choice": "run.template.source_scope_sine",
            "confidence": 0.8,
            "probabilities": {"run.template.source_scope_sine": 0.85, "capture.inspect": 0.15},
        }

    def system_one(self, *, model, state, questions):
        self.calls.append({"model": model, "state": state, "questions": questions})
        return {"model": "jev-1.13.0", "answers": {"route": self._answer}, "usage": {}}


class BrokenClient:
    def system_one(self, *, model, state, questions):
        raise TimeoutError("network down")


def _advisor(client=None) -> Advisor:
    return Advisor(client, allowed_fields=ALLOWED_FIELDS, endpoint_hosts=ENDPOINTS)


def _state(**overrides) -> DecisionState:
    fields = {
        "run_status": "partial",
        "cycles_bucket": bucket_cycles(1.8),
        "points_per_cycle_bucket": bucket_points_per_cycle(12.0),
    }
    fields.update(overrides)
    return DecisionState(
        fields=fields,
        untrusted=(UntrustedSpan("operator.utterance", UTTERANCE),),
    )


def _consent(**overrides) -> ConsentDecision:
    values = {
        "granted": True,
        "valid_for": "invocation",
        "endpoint_hosts": ENDPOINTS,
        "allowed_state_fields": ALLOWED_FIELDS,
        "granted_by": "operator",
        "granted_at": "2026-09-28T07:40:10Z",
    }
    values.update(overrides)
    return ConsentDecision(**values)


def _target(tmp_path, name: str = RUN_DIR_NAME) -> DecisionTarget:
    return DecisionTarget(run_dir=tmp_path / "data" / "runs" / name)


def _advise(advisor: Advisor, *, state=None, consent=None, target=None):
    return advisor.advise(
        state=state or _state(),
        questions=build_questions(build_routing_question(OPTIONS)),
        policy=ThresholdPolicy(),
        options=OPTIONS,
        consent=consent or _consent(),
        target=target,
    )


def test_build_state_is_deterministic():
    first = json.dumps(_state().as_payload(), sort_keys=True)
    second = json.dumps(_state().as_payload(), sort_keys=True)

    assert first == second
    payload = json.loads(first)
    assert payload["fields"]["cycles_bucket"] == "few_cycles"
    assert payload["untrusted"][0]["source"] == "operator.utterance"


def test_questions_never_embed_untrusted_text():
    serialized = json.dumps(build_routing_question(OPTIONS).as_payload(), ensure_ascii=False)

    for span in _state().untrusted:
        assert span.text not in serialized


def test_preview_request_does_not_send_and_shows_untrusted_sources():
    client = FakeClient()
    state = _state()

    preview = preview_request(
        model=DEFAULT_MODEL,
        state=state.as_payload(),
        questions={"route": build_routing_question(OPTIONS).as_payload()},
    )

    assert client.calls == []
    assert preview.untrusted_sources == ("operator.utterance",)
    assert preview.payload_bytes > 0
    assert len(preview.payload_sha256) == 64
    assert UTTERANCE in json.dumps(preview.payload, ensure_ascii=False)


def test_without_consent_refuses_and_does_not_send():
    client = FakeClient()

    artifact = _advise(_advisor(client), consent=_consent(granted=False, granted_by=None))

    assert artifact.status == "refused"
    assert artifact.reason == "external_state_consent_not_granted"
    assert artifact.recommendations == ()
    assert client.calls == []
    assert artifact.consent["accepted_data_leaves_machine"] is False


def test_unregistered_state_field_is_refused_before_sending():
    client = FakeClient()

    artifact = _advise(_advisor(client), state=_state(instrument_serial="SN123456"))

    assert artifact.status == "refused"
    assert "unregistered_state_field" in (artifact.reason or "")
    assert client.calls == []


def test_run_scoped_consent_covers_the_same_run_only(tmp_path):
    client = FakeClient()
    advisor = _advisor(client)
    consent = _consent(valid_for="run", run_id=RUN_DIR_NAME)

    ok = _advise(advisor, consent=consent, target=_target(tmp_path))
    assert ok.status == "ok"
    assert len(client.calls) == 1

    # 同一个同意不能授权另一次 run
    other = _advise(advisor, consent=consent, target=_target(tmp_path, "20260928_0800_other"))
    assert other.status == "refused"
    assert other.reason == "consent_invalidated_by_run_change"
    assert len(client.calls) == 1


def test_run_scoped_consent_without_run_target_is_refused():
    client = FakeClient()

    artifact = _advise(
        _advisor(client),
        consent=_consent(valid_for="run", run_id=RUN_DIR_NAME),
    )

    assert artifact.status == "refused"
    assert artifact.reason == "run_scoped_consent_requires_run_target"
    assert client.calls == []


def test_run_scoped_consent_requires_run_id_at_construction():
    with pytest.raises(ValueError, match="run_id"):
        ConsentDecision(granted=True, valid_for="run", endpoint_hosts=ENDPOINTS,
                        allowed_state_fields=ALLOWED_FIELDS)


def test_consent_is_invalidated_by_endpoint_and_field_changes():
    covered, reason = consent_covers(
        _consent(),
        endpoint_hosts=("api.typesafe.ai", "evil.example"),
        allowed_state_fields=ALLOWED_FIELDS,
        run_id=None,
    )
    assert covered is False
    assert reason == "consent_invalidated_by_endpoint_change"

    covered, reason = consent_covers(
        _consent(),
        endpoint_hosts=ENDPOINTS,
        allowed_state_fields=(*ALLOWED_FIELDS, "instrument_serial"),
        run_id=None,
    )
    assert covered is False
    assert reason == "consent_invalidated_by_allowed_field_change"

    covered, reason = consent_covers(
        _consent(), endpoint_hosts=ENDPOINTS, allowed_state_fields=ALLOWED_FIELDS, run_id=None
    )
    assert covered is True
    assert reason is None


def test_thresholds_map_probability_to_recommendation():
    answers = parse_answers(
        {
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "run.template.source_scope_sine",
                    "confidence": 0.8,
                    "probabilities": {"run.template.source_scope_sine": 0.85, "capture.inspect": 0.15},
                }
            }
        }
    )

    recommendations = apply_choice_threshold(answers["route"], OPTIONS, ThresholdPolicy())

    assert len(recommendations) == 1
    assert recommendations[0]["action"] == "run.template.source_scope_sine"
    assert recommendations[0]["mutates_instrument_if_applied"] is False
    assert ">= accept 0.60" in recommendations[0]["reason"]
    assert recommendations[0]["command"].startswith("python -m wavebench run template")


def test_low_confidence_goes_to_review_without_command():
    answers = parse_answers(
        {
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "capture.inspect",
                    "confidence": 0.5,
                    "probabilities": {"capture.inspect": 0.40, "run.template.source_scope_sine": 0.35},
                }
            }
        }
    )

    recommendations = apply_choice_threshold(answers["route"], OPTIONS, ThresholdPolicy())

    assert [item["action"] for item in recommendations] == ["human_review"]
    assert recommendations[0]["command"] is None


def test_unknown_option_id_is_rejected():
    answers = parse_answers(
        {"answers": {"route": {"type": "choice", "choice": "not.a.real.option", "probabilities": {}}}}
    )

    with pytest.raises(UnknownOptionError):
        apply_choice_threshold(answers["route"], OPTIONS, ThresholdPolicy())


def test_unknown_answer_type_is_rejected():
    with pytest.raises(SeamError):
        parse_answers({"answers": {"route": {"type": "freetext", "text": "run something"}}})


def test_advisor_without_client_is_unavailable_and_harmless():
    artifact = _advise(_advisor(None))

    assert artifact.status == "unavailable"
    assert artifact.reason == "advisor_not_configured"
    assert artifact.recommendations == ()
    assert artifact.advisory_only is True


def test_advisor_call_failure_degrades_without_raising():
    artifact = _advise(_advisor(BrokenClient()))

    assert artifact.status == "unavailable"
    assert artifact.reason == "advisor_call_failed: TimeoutError"
    assert artifact.recommendations == ()


def test_artifact_records_model_consent_thresholds_and_target(tmp_path):
    client = FakeClient(
        answer={
            "type": "choice",
            "choice": "scope.observe.fetch",
            "confidence": 0.7,
            "probabilities": {"scope.observe.fetch": 0.7, "capture.inspect": 0.3},
        }
    )

    artifact = _advise(_advisor(client), target=_target(tmp_path))
    payload = json.loads(artifact.to_json())

    assert payload["status"] == "ok"
    assert payload["schema"] == ADVISOR_SCHEMA
    assert payload["advisory_only"] is True
    assert payload["advisory"]["requested_model"] == DEFAULT_MODEL
    assert payload["advisory"]["reported_model"] == "jev-1.13.0"
    assert payload["target"]["run_dir"].endswith(RUN_DIR_NAME)
    assert payload["thresholds"] == {"accept": 0.6, "review": 0.35}
    # 同意记录：范围 + 绑定的 endpoint 集合 + 允许字段集 + 本次 payload 指纹
    assert payload["consent"]["granted"] is True
    assert payload["consent"]["valid_for"] == "invocation"
    assert payload["consent"]["endpoint_hosts"] == list(ENDPOINTS)
    assert payload["consent"]["allowed_state_fields"] == list(ALLOWED_FIELDS)
    assert len(payload["consent"]["payload_sha256"]) == 64
    assert payload["consent"]["payload_bytes"] > 0
    assert payload["answers"]["route"]["probabilities"]["scope.observe.fetch"] == 0.7
    # 写路径选项被选中时，建议里必须显式标出会改变仪器状态
    assert payload["recommendations"][0]["mutates_instrument_if_applied"] is True


def test_artifact_is_written_under_run_decisions_and_never_overwrites(tmp_path):
    target = _target(tmp_path)
    artifact = _advise(_advisor(FakeClient()), target=target)

    path = artifact_path(target, advisor_id="routing", timestamp="20260928T074010Z")
    assert path == target.run_dir / "decisions" / "20260928T074010Z-routing.json"

    write_artifact(artifact, path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["schema"] == ADVISOR_SCHEMA

    # 独占创建：同名二次写入必须失败，绝不覆盖
    with pytest.raises(FileExistsError):
        write_artifact(artifact, path)


def test_advisor_without_run_target_keeps_no_target_and_writes_nothing(tmp_path):
    artifact = _advise(_advisor(FakeClient()))

    assert artifact.target is None
    assert not (tmp_path / "decisions").exists()
