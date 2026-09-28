from contextlib import contextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from wavebench.errors import ConfigError
from wavebench.logging import CommandLogger
from wavebench.services.run_artifacts import RunStepRecord
from wavebench.services.run_pipeline import execute_analysis_pipeline as execute_pipeline
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunInstrumentServices, RunService

from test_run_service import fake_capture, make_config, write_plan


def _plan(tmp: str, *, capture_failure: str = "continue", two_analyses: bool = False):
    second = """
[[steps]]
id = "spectrum_second"
kind = "analysis.pipeline"
source = { step = "capture_main" }
operations = [{ op = "measure", metrics = ["voltage_mean_v"] }]
""" if two_analyses else ""
    return load_run_plan(
        write_plan(
            tmp,
            f"""
[[steps]]
id = "capture_main"
kind = "scope.capture"
save_npy = true
on_failure = "{capture_failure}"

[[steps]]
id = "spectrum_main"
kind = "analysis.pipeline"
source = {{ step = "capture_main" }}
operations = [
  {{ op = "measure", metrics = ["voltage_mean_v"] }},
  {{ op = "remove_dc" }},
  {{ op = "fft" }},
  {{ op = "measure", metrics = ["peak_frequency_hz"] }},
  {{ op = "export", name = "spectrum", formats = ["npy", "csv"] }},
]
{second}
""",
        )
    )


class PhaseRunService(RunService):
    def __init__(self, *args, capture_record: RunStepRecord, events: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        self.capture_record = capture_record
        self.events = events
        self.lifecycle_services = RunInstrumentServices()

    def check(self, plan):
        return None

    def _run_safety_guards(self, plan, *, services=None):
        return None

    @contextmanager
    def _run_instrument_services(self, plan):
        self.events.append("session_open")
        try:
            yield self.lifecycle_services
        finally:
            self.events.append("session_and_lease_closed")

    def _run_step(self, plan, step, **kwargs):
        self.events.append(f"hardware:{step.id}")
        return RunStepRecord(
            index=step.index,
            kind=step.kind,
            status=self.capture_record.status,
            fields=step.fields,
            artifact=self.capture_record.artifact,
            id=step.id,
        )


def _capture_record(tmp: str, *, status: str = "ok") -> RunStepRecord:
    capture = fake_capture(tmp, "capture", [])
    return RunStepRecord(
        index=0,
        kind="scope.capture",
        status=status,
        fields={"save_npy": True, "on_failure": "continue"},
        artifact={
            "package": str(capture.package_dir),
            "metadata": str(capture.metadata_path),
            "quality": {"status": "ok", "warnings": []},
        },
        id="capture_main",
    )


def test_analysis_runs_only_after_sessions_and_leases_are_released() -> None:
    with TemporaryDirectory() as tmp:
        events: list[str] = []
        service = PhaseRunService(
            config=make_config(tmp),
            logger=CommandLogger(),
            capture_record=_capture_record(tmp),
            events=events,
        )
        original = execute_pipeline

        def tracked_execute(**kwargs):
            events.append("analysis")
            return original(**kwargs)

        def tracked_restore(*args, **kwargs):
            events.append("restore")
            return None

        with patch(
            "wavebench.services.run_service.restore_source_state",
            side_effect=tracked_restore,
        ), patch(
            "wavebench.services.run_service.execute_analysis_pipeline", side_effect=tracked_execute
        ):
            result = service.run(_plan(tmp))

        assert events == [
            "session_open",
            "hardware:capture_main",
            "restore",
            "session_and_lease_closed",
            "analysis",
        ]
        assert [record.id for record in result.steps] == ["capture_main", "spectrum_main"]
        assert result.steps[1].status == "ok"
        assert (result.run_dir / "processing/01_spectrum_main/manifest.json").is_file()
        assert (result.run_dir / "processing/01_spectrum_main/exports/spectrum.csv").is_file()
        run = json.loads(result.run_json_path.read_text(encoding="utf-8"))
        assert run["status"] == "ok"
        assert run["steps"][0]["id"] == "capture_main"
        assert run["steps"][1]["id"] == "spectrum_main"


def test_failed_capture_expectation_with_continue_can_still_be_analyzed() -> None:
    with TemporaryDirectory() as tmp:
        events: list[str] = []
        service = PhaseRunService(
            config=make_config(tmp),
            logger=CommandLogger(),
            capture_record=_capture_record(tmp, status="failed"),
            events=events,
        )

        result = service.run(_plan(tmp))

        assert [record.status for record in result.steps] == ["failed", "ok"]
        assert result.steps[1].artifact["analysis_pipeline"]["source_status"] == "failed"
        run = json.loads(result.run_json_path.read_text(encoding="utf-8"))
        assert run["status"] == "failed"
        assert "error" not in run


def test_hardware_stop_skips_analysis_suffix() -> None:
    with TemporaryDirectory() as tmp:
        events: list[str] = []
        service = PhaseRunService(
            config=make_config(tmp),
            logger=CommandLogger(),
            capture_record=_capture_record(tmp, status="failed"),
            events=events,
        )
        with patch("wavebench.services.run_service.execute_analysis_pipeline") as execute:
            result = service.run(_plan(tmp, capture_failure="stop"))

        execute.assert_not_called()
        assert len(result.steps) == 1
        run = json.loads(result.run_json_path.read_text(encoding="utf-8"))
        assert run["error"]["code"] == "step_failed"


def test_session_close_failure_skips_analysis_and_is_not_overwritten() -> None:
    with TemporaryDirectory() as tmp:
        events: list[str] = []
        service = PhaseRunService(
            config=make_config(tmp),
            logger=CommandLogger(),
            capture_record=_capture_record(tmp),
            events=events,
        )
        service.lifecycle_services.close_errors.append({
            "operation": "session.close.scope",
            "type": "RuntimeError",
            "error": {
                "schema": "wavebench.error.v1",
                "code": "unexpected_error",
                "type": "RuntimeError",
                "message": "close failed",
                "exit_code": 1,
                "operation": "session.close.scope",
            },
        })

        with patch("wavebench.services.run_service.execute_analysis_pipeline") as execute:
            result = service.run(_plan(tmp))

        execute.assert_not_called()
        run = json.loads(result.run_json_path.read_text(encoding="utf-8"))
        assert run["status"] == "failed"
        assert run["error"]["code"] == "session_close_failed"


def test_restore_failure_occurs_before_and_skips_analysis() -> None:
    with TemporaryDirectory() as tmp:
        events: list[str] = []
        service = PhaseRunService(
            config=make_config(tmp),
            logger=CommandLogger(),
            capture_record=_capture_record(tmp),
            events=events,
        )
        restore_error = {
            "schema": "wavebench.error.v1",
            "code": "restore_failed",
            "type": "ConfigError",
            "message": "restore failed",
            "exit_code": 2,
        }
        with patch(
            "wavebench.services.run_service.restore_source_state",
            return_value=restore_error,
        ), patch("wavebench.services.run_service.execute_analysis_pipeline") as execute:
            try:
                service.run(_plan(tmp))
            except ConfigError:
                pass
            else:  # pragma: no cover - assertion helper without pytest dependency
                raise AssertionError("restore failure should be raised")

        execute.assert_not_called()


def test_analysis_on_failure_controls_only_the_analysis_suffix() -> None:
    for policy, expected_calls in (("stop", 1), ("continue", 2)):
        with TemporaryDirectory() as tmp:
            events: list[str] = []
            plan = _plan(tmp, two_analyses=True)
            plan.steps[1].fields["on_failure"] = policy
            service = PhaseRunService(
                config=make_config(tmp),
                logger=CommandLogger(),
                capture_record=_capture_record(tmp),
                events=events,
            )
            failed = {
                "analysis_pipeline": {
                    "schema": "wavebench.analysis_pipeline.v1",
                    "status": "failed",
                    "error": {"message": "analysis failed"},
                },
                "metrics": {},
            }
            succeeded = {
                "analysis_pipeline": {
                    "schema": "wavebench.analysis_pipeline.v1",
                    "status": "ok",
                },
                "metrics": {"voltage_mean_v": 0.0},
            }
            with patch(
                "wavebench.services.run_service.execute_analysis_pipeline",
                side_effect=[failed, succeeded],
            ) as execute:
                result = service.run(plan)

            assert execute.call_count == expected_calls
            assert len(result.steps) == 1 + expected_calls
            assert events.count("hardware:capture_main") == 1


def test_legacy_plan_does_not_enter_analysis_phase() -> None:
    with TemporaryDirectory() as tmp:
        plan = load_run_plan(
            write_plan(
                tmp,
                """
[[steps]]
kind = "sleep"
duration_s = 0.001
""",
            )
        )
        with patch("wavebench.services.run_service.execute_analysis_pipeline") as execute:
            result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

        execute.assert_not_called()
        run = json.loads(result.run_json_path.read_text(encoding="utf-8"))
        assert len(run["steps"]) == 1
        assert "id" not in run["steps"][0]


def test_missing_filter_dependency_is_rejected_before_instrument_lifecycle() -> None:
    with TemporaryDirectory() as tmp:
        plan = load_run_plan(
            write_plan(
                tmp,
                """
[[steps]]
id = "capture_main"
kind = "scope.capture"
save_npy = true

[[steps]]
kind = "analysis.pipeline"
source = { step = "capture_main" }
operations = [
  { op = "filter", family = "iir", design = "butterworth", response = "lowpass", cutoff_hz = 1000, order = 4, mode = "causal" },
  { op = "export", name = "filtered", formats = ["npy"] },
]
""",
            )
        )
        service = RunService(config=make_config(tmp), logger=CommandLogger())

        with patch(
            "wavebench.services.run_service.ensure_analysis_pipeline_dependencies",
            side_effect=ConfigError(
                "analysis filter requires SciPy; install WaveBench with `.[analysis]`"
            ),
        ), patch.object(service, "_run_instrument_services") as open_services:
            try:
                service.run(plan)
            except ConfigError as exc:
                assert ".[analysis]" in str(exc)
            else:  # pragma: no cover - assertion helper without pytest dependency
                raise AssertionError("missing filter dependency should be rejected")

        open_services.assert_not_called()
        assert not (Path(tmp) / "data" / "runs").exists()
