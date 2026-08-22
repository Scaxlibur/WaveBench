from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from wavebench.services.run_artifacts import RunStepRecord, write_run_files
from wavebench.services.run_plan import load_run_plan
from wavebench.services.source_state import RestorableSourceState


def _plan(directory: Path):
    path = directory / "plan.toml"
    path.write_text('[[steps]]\nkind = "source.status"\n', encoding="utf-8")
    return load_run_plan(path)


def _write(
    directory: Path,
    *,
    source_operations: list[dict[str, object]] | None = None,
) -> bytes:
    run_path = directory / "run.json"
    write_run_files(
        plan=_plan(directory),
        run_json_path=run_path,
        summary_csv_path=directory / "summary.csv",
        status="ok",
        records=[
            RunStepRecord(
                index=0,
                kind="source.status",
                status="ok",
                fields={"channel": 1},
                artifact={"source_status": {"channel": 1, "output": "OFF"}},
            )
        ],
        error=None,
        restore_state=[
            RestorableSourceState(
                channel=1,
                output="OFF",
                function="SIN",
                frequency_hz=1000.0,
                amplitude_vpp=1.0,
                amplitude_unit="VPP",
            )
        ],
        provenance={"schema": "wavebench.run_provenance.v1"},
        source_operations=source_operations,
    )
    return run_path.read_bytes()


def test_empty_source_operation_namespace_preserves_v1_run_json_bytes() -> None:
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        default_bytes = _write(directory)
        explicit_none_bytes = _write(directory, source_operations=None)

        assert default_bytes == explicit_none_bytes
        run = json.loads(default_bytes)
        assert "source_operations" not in run
        assert run["restore"]["source_state_scope"] == "basic"
        assert run["steps"][0]["artifact"]["source_status"] == {
            "channel": 1,
            "output": "OFF",
        }


def test_nonempty_source_operation_namespace_is_additive_to_v1_run_artifacts() -> None:
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        baseline = json.loads(_write(directory))
        enriched = json.loads(
            _write(
                directory,
                source_operations=[
                    {
                        "schema": "wavebench.source.operation.v1",
                        "operation": "source.future_v2",
                    }
                ],
            )
        )

        assert enriched["source_operations"] == [
            {
                "schema": "wavebench.source.operation.v1",
                "operation": "source.future_v2",
            }
        ]
        assert enriched["restore"] == baseline["restore"]
        assert enriched["steps"] == baseline["steps"]


@pytest.mark.parametrize(
    "source_operations",
    [
        [{"operation": "source.future_v2"}],
        [{"schema": "wavebench.source.operation.v1", "operation": "future_v2"}],
    ],
)
def test_source_operation_namespace_rejects_untyped_artifacts(
    source_operations: list[dict[str, object]],
) -> None:
    with TemporaryDirectory() as tmp:
        with pytest.raises((TypeError, ValueError)):
            _write(Path(tmp), source_operations=source_operations)
