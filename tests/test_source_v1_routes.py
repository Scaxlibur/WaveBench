from __future__ import annotations

from tempfile import TemporaryDirectory
from pathlib import Path

import pytest

from wavebench.errors import ConfigError
from wavebench.instruments.source_extensions import SourceV1WriteRouteId
from wavebench.services.operation_specs import list_operation_specs, require_operation_spec
from wavebench.services.run_plan import ALLOWED_STEP_KINDS, load_run_plan
from wavebench.services.source_service import SourceService
from wavebench.services.source_v1_routes import SOURCE_V1_WRITE_ROUTE_INVENTORY


def test_source_v1_write_inventory_remains_complete_alongside_v2_operation_specs() -> None:
    inventory = SOURCE_V1_WRITE_ROUTE_INVENTORY

    assert tuple(item.route for item in inventory) == tuple(SourceV1WriteRouteId)
    assert all(
        callable(getattr(SourceService, item.route.value.removeprefix("source_service."), None))
        for item in inventory
    )

    source_write_operations = {
        spec.operation
        for spec in list_operation_specs(instrument_kind="source")
        if spec.effect == "write"
    }
    inventoried_operations = {item.operation for item in inventory if item.operation is not None}
    assert inventoried_operations <= source_write_operations
    assert source_write_operations - inventoried_operations == {
        "source.basic_configure_v2",
        "source.output_enable_v2",
        "source.output_disable_v2",
    }
    assert all(require_operation_spec(operation).effect == "write" for operation in inventoried_operations)


def test_source_v1_indirect_write_entries_are_frozen_and_v2_writes_have_no_run_plan_entry() -> None:
    entrypoints = {
        entrypoint
        for item in SOURCE_V1_WRITE_ROUTE_INVENTORY
        for entrypoint in item.entrypoints
    }
    expected_run_steps = {
        "source.arb_load",
        "source.set_func",
        "source.set_freq",
        "source.set_vpp",
        "source.set_duty",
        "source.output",
    }
    assert {item.removeprefix("run-plan.") for item in entrypoints if item.startswith("run-plan.")} == expected_run_steps
    assert {
        kind
        for kind in ALLOWED_STEP_KINDS
        if kind.startswith("source.") and kind != "source.status"
    } == expected_run_steps
    assert {
        spec.operation
        for spec in list_operation_specs(instrument_kind="source")
        if "_v2" in spec.operation and spec.effect == "write"
    } == {
        "source.basic_configure_v2",
        "source.output_enable_v2",
        "source.output_disable_v2",
    }

    with TemporaryDirectory() as tmp:
        for kind in (
            "source.basic_configure_v2",
            "source.output_enable_v2",
            "source.output_disable_v2",
            "source.output_v2",
        ):
            plan_path = Path(tmp) / f"{kind}.toml"
            plan_path.write_text(f'[[steps]]\nkind = "{kind}"\n', encoding="utf-8")
            with pytest.raises(ConfigError, match=f"{kind}.*not supported"):
                load_run_plan(plan_path)
