"""Core 阶段 1 骨架的契约测试：全部离线，不需要网络或第三方 SDK。"""

from __future__ import annotations

import json

import pytest

from advisor_api import (
    ADVISOR_CAPABILITY_PREFIX,
    SUPPORTED_ADVISOR_API_VERSION,
    AdvisorPlugin,
    EgressDeclaration,
)
from advisor_packages import (
    ADVISOR_ENTRY_POINT_GROUP,
    INSTRUMENT_ENTRY_POINT_GROUP,
    PackageCategoryError,
    select_package_category,
    source_entry_point_group,
)
from advisor_registry import (
    AdvisorRegistry,
    advisor_doctor_records,
    build_advisor_registry,
    builtin_advisor_registry,
    has_advisor_doctor_errors,
    load_advisor_entry_points,
    validate_advisor,
)
from builtin_advisors import RULE_ADVISOR_ID, RULE_ADVISOR_MODEL_ID, RuleAdvisorClient, rule_advisor_plugin

from decision_seam import (
    Advisor,
    ConsentDecision,
    DecisionState,
    DecisionTarget,
    RoutingOption,
    ThresholdPolicy,
    UntrustedSpan,
    build_questions,
    build_routing_question,
)

ALLOWED_FIELDS = ("run_status", "cycles_bucket")


def _local_consent() -> ConsentDecision:
    """非外发 advisor：endpoint 集合为空，同意范围按次。"""
    return ConsentDecision(
        granted=True,
        valid_for="invocation",
        endpoint_hosts=(),
        allowed_state_fields=ALLOWED_FIELDS,
    )


def _transmitting_plugin(**overrides) -> AdvisorPlugin:
    values = {
        "advisor_id": "example.remote",
        "display_name": "Example remote advisor",
        "provider": "example",
        "capabilities": ("advisor.route", "advisor.external_state"),
        "summary": "Remote advisor used to exercise the egress contract.",
        "egress": EgressDeclaration(
            transmits_off_machine=True,
            purpose="Remote judgment.",
            endpoint_hosts=("api.example.invalid",),
            allowed_state_fields=("run_status",),
        ),
    }
    values.update(overrides)
    return AdvisorPlugin(**values)


# --- 契约校验 --------------------------------------------------------------- #


def test_builtin_rule_advisor_passes_contract():
    plugin = rule_advisor_plugin()

    assert validate_advisor(plugin) == []
    assert plugin.transmits_off_machine is False
    assert plugin.api_version == SUPPORTED_ADVISOR_API_VERSION


def test_capability_must_use_advisor_prefix():
    plugin = _transmitting_plugin(capabilities=("scope.idn",))

    errors = validate_advisor(plugin)

    assert any(ADVISOR_CAPABILITY_PREFIX in message for message in errors)


def test_unknown_api_version_is_rejected():
    plugin = _transmitting_plugin(api_version="wavebench.advisor.v99")

    errors = validate_advisor(plugin)

    assert any("api_version" in message for message in errors)


def test_missing_purpose_is_rejected():
    with pytest.raises(ValueError, match="purpose"):
        EgressDeclaration(transmits_off_machine=False, purpose="  ")


def test_transmitting_advisor_requires_endpoints_and_fields():
    with pytest.raises(ValueError, match="endpoint host"):
        EgressDeclaration(transmits_off_machine=True, purpose="Remote judgment.")
    with pytest.raises(ValueError, match="allowed_state_fields"):
        EgressDeclaration(
            transmits_off_machine=True,
            purpose="Remote judgment.",
            endpoint_hosts=("api.example.invalid",),
        )


def test_non_transmitting_advisor_must_not_declare_endpoints():
    with pytest.raises(ValueError, match="must not declare endpoint hosts"):
        EgressDeclaration(
            transmits_off_machine=False,
            purpose="Local only.",
            endpoint_hosts=("api.example.invalid",),
        )


def test_transmitting_advisor_must_declare_external_state_capability():
    plugin = _transmitting_plugin(capabilities=("advisor.route",))

    errors = validate_advisor(plugin)

    assert any("advisor.external_state" in message for message in errors)


def test_non_transmitting_advisor_must_not_claim_external_state():
    plugin = rule_advisor_plugin()
    plugin = AdvisorPlugin(
        advisor_id=plugin.advisor_id,
        display_name=plugin.display_name,
        provider=plugin.provider,
        capabilities=("advisor.route", "advisor.external_state"),
        summary=plugin.summary,
        egress=plugin.egress,
    )

    errors = validate_advisor(plugin)

    assert any("must not declare" in message for message in errors)


# --- 裁定 2a：一个包只能声明一个类别 ---------------------------------------- #


def test_package_may_declare_only_one_category():
    assert select_package_category([INSTRUMENT_ENTRY_POINT_GROUP]) == "instrument"
    assert select_package_category([ADVISOR_ENTRY_POINT_GROUP]) == "advisor"

    with pytest.raises(PackageCategoryError, match="only one category"):
        select_package_category([INSTRUMENT_ENTRY_POINT_GROUP, ADVISOR_ENTRY_POINT_GROUP])

    with pytest.raises(PackageCategoryError, match="exactly one"):
        select_package_category([])


def test_source_project_declares_one_category():
    assert (
        source_entry_point_group({"entry-points": {ADVISOR_ENTRY_POINT_GROUP: {"a": "b"}}})
        == "advisor"
    )
    with pytest.raises(PackageCategoryError):
        source_entry_point_group({"entry-points": {}})
    with pytest.raises(PackageCategoryError):
        source_entry_point_group(
            {
                "entry-points": {
                    INSTRUMENT_ENTRY_POINT_GROUP: {"a": "b"},
                    ADVISOR_ENTRY_POINT_GROUP: {"c": "d"},
                }
            }
        )


# --- registry / doctor ------------------------------------------------------ #


def test_builtin_registry_lists_rule_advisor():
    registry = builtin_advisor_registry()

    assert [plugin.advisor_id for plugin in registry.list_advisors()] == [RULE_ADVISOR_ID]
    assert registry.get(RULE_ADVISOR_ID).provider == "wavebench"
    with pytest.raises(KeyError):
        registry.get("nope")


def test_registry_filters_by_capability():
    registry = AdvisorRegistry((rule_advisor_plugin(), _transmitting_plugin()))

    assert [plugin.advisor_id for plugin in registry.list_advisors(capability="advisor.external_state")] == [
        "example.remote"
    ]


class _EntryPoint:
    def __init__(self, name, target):
        self.name = name
        module = getattr(target, "__module__", "test_advisor_skeleton")
        qualname = getattr(target, "__qualname__", "target")
        self.value = f"{module}:{qualname}"
        self._target = target

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


def test_entry_point_loading_reports_errors_without_raising():
    loaded = load_advisor_entry_points(
        [_EntryPoint(RULE_ADVISOR_ID, rule_advisor_plugin), _EntryPoint("broken", RuntimeError("boom"))]
    )

    assert loaded[0][0] is not None
    assert loaded[1][0] is None
    assert "boom" in (loaded[1][1].message if loaded[1][1] else "")


def test_entry_point_name_must_match_advisor_id():
    loaded = load_advisor_entry_points([_EntryPoint("mismatched.id", rule_advisor_plugin)])

    assert loaded[0][0] is None
    assert "does not match advisor_id" in (loaded[0][1].message if loaded[0][1] else "")


def test_build_registry_includes_entry_points_only_when_requested():
    offline = build_advisor_registry()
    assert [plugin.advisor_id for plugin in offline.registry.list_advisors()] == [RULE_ADVISOR_ID]

    loaded = build_advisor_registry(
        include_entry_points=True,
        entry_points=[_EntryPoint("example.remote", lambda: _transmitting_plugin())],
    )
    assert [plugin.advisor_id for plugin in loaded.registry.list_advisors()] == [
        "builtin.rule_advisor",
        "example.remote",
    ]


def test_doctor_reports_contract_errors():
    registry = AdvisorRegistry((_transmitting_plugin(capabilities=("advisor.route",)),))

    records = advisor_doctor_records(registry)

    assert has_advisor_doctor_errors(records) is True
    assert all(record.subject == "example.remote" for record in records)


def test_doctor_is_clean_for_builtin_advisor():
    records = advisor_doctor_records(builtin_advisor_registry())

    assert has_advisor_doctor_errors(records) is False
    assert any(record.severity == "ok" for record in records)


# --- 基线 advisor 走完整链路（离线端到端） --------------------------------- #


ROUTING_OPTIONS = (
    RoutingOption(
        option_id="capture.inspect",
        summary="Inspect an existing offline capture package.",
        command_template="python -m wavebench capture inspect {capture_dir}",
        mutates_instrument=False,
    ),
    RoutingOption(
        option_id="run.template.sine",
        summary="Generate a plan to start a sine measurement run.",
        command_template="python -m wavebench run template source-scope-sine",
        mutates_instrument=False,
    ),
)


def _state() -> DecisionState:
    return DecisionState(
        fields={"run_status": "partial", "cycles_bucket": "few_cycles"},
        untrusted=(
            UntrustedSpan("operator.utterance", "please inspect the offline capture package"),
        ),
    )


def _advise_with_rule_advisor(*, target=None):
    advisor = Advisor(
        RuleAdvisorClient(),
        allowed_fields=ALLOWED_FIELDS,
        endpoint_hosts=(),
        model=RULE_ADVISOR_MODEL_ID,
    )
    return advisor.advise(
        state=_state(),
        questions=build_questions(build_routing_question(ROUTING_OPTIONS)),
        policy=ThresholdPolicy(),
        options=ROUTING_OPTIONS,
        consent=_local_consent(),
        target=target,
    )


def test_rule_advisor_completes_the_pipeline_offline():
    artifact = _advise_with_rule_advisor()

    assert artifact.status == "ok"
    assert artifact.requested_model == RULE_ADVISOR_MODEL_ID
    assert artifact.reported_model == RULE_ADVISOR_MODEL_ID
    assert artifact.consent["endpoint_hosts"] == []
    recommendations = artifact.recommendations
    assert len(recommendations) == 1
    assert recommendations[0]["action"] == "capture.inspect"
    assert recommendations[0]["command"].startswith("python -m wavebench capture inspect")


def test_rule_advisor_is_deterministic():
    first = _advise_with_rule_advisor().to_json()
    second = _advise_with_rule_advisor().to_json()

    assert json.loads(first)["answers"] == json.loads(second)["answers"]


def test_rule_advisor_routes_to_human_review_when_nothing_matches():
    advisor = Advisor(
        RuleAdvisorClient(),
        allowed_fields=ALLOWED_FIELDS,
        endpoint_hosts=(),
        model=RULE_ADVISOR_MODEL_ID,
    )
    state = DecisionState(fields={"run_status": "ok", "cycles_bucket": "ok"})

    artifact = advisor.advise(
        state=state,
        questions=build_questions(build_routing_question(ROUTING_OPTIONS)),
        policy=ThresholdPolicy(),
        options=ROUTING_OPTIONS,
        consent=_local_consent(),
    )

    # 两个选项且完全没有重叠 -> 均匀 0.50，落在 review 区间 -> 交人工复核，不给具体动作
    assert artifact.status == "ok"
    assert [item["action"] for item in artifact.recommendations] == ["human_review"]
    assert artifact.recommendations[0]["command"] is None


def test_rule_advisor_returns_nothing_below_review_threshold():
    three_options = ROUTING_OPTIONS + (
        RoutingOption(
            option_id="power.status",
            summary="Report the power supply output status.",
            command_template="python -m wavebench power status",
            mutates_instrument=False,
        ),
    )
    advisor = Advisor(
        RuleAdvisorClient(),
        allowed_fields=ALLOWED_FIELDS,
        endpoint_hosts=(),
        model=RULE_ADVISOR_MODEL_ID,
    )
    state = DecisionState(fields={"run_status": "ok", "cycles_bucket": "ok"})

    artifact = advisor.advise(
        state=state,
        questions=build_questions(build_routing_question(three_options)),
        policy=ThresholdPolicy(),
        options=three_options,
        consent=_local_consent(),
    )

    # 三个选项均匀 0.333 < review 0.35 -> 连人工复核项都不给（最保守）
    assert artifact.status == "ok"
    assert artifact.recommendations == ()


def test_rule_advisor_writes_artifact_under_run_decisions(tmp_path):
    target = DecisionTarget(run_dir=tmp_path / "data" / "runs" / "20260928_0900_baseline")

    artifact = _advise_with_rule_advisor(target=target)

    assert artifact.target["run_dir"].endswith("20260928_0900_baseline")
