from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "docs_impact.py"
SPEC = importlib.util.spec_from_file_location("wavebench_docs_impact", SCRIPT)
assert SPEC and SPEC.loader
IMPACT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = IMPACT
SPEC.loader.exec_module(IMPACT)


def test_run_plan_change_lists_generated_and_task_pages():
    impacts = IMPACT.impacts_for_paths(["src/wavebench/services/run_plan.py"])

    assert len(impacts) == 1
    rule, matches = impacts[0]
    assert rule.title == "run plan / templates"
    assert matches == ("src/wavebench/services/run_plan.py",)
    assert "docs/reference/generated/run-schema.md" in rule.pages


def test_multiple_domains_keep_a_scoped_set_of_candidates():
    impacts = IMPACT.impacts_for_paths(
        ["src/wavebench/cli.py", "src/wavebench/services/access_policy.py"]
    )

    assert [rule.title for rule, _ in impacts] == ["CLI / command surface", "safety / session behavior"]


def test_unmapped_path_produces_no_candidate():
    assert IMPACT.impacts_for_paths(["tests/test_docs_impact.py"]) == []
