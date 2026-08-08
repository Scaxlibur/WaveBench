from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from wavebench.cli import main
from wavebench.services.run_artifacts import RunStepRecord
from wavebench.services.run_service import RunResult


def _result(tmp: str, status: str) -> RunResult:
    root = Path(tmp)
    record = RunStepRecord(
        index=0,
        kind="scope.capture",
        status=status,
        fields={},
        artifact={},
    )
    return RunResult(
        run_dir=root / "run",
        run_json_path=root / "run" / "run.json",
        summary_csv_path=root / "run" / "summary.csv",
        steps=[record],
    )


def test_run_plan_returns_nonzero_for_failed_result_and_keeps_paths(capsys) -> None:
    with TemporaryDirectory() as tmp:
        plan_path = Path(tmp) / "plan.toml"
        plan_path.write_text("", encoding="utf-8")
        result = _result(tmp, "failed")
        service = SimpleNamespace(run=lambda _plan: result)
        with patch("wavebench.cli.load_run_plan", return_value=object()), patch(
            "wavebench.cli._load_run_service", return_value=service
        ):
            code = main(["run", "plan", "--plan", str(plan_path)])

        assert code == 2
        output = capsys.readouterr().out
        assert f"run_json={result.run_json_path}" in output
        assert f"summary={result.summary_csv_path}" in output


def test_run_plan_keeps_zero_for_ok_result() -> None:
    with TemporaryDirectory() as tmp:
        plan_path = Path(tmp) / "plan.toml"
        plan_path.write_text("", encoding="utf-8")
        result = _result(tmp, "ok")
        service = SimpleNamespace(run=lambda _plan: result)
        with patch("wavebench.cli.load_run_plan", return_value=object()), patch(
            "wavebench.cli._load_run_service", return_value=service
        ):
            assert main(["run", "plan", "--plan", str(plan_path)]) == 0
