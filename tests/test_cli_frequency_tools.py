from __future__ import annotations

import json
from pathlib import Path

from wavebench.cli import main


def _run(path: Path, gain: float) -> None:
    path.mkdir()
    (path / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")
    (path / "frequency_response.csv").write_text(
        "index,requested_frequency_hz,requested_vpp,gain_db,status\n"
        f"0,100,0.1,{gain},ok\n",
        encoding="utf-8",
    )


def test_run_compare_cli_writes_json_without_instrument_access(tmp_path: Path, capsys) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    output = tmp_path / "compare.json"
    _run(first, 1.0)
    _run(second, 1.5)

    assert main(["run", "compare", str(first), str(second), "--output", str(output)]) == 0
    captured = capsys.readouterr()
    assert "status=ok" in captured.out
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"].startswith(
        "wavebench.frequency_response_compare"
    )


def test_run_compare_json_format_is_machine_readable(tmp_path: Path, capsys) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _run(first, 1.0)
    _run(second, 1.5)

    assert main(["run", "compare", str(first), str(second), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["comparisons"][0]["summary"]["matched"] == 1


def test_run_resume_cli_is_offline(tmp_path: Path, capsys) -> None:
    plan = tmp_path / "plan.toml"
    plan.write_text(
        """
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
""",
        encoding="utf-8",
    )
    run = tmp_path / "run"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")
    (run / "frequency_response.csv").write_text(
        "index,requested_frequency_hz,requested_vpp,gain_linear,gain_db,status\n"
        "0,100,,2,6,ok\n",
        encoding="utf-8",
    )
    output = tmp_path / "resume.json"

    assert main(["run", "resume", str(run), "--plan", str(plan), "--output", str(output)]) == 0
    assert "pending=1" in capsys.readouterr().out
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["reusable"] == 1
