from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from wavebench.services.run_plan import format_run_plan_schema


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "generate_docs.py"
SPEC = importlib.util.spec_from_file_location("wavebench_docs_generation", SCRIPT)
assert SPEC and SPEC.loader
GENERATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GENERATION
SPEC.loader.exec_module(GENERATION)


def test_generated_run_schema_page_embeds_the_machine_schema():
    page = GENERATION.render_run_schema_page()

    assert "Generated run plan schema" in page
    assert format_run_plan_schema() in page


def test_checked_in_run_schema_page_is_current():
    assert GENERATION.main(["--check"]) == 0
