from __future__ import annotations

import shutil
import time
import json
from math import isfinite
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator, Mapping

from wavebench.config import WaveBenchConfig
from wavebench.data.package import new_package_dir, safe_label
from wavebench.data.packages import load_run_package
from wavebench.errors import (
    ConfigError,
    SessionHealthError,
    TransportIOError,
    WaveBenchError,
    error_envelope,
)
from wavebench.instruments.capabilities import require_capabilities
from wavebench.instruments.registry import resolve_instrument_descriptor
from wavebench.logging import CommandLogger
from wavebench.services.power_service import PowerService
from wavebench.services.dmm_service import DmmService
from wavebench.services.frequency_response import (
    analyze_frequency_response_point,
    build_fit_document,
    ensure_fit_dependencies,
    failed_frequency_response_point,
    load_frequency_response_points,
    unwrap_frequency_response_phase,
    write_fit_document,
    write_frequency_response_csv,
)
from wavebench.services.frequency_response_adaptive import select_adaptive_frequency_refinement
from wavebench.services.frequency_response_baseline import (
    apply_frequency_response_baseline,
    write_frequency_response_baseline_json,
)
from wavebench.services.frequency_response_calibration import (
    build_frequency_response_calibration,
    ensure_calibration_dependencies,
    normalize_frequency_response_calibration,
    write_fixed_point_calibration,
    write_frequency_response_calibration_csv,
    write_frequency_response_calibration_json,
)
from wavebench.services.frequency_response_evidence import (
    CAPTURE_SYNC_GRADE,
    acquisition_id,
    annotate_capture_metadata,
    case_id,
    plan_digest,
    signal_level_evidence,
)
from wavebench.services.execution_intent import (
    build_execution_intent,
    verify_execution_intent,
)
from wavebench.services.run_artifacts import RunStepRecord, write_run_files, write_step_record
from wavebench.services.run_analysis import (
    capture_consistency,
    capture_fft_summary,
    evaluate_expect,
    step_status,
)
from wavebench.services.run_plan import RunPlan, RunStep
from wavebench.services.run_restore import restore_source_state, snapshot_source_state
from wavebench.services.run_safety import (
    check_run_plan_safety_limits,
    plan_scope_guard_channels,
    reject_unsupported_steps,
    run_scope_safety_guards,
)
from wavebench.services.resource_lease import (
    ResourceLease,
    ResourceLeaseManager,
    resource_fingerprint,
)
from wavebench.services.scope_service import ScopeService
from wavebench.services.source_service import SourceService
from wavebench.services.source_state import RestorableSourceState
from wavebench.services.state_guard import PowerStateGuard, SourceStateGuard


@dataclass(frozen=True)
class RunResult:
    run_dir: Path
    run_json_path: Path
    summary_csv_path: Path
    steps: list[RunStepRecord]


@dataclass(frozen=True)
class RunPreflightRecord:
    instrument: str
    resource: str
    idn: str


@dataclass(frozen=True)
class RunInstrumentServices:
    scope: ScopeService | None = None
    source: SourceService | None = None
    power: PowerService | None = None
    dmm: DmmService | None = None
    # ExitStack close callbacks append lifecycle failures here without
    # replacing a completed run with an unhandled close exception.
    close_errors: list[dict[str, Any]] = field(default_factory=list, compare=False, repr=False)

    def audit_snapshot(self) -> dict[str, Any] | None:
        """Return transport-native counters without deriving them from logs."""

        instruments: dict[str, dict[str, Any]] = {}
        for name, service in (
            ("scope", self.scope),
            ("source", self.source),
            ("power", self.power),
            ("dmm", self.dmm),
        ):
            if service is None:
                continue
            getter = getattr(service, "audit_snapshot", None)
            if not callable(getter):
                continue
            try:
                snapshot = getter()
            except Exception:  # noqa: BLE001 - audit must not mask run failures
                continue
            if isinstance(snapshot, dict):
                instruments[name] = snapshot
        if not instruments:
            return None

        mutation_writes = 0
        mutation_writes_completed = 0
        for snapshot in instruments.values():
            counters = snapshot.get("counters")
            if not isinstance(counters, dict):
                continue
            mutation_writes += int(counters.get("instrument_mutation_writes", 0))
            mutation_writes_completed += int(
                counters.get("instrument_mutation_writes_completed", 0)
            )
        return {
            "schema": "wavebench.run_instrument_io.v1",
            "coverage": "run_factory_transports",
            "instruments": instruments,
            "instrument_mutation_writes": mutation_writes,
            "instrument_mutation_writes_completed": mutation_writes_completed,
        }

    def state_snapshot(self) -> dict[str, dict[str, dict[str, object]]]:
        snapshots: dict[str, dict[str, dict[str, object]]] = {}
        for name, service in (("source", self.source), ("power", self.power)):
            if service is None:
                continue
            getter = getattr(service, "state_guard_snapshot", None)
            snapshot = getter() if callable(getter) else None
            if isinstance(snapshot, dict) and snapshot:
                snapshots[name] = snapshot
        return snapshots


class _FrequencyResponseExecutionError(Exception):
    """Carry the partial sweep artifact so a fatal source failure remains auditable."""

    def __init__(self, record: RunStepRecord, cause: Exception) -> None:
        self.record = record
        self.cause = cause
        super().__init__(str(cause))


def run_output_base(config: WaveBenchConfig) -> Path:
    return config.output.directory.parent / "runs"


@dataclass
class RunService:
    config: WaveBenchConfig
    logger: CommandLogger
    lease_manager: ResourceLeaseManager | None = None

    def verify(self, plan: RunPlan) -> list[RunPreflightRecord]:
        self.check(plan)
        self._run_safety_guards(plan)
        instruments = self._plan_instruments(plan)
        records: list[RunPreflightRecord] = []
        if "scope" in instruments:
            records.append(
                RunPreflightRecord(
                    instrument="scope",
                    resource=self.config.connection.resource,
                    idn=self._scope_service().idn(),
                )
            )
        if "source" in instruments:
            source = self.config.source
            if source is None or not source.resource:
                raise ConfigError("source resource is required by this run plan")
            records.append(
                RunPreflightRecord(
                    instrument="source",
                    resource=source.resource,
                    idn=self._source_service().idn(),
                )
            )
        if "power" in instruments:
            power = self.config.power
            if power is None or not power.resource:
                raise ConfigError("power resource is required by this run plan")
            records.append(
                RunPreflightRecord(
                    instrument="power",
                    resource=power.resource,
                    idn=self._power_service().idn(),
                )
            )
        if "dmm" in instruments:
            dmm = self.config.dmm
            if dmm is None or not dmm.resource:
                raise ConfigError("dmm resource is required by this run plan")
            records.append(
                RunPreflightRecord(
                    instrument="dmm",
                    resource=dmm.resource,
                    idn=self._dmm_service().idn(),
                )
            )
        return records

    def check(self, plan: RunPlan) -> None:
        check_run_plan_safety_limits(plan, self.config.safety_limits)
        reject_unsupported_steps(plan)
        self._check_frequency_response_baselines(plan)
        self._check_frequency_response_resumes(plan)
        self._check_plan_capabilities(plan)

    def _check_frequency_response_resumes(self, plan: RunPlan) -> None:
        """Validate resume CSVs before any instrument session is opened."""

        plan_hash = plan_digest(plan)
        for step in plan.steps:
            if step.kind != "sweep.frequency_response" or not step.fields.get("resume_from"):
                continue
            requested_amplitudes = step.fields.get("amplitudes_vpp") or [None]
            self._load_frequency_response_resume(
                plan,
                step,
                label=step.fields.get("label", f"frequency_response_{step.index:02d}"),
                requested_amplitudes=requested_amplitudes,
                plan_hash=plan_hash,
            )

    def _check_frequency_response_baselines(self, plan: RunPlan) -> None:
        """Validate referenced baseline evidence offline, before an instrument is opened."""
        for step in plan.steps:
            baseline = step.fields.get("baseline") if step.kind == "sweep.frequency_response" else None
            if not baseline:
                continue
            response = self._load_baseline_response(plan, baseline)
            if response.csv_path is None:
                raise ConfigError(f"baseline response {response.label!r} has no frequency_response.csv")
            groups: dict[float | None, list[float]] = {}
            for row in response.rows:
                if str(row.get("status", "ok")).lower() == "failed":
                    continue
                try:
                    frequency = float(row.get("requested_frequency_hz"))
                except (TypeError, ValueError):
                    continue
                gain = _first_finite_row_value(row, "gain_db_corrected", "gain_db")
                phase = _first_finite_row_value(
                    row, "phase_unwrapped_corrected_deg", "phase_unwrapped_deg"
                )
                mode = baseline.get("mode", "complex_transfer")
                if (mode == "complex_transfer" and (gain is None or phase is None)) or (
                    mode in {"phase_only", "delay_only"} and phase is None
                ):
                    continue
                try:
                    amplitude = float(row["requested_vpp"])
                except (KeyError, TypeError, ValueError):
                    amplitude = None
                if frequency > 0:
                    groups.setdefault(amplitude, []).append(frequency)
            if not groups:
                raise ConfigError(f"baseline response {response.label!r} has no valid frequency rows")
            requested_amplitudes = step.fields.get("amplitudes_vpp") or [None]
            for amplitude in requested_amplitudes:
                values = groups.get(amplitude)
                if values is None and len(groups) == 1:
                    values = next(iter(groups.values()))
                if values is None:
                    choices = ", ".join(str(value) for value in groups)
                    raise ConfigError(
                        f"baseline response {response.label!r} has no requested_vpp slice for "
                        f"{amplitude!r}; available: {choices}"
                    )
                lower, upper = min(values), max(values)
                for frequency in step.fields["frequencies_hz"]:
                    if frequency < lower or frequency > upper:
                        raise ConfigError(
                            f"baseline response {response.label!r} does not cover {frequency:.12g} Hz "
                            f"for requested_vpp {amplitude!r}; valid domain is {lower:.12g}..{upper:.12g} Hz"
                        )

    @staticmethod
    def _load_baseline_response(plan: RunPlan, baseline: dict[str, Any]):
        run_dir = Path(baseline["run_dir"])
        if not run_dir.is_absolute():
            run_dir = plan.path.parent / run_dir
        package = load_run_package(run_dir)
        return package.select_frequency_response(baseline.get("response"))

    def _check_plan_capabilities(self, plan: RunPlan) -> None:
        required: dict[str, set[str]] = {}

        def add(kind: str, *capabilities: str) -> None:
            required.setdefault(kind, set()).update(capabilities)

        for step in plan.steps:
            if step.kind == "scope.auto":
                add("scope", "scope.autoscale")
                if self.config.autoscale.check_errors:
                    add("scope", "scope.errors")
            elif step.kind == "scope.capture":
                add("scope", "scope.idn", "scope.capture_waveform")
                if self.config.scope.check_errors:
                    add("scope", "scope.errors")
                if step.fields.get("screenshot", self.config.output.save_screenshot):
                    add("scope", "scope.screenshot")
                if step.fields.get("autoscale_before_capture") or step.fields.get("auto_recover"):
                    add("scope", "scope.autoscale")
            elif step.kind == "sweep.frequency_response":
                add("scope", "scope.idn", "scope.capture_waveforms")
                add("source", "source.status", "source.set_frequency")
                if step.fields.get("amplitudes_vpp"):
                    add("source", "source.set_amplitude_vpp")
                if step.fields.get("autoscale_each_amplitude"):
                    add("scope", "scope.autoscale")
                source = self.config.source
                if self.config.scope.check_errors:
                    add("scope", "scope.errors")
                if source is not None and source.check_errors:
                    add("source", "source.errors")
                if step.fields.get("screenshot", self.config.output.save_screenshot):
                    add("scope", "scope.screenshot")
                ensure_fit_dependencies(step.fields.get("fit"))
                calibration = step.fields.get("calibration")
                if calibration and calibration.get("enabled", True):
                    ensure_calibration_dependencies()
            elif step.kind == "source.status":
                add("source", "source.status")
            elif step.kind == "source.set_freq":
                add("source", "source.set_frequency")
                source = self.config.source
                if source is not None and source.settle_ms_after_set_frequency:
                    add("source", "source.status")
                if source is not None and source.check_errors:
                    add("source", "source.errors")
            elif step.kind == "source.arb_load":
                add("source", "source.arbitrary_upload")
            elif step.kind == "source.set_func":
                add("source", "source.set_function")
            elif step.kind == "source.set_vpp":
                add("source", "source.set_amplitude_vpp")
            elif step.kind == "source.set_duty":
                add("source", "source.set_square_duty_cycle")
            elif step.kind == "source.output":
                add("source", "source.output")
                if step.fields["state"] == "on":
                    add("source", "source.status")
            elif step.kind == "power.status":
                add("power", "power.status")
            elif step.kind == "power.set":
                add("power", "power.set_voltage_current_limit")
            elif step.kind == "power.output":
                add("power", "power.output")
                if step.fields["state"] == "on":
                    add("power", "power.status", "power.protection")
            elif step.kind == "dmm.read":
                add("dmm", "dmm.read")

            gate = step.fields.get("safety_gate")
            if isinstance(gate, dict) and gate.get("enabled"):
                if (
                    gate.get("source_channels")
                    or step.kind.startswith("source.")
                    or step.kind == "sweep.frequency_response"
                    or plan.restore.source_state
                ):
                    add("source", "source.output")
                if gate.get("power_channels") or step.kind.startswith("power."):
                    add("power", "power.output")

        if plan.restore.source_state:
            add(
                "source",
                "source.status",
                "source.set_function",
                "source.set_amplitude_vpp",
                "source.set_frequency",
                "source.set_square_duty_cycle",
                "source.output",
            )
        if plan.safety.safety_gate:
            if plan.safety.off_source_channels or any(
                item.kind.startswith("source.") or item.kind == "sweep.frequency_response"
                for item in plan.steps
            ):
                add("source", "source.output")
            if plan.safety.off_power_channels or any(
                item.kind.startswith("power.") for item in plan.steps
            ):
                add("power", "power.output")
        if plan_scope_guard_channels(plan, self.config.scope.default_channel):
            add("scope", "scope.channel_coupling")

        for kind, capabilities in required.items():
            driver_reference = self._driver_reference(kind)
            descriptor = resolve_instrument_descriptor(
                driver_reference,
                expected_kind=kind,
            )
            require_capabilities(
                descriptor,
                capabilities,
                operation=f"run plan {kind}",
            )

    def _driver_reference(self, kind: str) -> str:
        if kind == "scope":
            return self.config.scope.driver
        config = getattr(self.config, kind)
        if config is None or not config.resource:
            raise ConfigError(f"{kind} resource is required by this run plan")
        return config.driver

    def _plan_instruments(self, plan: RunPlan) -> set[str]:
        instruments = {step.kind.split(".", 1)[0] for step in plan.steps if "." in step.kind}
        instruments.discard("sleep")
        if "sweep" in instruments:
            instruments.discard("sweep")
            instruments.update({"source", "scope"})
        if plan.restore.source_state:
            instruments.add("source")
        if plan.safety.require_scope_coupling_not:
            instruments.add("scope")
        if plan_scope_guard_channels(plan, self.config.scope.default_channel):
            instruments.add("scope")
        if plan.safety.off_source_channels:
            instruments.add("source")
        if plan.safety.off_power_channels:
            instruments.add("power")
        for step in plan.steps:
            gate = step.fields.get("safety_gate")
            if not isinstance(gate, dict) or not gate.get("enabled"):
                continue
            if gate.get("source_channels"):
                instruments.add("source")
            if gate.get("power_channels"):
                instruments.add("power")
        return instruments

    def run(
        self,
        plan: RunPlan,
        *,
        execution_intent: Mapping[str, Any] | None = None,
    ) -> RunResult:
        self.check(plan)
        intent = build_execution_intent(plan, self.config)
        if execution_intent is not None:
            intent = verify_execution_intent(execution_intent, plan, self.config)
        plan_hash = intent.plan_digest
        with self._run_instrument_services(plan) as services:
            self._run_safety_guards(plan, services=services)
            run_dir = new_package_dir(run_output_base(self.config), plan.label)
            steps_dir = run_dir / "steps"
            steps_dir.mkdir(parents=True, exist_ok=False)
            if plan.path.exists():
                shutil.copyfile(plan.path, run_dir / "plan.toml")

            records: list[RunStepRecord] = []
            run_json_path = run_dir / "run.json"
            summary_csv_path = run_dir / "summary.csv"
            restore_state: list[RestorableSourceState] | None = None
            restore_error: dict[str, Any] | None = None
            run_failure: dict[str, Any] | None = None
            safety_gate_step: RunStep | None = None
            safety_gate_config: dict[str, Any] | None = None
            provenance = {
                "schema": "wavebench.run_provenance.v1",
                "plan_hash": plan_hash,
                "execution_intent": intent.as_dict(),
                "frequency_response": {
                    "schema": "wavebench.frequency_response_evidence.v1",
                    "capture_sync_grade": CAPTURE_SYNC_GRADE,
                },
            }

            def refresh_provenance() -> None:
                instrument_io = services.audit_snapshot()
                if instrument_io is not None:
                    provenance["instrument_io"] = instrument_io
                state_snapshot = services.state_snapshot()
                if state_snapshot:
                    provenance["state_guard"] = {
                        "schema": "wavebench.state_guard.v1",
                        "expected": state_snapshot,
                    }

            try:
                restore_state = snapshot_source_state(
                    plan,
                    source_service_factory=lambda: self._source_service(services=services),
                )
                for step in plan.steps:
                    step_failure: BaseException | None = None
                    try:
                        record = self._run_step(
                            plan, step, run_dir=run_dir, services=services, plan_hash=plan_hash
                        )
                    except _FrequencyResponseExecutionError as exc:
                        if not isinstance(exc.cause, (TransportIOError, SessionHealthError)):
                            raise
                        step_failure = exc.cause
                        record = exc.record
                        record = replace(
                            record,
                            artifact={
                                **record.artifact,
                                "error": error_envelope(
                                    exc.cause,
                                    operation=f"run.step.{step.kind}",
                                ),
                            },
                        )
                    except (TransportIOError, SessionHealthError) as exc:
                        step_failure = exc
                        record = RunStepRecord(
                            index=step.index,
                            kind=step.kind,
                            status="failed",
                            fields=step.fields,
                            artifact={
                                "error": error_envelope(
                                    exc,
                                    operation=f"run.step.{step.kind}",
                                )
                            },
                        )
                    safety_gate = self._safety_gate_for_step(plan, step)
                    gate_triggered = safety_gate["enabled"] and record.status in {
                        "failed",
                        "warning",
                    }
                    trigger_status = record.status
                    if gate_triggered:
                        try:
                            gate_result = self._apply_safety_gate(step, safety_gate, services=services)
                        except Exception as exc:  # noqa: BLE001 - retain gate failure in run artifact
                            payload = error_envelope(
                                exc,
                                operation=f"safety_gate.step.{step.index}",
                            )
                            gate_result = {
                                "status": "failed",
                                "error": payload,
                            }
                        record = replace(
                            record,
                            status="failed",
                            artifact={**record.artifact, "safety_gate": gate_result},
                        )
                    records.append(record)
                    write_step_record(steps_dir, record)
                    self._update_frequency_responses_manifest(run_dir, record)
                    if gate_triggered:
                        safety_gate_step = step
                        safety_gate_config = safety_gate
                        run_failure = {
                            "type": "SafetyGateFailure",
                            "code": "safety_gate_failed",
                            "message": f"safety gate stopped run step {step.index} ({step.kind})",
                            "step_index": step.index,
                            "step_kind": step.kind,
                            "policy": "stop",
                            "trigger_status": trigger_status,
                            "safety_gate": gate_result,
                        }
                        if step_failure is not None:
                            run_failure["step_error"] = error_envelope(
                                step_failure,
                                operation=f"run.step.{step.kind}",
                            )
                        break
                    if step_failure is not None:
                        failure_payload = error_envelope(
                            step_failure,
                            operation=f"run.step.{step.kind}",
                        )
                        if step.fields.get("on_failure", "stop") == "stop":
                            run_failure = {
                                "type": "StepFailure",
                                "code": "step_failed",
                                "message": f"run step {step.index} ({step.kind}) failed",
                                "step_index": step.index,
                                "step_kind": step.kind,
                                "policy": "stop",
                                "error": failure_payload,
                            }
                            break
                        continue
                    if record.status == "failed" and step.fields.get("on_failure", "stop") == "stop":
                        run_failure = {
                            "type": "StepFailure",
                            "code": "step_failed",
                            "message": f"run step {step.index} ({step.kind}) failed",
                            "step_index": step.index,
                            "step_kind": step.kind,
                            "policy": "stop",
                        }
                        break
            except KeyboardInterrupt as exc:
                restore_error = restore_source_state(
                    restore_state,
                    source_service_factory=lambda: self._source_service(services=services),
                )
                refresh_provenance()
                interruption_error: dict[str, Any] = {
                    "type": "KeyboardInterrupt",
                    "message": str(exc) or "run interrupted by user",
                }
                if restore_error is not None:
                    interruption_error["cause"] = dict(interruption_error)
                    interruption_error["type"] = "RestoreError"
                    interruption_error["message"] = _restore_error_message(restore_error)
                write_run_files(
                    plan=plan,
                    run_json_path=run_json_path,
                    summary_csv_path=summary_csv_path,
                    status="failed",
                    records=records,
                    error=interruption_error,
                    restore_state=restore_state,
                    restore_error=restore_error,
                    provenance=provenance,
                )
                if restore_error is not None:
                    raise ConfigError(
                        "run plan source state restore failed: "
                        + _restore_error_message(restore_error)
                    ) from exc
                raise
            except Exception as exc:
                failure = exc
                if isinstance(exc, _FrequencyResponseExecutionError):
                    records.append(exc.record)
                    write_step_record(steps_dir, exc.record)
                    self._update_frequency_responses_manifest(run_dir, exc.record)
                    failure = exc.cause
                restore_error = restore_source_state(
                    restore_state,
                    source_service_factory=lambda: self._source_service(services=services),
                )
                refresh_provenance()
                failure_error: dict[str, Any]
                if isinstance(failure, WaveBenchError):
                    failure_error = error_envelope(failure)
                else:
                    failure_error = {"type": type(failure).__name__, "message": str(failure)}
                write_run_files(
                    plan=plan,
                    run_json_path=run_json_path,
                    summary_csv_path=summary_csv_path,
                    status="failed",
                    records=records,
                    error=failure_error,
                    restore_state=restore_state,
                    restore_error=restore_error,
                    provenance=provenance,
                )
                if isinstance(exc, _FrequencyResponseExecutionError):
                    raise failure from None
                raise

            restore_error = restore_source_state(
                restore_state,
                source_service_factory=lambda: self._source_service(services=services),
            )
            if (
                safety_gate_step is not None
                and safety_gate_config is not None
                and restore_state is not None
            ):
                try:
                    post_restore_gate = self._apply_safety_gate(
                        safety_gate_step,
                        safety_gate_config,
                        services=services,
                    )
                except Exception as exc:  # noqa: BLE001 - retain final OFF evidence
                    payload = error_envelope(
                        exc,
                        operation=f"safety_gate.post_restore.{safety_gate_step.index}",
                    )
                    post_restore_gate = {
                        "status": "failed",
                        "error": payload,
                    }
                if run_failure is not None:
                    gate_error = run_failure.get("safety_gate")
                    if isinstance(gate_error, dict):
                        gate_error["post_restore"] = post_restore_gate
            if restore_error is not None:
                restore_failure: dict[str, Any] = {
                    "type": "ConfigError",
                    "code": "restore_failed",
                    "message": "source state restore failed",
                }
                if run_failure is not None:
                    restore_failure["cause"] = run_failure
                refresh_provenance()
                write_run_files(
                    plan=plan,
                    run_json_path=run_json_path,
                    summary_csv_path=summary_csv_path,
                    status="failed",
                    records=records,
                    error=restore_failure,
                    restore_state=restore_state,
                    restore_error=restore_error,
                    provenance=provenance,
                )
                raise ConfigError("run plan source state restore failed: " + restore_error["message"])

            run_status = "failed" if any(record.status == "failed" for record in records) else "ok"
            refresh_provenance()
            write_run_files(
                plan=plan,
                run_json_path=run_json_path,
                summary_csv_path=summary_csv_path,
                status=run_status,
                records=records,
                error=run_failure,
                restore_state=restore_state,
                restore_error=None,
                provenance=provenance,
            )
            result = RunResult(
                run_dir=run_dir,
                run_json_path=run_json_path,
                summary_csv_path=summary_csv_path,
                steps=records,
            )

        close_errors = getattr(services, "close_errors", [])
        if close_errors:
            close_failure: dict[str, Any] = {
                "type": "SessionCloseError",
                "code": "session_close_failed",
                "message": "one or more instrument session close operations failed",
                "errors": close_errors,
            }
            if run_failure is not None:
                close_failure["cause"] = run_failure
            provenance["session_lifecycle"] = {
                "close_errors": close_errors,
            }
            write_run_files(
                plan=plan,
                run_json_path=run_json_path,
                summary_csv_path=summary_csv_path,
                status="failed",
                records=records,
                error=close_failure,
                restore_state=restore_state,
                restore_error=restore_error,
                provenance=provenance,
            )
        return result

    def _safety_gate_for_step(self, plan: RunPlan, step: RunStep) -> dict[str, Any]:
        local = step.fields.get("safety_gate")
        local_gate = local if isinstance(local, dict) else {"enabled": False}
        enabled = bool(plan.safety.safety_gate or local_gate.get("enabled", False))
        source_channels = tuple(
            int(channel)
            for channel in (
                local_gate.get("source_channels") or plan.safety.off_source_channels
            )
        )
        power_channels = tuple(
            int(channel)
            for channel in (
                local_gate.get("power_channels") or plan.safety.off_power_channels
            )
        )
        if (
            enabled
            and not source_channels
            and not power_channels
            and local_gate.get("enabled", False)
            and not plan.safety.safety_gate
        ):
            source_channels = tuple(self._inferred_source_gate_channels(plan, step))
            power_channels = tuple(self._inferred_power_gate_channels(plan, step))
        return {
            "enabled": enabled,
            "source_channels": list(source_channels),
            "power_channels": list(power_channels),
        }

    def _inferred_source_gate_channels(self, plan: RunPlan, step: RunStep) -> list[int]:
        channels: list[int] = []

        def add(value: Any) -> None:
            if value is None:
                return
            channel = int(value)
            if channel not in channels:
                channels.append(channel)

        if step.kind.startswith("source.") or step.kind == "sweep.frequency_response":
            add(step.fields.get("source_channel") or step.fields.get("channel"))
        for item in plan.steps:
            if item.kind.startswith("source.") or item.kind == "sweep.frequency_response":
                add(item.fields.get("source_channel") or item.fields.get("channel"))
        for channel in plan.restore.source_channels:
            add(channel)
        if not channels and "source" in self._plan_instruments(plan) and self.config.source is not None:
            add(self.config.source.default_channel)
        return channels

    def _inferred_power_gate_channels(self, plan: RunPlan, step: RunStep) -> list[int]:
        channels: list[int] = []

        def add(value: Any) -> None:
            if value is None:
                return
            channel = int(value)
            if channel not in channels:
                channels.append(channel)

        if step.kind.startswith("power."):
            add(step.fields.get("channel"))
        for item in plan.steps:
            if item.kind.startswith("power."):
                add(item.fields.get("channel"))
        if not channels and "power" in self._plan_instruments(plan) and self.config.power is not None:
            add(self.config.power.default_channel)
        return channels

    def _apply_safety_gate(
        self,
        step: RunStep,
        gate: dict[str, Any],
        *,
        services: RunInstrumentServices | None = None,
    ) -> dict[str, Any]:
        source_channels = [int(channel) for channel in gate.get("source_channels", [])]
        power_channels = [int(channel) for channel in gate.get("power_channels", [])]
        if not source_channels and not power_channels:
            raise ConfigError(
                f"step {step.index} safety gate has no authorized OFF targets"
            )
        result: dict[str, Any] = {
            "status": "ok",
            "source_channels": source_channels,
            "power_channels": power_channels,
            "actions": [],
        }
        errors: list[dict[str, Any]] = []
        for channel in source_channels:
            try:
                status = self._source_service(services=services).set_output(
                    channel=channel,
                    enabled=False,
                )
                result["actions"].append(
                    {
                        "instrument": "source",
                        "channel": channel,
                        "state": "off",
                        "status": _status_payload(status),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - attempt every authorized OFF target
                payload = error_envelope(
                    exc,
                    operation=f"safety_gate.source.{channel}.off",
                )
                errors.append(
                    {
                        "instrument": "source",
                        "channel": channel,
                        "type": type(exc).__name__,
                        "message": payload["message"],
                        "error": payload,
                    }
                )
        for channel in power_channels:
            try:
                status = self._power_service(services=services).set_output(
                    channel=channel,
                    enabled=False,
                )
                result["actions"].append(
                    {
                        "instrument": "power",
                        "channel": channel,
                        "state": "off",
                        "status": _status_payload(status),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - attempt every authorized OFF target
                payload = error_envelope(
                    exc,
                    operation=f"safety_gate.power.{channel}.off",
                )
                errors.append(
                    {
                        "instrument": "power",
                        "channel": channel,
                        "type": type(exc).__name__,
                        "message": payload["message"],
                        "error": payload,
                    }
                )
        if errors:
            result["status"] = "failed"
            result["errors"] = errors
        return result

    def _run_safety_guards(
        self,
        plan: RunPlan,
        *,
        services: RunInstrumentServices | None = None,
    ) -> None:
        run_scope_safety_guards(
            plan,
            scope_service=self._scope_service(services=services),
            default_channel=self.config.scope.default_channel,
        )

    def _run_step(
        self,
        plan: RunPlan,
        step: RunStep,
        *,
        run_dir: Path,
        services: RunInstrumentServices | None = None,
        plan_hash: str = "",
    ) -> RunStepRecord:
        if step.kind == "power.status":
            status = self._power_service(services=services).status(channel=step.fields.get("channel"))
            artifact = {"power_status": _status_payload(status)}
        elif step.kind == "power.set":
            status = self._power_service(services=services).set_voltage_current_limit(
                channel=step.fields.get("channel"),
                voltage_v=step.fields["voltage_v"],
                current_limit_a=step.fields["current_limit_a"],
            )
            artifact = {"power_status": _status_payload(status)}
        elif step.kind == "power.output":
            status = self._power_service(services=services).set_output(
                channel=step.fields.get("channel"),
                enabled=step.fields["state"] == "on",
            )
            artifact = {"power_status": _status_payload(status)}
        elif step.kind == "source.status":
            status = self._source_service(services=services).status(channel=step.fields.get("channel"))
            artifact = {"source_status": _status_payload(status)}
        elif step.kind == "source.set_freq":
            status = self._source_service(services=services).set_frequency(
                channel=step.fields.get("channel"),
                value_hz=step.fields["frequency_hz"],
            )
            artifact = {"source_status": _status_payload(status)}
        elif step.kind == "source.arb_load":
            waveform_path = Path(step.fields["file"])
            if not waveform_path.is_absolute():
                waveform_path = plan.path.parent / waveform_path
            status = self._source_service(services=services).upload_arbitrary_waveform(
                channel=step.fields.get("channel"),
                file_path=str(waveform_path),
                playback_frequency_hz=step.fields["frequency_hz"],
                amplitude_vpp=step.fields["amplitude_vpp"],
                offset_v=step.fields.get("offset_v", 0.0),
                sample_rate_hz=step.fields.get("sample_rate_hz"),
                max_points=step.fields.get("max_points", 16384),
                byte_order=step.fields.get("byte_order", "little"),
                output_on=step.fields.get("output_on", False),
            )
            artifact = {"source_status": _status_payload(status)}
        elif step.kind == "source.set_func":
            status = self._source_service(services=services).set_function(
                channel=step.fields.get("channel"),
                function=step.fields["function"],
            )
            artifact = {"source_status": _status_payload(status)}
        elif step.kind == "source.set_vpp":
            status = self._source_service(services=services).set_amplitude_vpp(
                channel=step.fields.get("channel"),
                value_vpp=step.fields["value_vpp"],
            )
            artifact = {"source_status": _status_payload(status)}
        elif step.kind == "source.set_duty":
            status = self._source_service(services=services).set_square_duty_cycle(
                channel=step.fields.get("channel"),
                duty_percent=step.fields["duty_percent"],
            )
            artifact = {"source_status": _status_payload(status)}
        elif step.kind == "source.output":
            status = self._source_service(services=services).set_output(
                channel=step.fields.get("channel"),
                enabled=step.fields["state"] == "on",
            )
            artifact = {"source_status": _status_payload(status)}
        elif step.kind == "scope.auto":
            self._scope_service(services=services).autoscale()
            artifact = {"autoscale": "completed"}
        elif step.kind == "scope.capture":
            artifact = self._run_scope_capture_step(plan, step, services=services)
        elif step.kind == "sweep.frequency_response":
            artifact = self._run_frequency_response_step(
                plan,
                step,
                run_dir=run_dir,
                multiple_responses=sum(
                    item.kind == "sweep.frequency_response" for item in plan.steps
                ) > 1,
                services=services,
                plan_hash=plan_hash,
            )
        elif step.kind == "dmm.read":
            reading = self._dmm_service(services=services).read(function=step.fields.get("function", "dcv"))
            reading_payload = _status_payload(reading)
            artifact = {"dmm_reading": reading_payload}
            if "expect" in step.fields:
                artifact["expect"] = evaluate_expect(reading_payload, step.fields["expect"])
        elif step.kind == "sleep":
            time.sleep(step.fields["duration_s"])
            artifact = {"duration_s": step.fields["duration_s"]}
        else:  # pragma: no cover - guarded before execution
            raise ConfigError(f"unsupported run step kind: {step.kind}")
        return RunStepRecord(
            index=step.index,
            kind=step.kind,
            status=step_status(artifact),
            fields=step.fields,
            artifact=artifact,
        )

    def _run_frequency_response_step(
        self,
        plan: RunPlan,
        step: RunStep,
        *,
        run_dir: Path,
        multiple_responses: bool,
        services: RunInstrumentServices | None = None,
        plan_hash: str = "",
    ) -> dict[str, Any]:
        source_channel = step.fields.get("source_channel")
        source = self._source_service(services=services)
        label = step.fields.get("label", f"frequency_response_{step.index:02d}")
        response_dir = self._frequency_response_directory(
            run_dir, step.index, label, multiple_responses=multiple_responses
        )
        response_dir.mkdir(parents=True, exist_ok=True)
        csv_path = response_dir / "frequency_response.csv"
        fit_path = response_dir / "frequency_response_fit.json"
        baseline_path = response_dir / "frequency_response_baseline.json"
        calibration_csv_path = response_dir / "frequency_response_calibration.csv"
        calibration_json_path = response_dir / "frequency_response_calibration.json"
        points = []
        reference_channel = step.fields["reference_channel"]
        response_channel = step.fields["response_channel"]
        tolerance = step.fields.get(
            "frequency_tolerance", self.config.waveform.frequency_tolerance_ratio
        )
        adaptive_config = step.fields.get("adaptive")
        adaptive_summary: dict[str, Any] | None = None
        baseline_document: dict[str, Any] | None = None
        baseline_response = (
            self._load_baseline_response(plan, step.fields["baseline"])
            if step.fields.get("baseline")
            else None
        )

        try:
            source_status = source.status(channel=source_channel)
            if str(source_status.output).strip().upper() != "ON":
                raise ConfigError(
                    "frequency response requires the source output to be ON; "
                    "enable it explicitly with a source.output step before the sweep"
                )
        except Exception as exc:
            write_frequency_response_csv(csv_path, points)
            raise self._frequency_response_execution_error(
                step,
                exc,
                points=points,
                csv_path=csv_path,
                fit_path=None,
                label=label,
                response_dir=response_dir,
                source_channel=source_channel,
                reference_channel=reference_channel,
                response_channel=response_channel,
            ) from exc

        requested_amplitudes = step.fields.get("amplitudes_vpp") or [None]
        points, resumed_keys, resume_summary = self._load_frequency_response_resume(
            plan,
            step,
            label=label,
            requested_amplitudes=requested_amplitudes,
            plan_hash=plan_hash,
        )
        point_index = max((point.index for point in points), default=-1) + 1
        pending = [
            (frequency_hz, 0, None, None)
            for frequency_hz in step.fields["frequencies_hz"]
        ]
        refinement_levels = 0
        budget_limited = False
        while pending:
            current_level = pending[0][1]
            for amplitude_index, requested_vpp in enumerate(requested_amplitudes):
                if requested_vpp is not None:
                    try:
                        source_status = source.set_amplitude_vpp(
                            channel=source_channel, value_vpp=requested_vpp
                        )
                    except Exception as exc:
                        write_frequency_response_csv(csv_path, points)
                        raise self._frequency_response_execution_error(
                            step, exc, points=points, csv_path=csv_path, fit_path=None,
                            label=label, response_dir=response_dir, source_channel=source_channel,
                            reference_channel=reference_channel, response_channel=response_channel,
                        ) from exc
                    if str(source_status.output).strip().upper() != "ON":
                        error = ConfigError(
                            f"source output is {source_status.output} after setting {requested_vpp:.12g} Vpp"
                        )
                        write_frequency_response_csv(csv_path, points)
                        raise self._frequency_response_execution_error(
                            step, error, points=points, csv_path=csv_path, fit_path=None,
                            label=label, response_dir=response_dir, source_channel=source_channel,
                            reference_channel=reference_channel, response_channel=response_channel,
                        ) from error
                for frequency_index, (frequency_hz, adaptive_level, parent_start, parent_stop) in enumerate(pending):
                    point_key = self._frequency_response_point_key(
                        amplitude_index, requested_vpp, frequency_hz
                    )
                    if point_key in resumed_keys:
                        continue
                    point_case_id = case_id(
                        plan_name=plan.name,
                        step_index=step.index,
                        label=label,
                        frequency_hz=frequency_hz,
                        requested_vpp=requested_vpp,
                        reference_channel=reference_channel,
                        response_channel=response_channel,
                    )
                    try:
                        source_status = source.set_frequency(channel=source_channel, value_hz=frequency_hz)
                    except Exception as exc:
                        points.append(
                            self._annotate_frequency_point(
                                failed_frequency_response_point(
                                    index=point_index,
                                    amplitude_index=amplitude_index,
                                    requested_vpp=requested_vpp,
                                    requested_frequency_hz=frequency_hz,
                                    error=exc,
                                    adaptive_level=adaptive_level,
                                    adaptive_parent_start_hz=parent_start,
                                    adaptive_parent_stop_hz=parent_stop,
                                ),
                                case_id_value=point_case_id,
                                plan_hash=plan_hash,
                                reference_channel=reference_channel,
                                response_channel=response_channel,
                                requested_frequency_hz=frequency_hz,
                                requested_vpp=requested_vpp,
                            )
                        )
                        write_frequency_response_csv(csv_path, points)
                        raise self._frequency_response_execution_error(
                            step, exc, points=points, csv_path=csv_path, fit_path=None,
                            label=label, response_dir=response_dir, source_channel=source_channel,
                            reference_channel=reference_channel, response_channel=response_channel,
                        ) from exc
                    if str(source_status.output).strip().upper() != "ON":
                        error = ConfigError(
                            f"source output is {source_status.output} after setting {frequency_hz:.12g} Hz"
                        )
                        points.append(
                            self._annotate_frequency_point(
                                failed_frequency_response_point(
                                    index=point_index,
                                    amplitude_index=amplitude_index,
                                    requested_vpp=requested_vpp,
                                    requested_frequency_hz=frequency_hz,
                                    error=error,
                                    adaptive_level=adaptive_level,
                                    adaptive_parent_start_hz=parent_start,
                                    adaptive_parent_stop_hz=parent_stop,
                                ),
                                case_id_value=point_case_id,
                                plan_hash=plan_hash,
                                reference_channel=reference_channel,
                                response_channel=response_channel,
                                requested_frequency_hz=frequency_hz,
                                requested_vpp=requested_vpp,
                            )
                        )
                        write_frequency_response_csv(csv_path, points)
                        raise self._frequency_response_execution_error(
                            step, error, points=points, csv_path=csv_path, fit_path=None,
                            label=label, response_dir=response_dir, source_channel=source_channel,
                            reference_channel=reference_channel, response_channel=response_channel,
                        ) from error
                    if step.fields["settle_s"]:
                        time.sleep(step.fields["settle_s"])
                    scope = self._scope_service_for_frequency_response(
                        step, frequency_hz=frequency_hz, services=services
                    )
                    if current_level == 0 and frequency_index == 0 and step.fields.get("autoscale_each_amplitude"):
                        scope.autoscale()
                        if step.fields["settle_s"]:
                            time.sleep(step.fields["settle_s"])
                    amplitude_label = (
                        f"{label}_{point_index:03d}_{frequency_hz:.12g}hz"
                        if requested_vpp is None
                        else f"{label}_a{amplitude_index:02d}_{requested_vpp:.12g}vpp_"
                        f"l{adaptive_level}_{frequency_index:03d}_{frequency_hz:.12g}hz"
                    )
                    try:
                        capture = scope.capture_waveforms(
                            channels=[reference_channel, response_channel], label=amplitude_label
                        )
                        point = analyze_frequency_response_point(
                            index=point_index, amplitude_index=amplitude_index, requested_vpp=requested_vpp,
                            requested_frequency_hz=frequency_hz,
                            reference_waveform=capture.waveforms[reference_channel],
                            response_waveform=capture.waveforms[response_channel],
                            frequency_tolerance_ratio=tolerance, capture_package=str(capture.package_dir),
                            metadata_path=str(capture.metadata_path), adaptive_level=adaptive_level,
                            min_signal_vpp=step.fields["min_signal_vpp"],
                            adaptive_parent_start_hz=parent_start, adaptive_parent_stop_hz=parent_stop,
                        )
                        point = self._annotate_frequency_point(
                            point,
                            case_id_value=point_case_id,
                            plan_hash=plan_hash,
                            reference_channel=reference_channel,
                            response_channel=response_channel,
                            requested_frequency_hz=frequency_hz,
                            requested_vpp=requested_vpp,
                        )
                        if point.status == "warning" and step.fields["retry_warning_with_autoscale"]:
                            initial_point = point
                            try:
                                scope.autoscale()
                                if step.fields["settle_s"]:
                                    time.sleep(step.fields["settle_s"])
                                retry_capture = scope.capture_waveforms(
                                    channels=[reference_channel, response_channel], label=f"{amplitude_label}_retry1"
                                )
                                retry_point = analyze_frequency_response_point(
                                    index=point_index, amplitude_index=amplitude_index, requested_vpp=requested_vpp,
                                    requested_frequency_hz=frequency_hz,
                                    reference_waveform=retry_capture.waveforms[reference_channel],
                                    response_waveform=retry_capture.waveforms[response_channel],
                                    frequency_tolerance_ratio=tolerance,
                                    min_signal_vpp=step.fields["min_signal_vpp"],
                                    capture_package=str(retry_capture.package_dir), metadata_path=str(retry_capture.metadata_path),
                                    adaptive_level=adaptive_level, adaptive_parent_start_hz=parent_start,
                                    adaptive_parent_stop_hz=parent_stop,
                                )
                                retry_point = self._annotate_frequency_point(
                                    retry_point,
                                    case_id_value=point_case_id,
                                    plan_hash=plan_hash,
                                    reference_channel=reference_channel,
                                    response_channel=response_channel,
                                    requested_frequency_hz=frequency_hz,
                                    requested_vpp=requested_vpp,
                                    retry_of=getattr(initial_point, "acquisition_id", None),
                                )
                                point = replace(
                                    retry_point,
                                    status="failed" if retry_point.status == "warning" else retry_point.status,
                                    quality_retry_count=1,
                                    initial_warnings=initial_point.warnings,
                                    initial_capture_package=initial_point.capture_package,
                                    initial_metadata_path=initial_point.metadata_path,
                                    retry_capture_package=retry_point.capture_package,
                                    retry_metadata_path=retry_point.metadata_path,
                                    error=(
                                        "quality_retry_exhausted: initial warnings="
                                        + " | ".join(initial_point.warnings)
                                        + "; retry warnings=" + " | ".join(retry_point.warnings)
                                    ) if retry_point.status == "warning" else retry_point.error,
                                )
                            except Exception as retry_exc:  # noqa: BLE001 - retain the first capture evidence
                                failed_retry_capture = self._find_failed_frequency_capture(
                                    f"{amplitude_label}_retry1"
                                )
                                point = replace(
                                    initial_point,
                                    status="failed",
                                    quality_retry_count=1,
                                    initial_warnings=initial_point.warnings,
                                    initial_capture_package=initial_point.capture_package,
                                    initial_metadata_path=initial_point.metadata_path,
                                    retry_capture_package=(
                                        str(failed_retry_capture) if failed_retry_capture is not None else ""
                                    ),
                                    retry_metadata_path=(
                                        str(failed_retry_capture / "metadata.partial.json")
                                        if failed_retry_capture is not None
                                        else ""
                                    ),
                                    error=f"quality_retry_failed: {type(retry_exc).__name__}: {retry_exc}",
                                )
                        points.append(point)
                    except Exception as exc:  # noqa: BLE001 - retain failed points and continue the sweep
                        failed_capture = self._find_failed_frequency_capture(amplitude_label)
                        failed_point = failed_frequency_response_point(
                            index=point_index, amplitude_index=amplitude_index, requested_vpp=requested_vpp,
                            requested_frequency_hz=frequency_hz, error=exc, adaptive_level=adaptive_level,
                            adaptive_parent_start_hz=parent_start, adaptive_parent_stop_hz=parent_stop,
                        )
                        if failed_capture is not None:
                            failed_point = replace(
                                failed_point,
                                capture_package=str(failed_capture),
                                metadata_path=str(failed_capture / "metadata.partial.json"),
                            )
                        points.append(
                            self._annotate_frequency_point(
                                failed_point,
                                case_id_value=point_case_id,
                                plan_hash=plan_hash,
                                reference_channel=reference_channel,
                                response_channel=response_channel,
                                requested_frequency_hz=frequency_hz,
                                requested_vpp=requested_vpp,
                            )
                        )
                    stop_reason = self._frequency_response_stop_reason(
                        points, step.fields.get("stop_conditions")
                    )
                    point_index += 1
                    write_frequency_response_csv(csv_path, points)
                    if stop_reason is not None:
                        error = ConfigError(
                            f"frequency response stop condition triggered: {stop_reason}"
                        )
                        raise self._frequency_response_execution_error(
                            step,
                            error,
                            points=points,
                            csv_path=csv_path,
                            fit_path=None,
                            label=label,
                            response_dir=response_dir,
                            source_channel=source_channel,
                            reference_channel=reference_channel,
                            response_channel=response_channel,
                            stop_conditions=step.fields.get("stop_conditions"),
                            stop_reason=stop_reason,
                            adaptive=adaptive_summary,
                            # The summary is initialized before the first point;
                            # preserve it if an explicit group stop aborts the step.
                            # (The helper accepts None for non-resume runs.)
                            resume=resume_summary,
                        ) from error

            points = unwrap_frequency_response_phase(points)
            if baseline_response is not None:
                try:
                    points, baseline_document = apply_frequency_response_baseline(
                        points, baseline_response.rows, step.fields["baseline"]
                    )
                    baseline_document["baseline_response_directory"] = str(baseline_response.directory)
                    baseline_document["baseline_response"] = baseline_response.label
                    baseline_document["baseline_csv"] = str(baseline_response.csv_path)
                except Exception as exc:
                    write_frequency_response_csv(csv_path, points)
                    raise self._frequency_response_execution_error(
                        step, exc, points=points, csv_path=csv_path, fit_path=None,
                        label=label, response_dir=response_dir, source_channel=source_channel,
                        reference_channel=reference_channel, response_channel=response_channel,
                    ) from exc
            if not adaptive_config or not adaptive_config.get("enabled", True):
                break
            refinement = select_adaptive_frequency_refinement(
                points,
                spacing=step.fields.get("spacing", "log"),
                level=current_level + 1,
                config=adaptive_config,
                existing_frequencies_hz={point.requested_frequency_hz for point in points},
            )
            budget_limited = budget_limited or refinement.budget_limited
            if not refinement.frequencies:
                break
            refinement_levels += 1
            pending = [
                (item.frequency_hz, item.level, item.parent_start_hz, item.parent_stop_hz)
                for item in refinement.frequencies
            ]

        points = unwrap_frequency_response_phase(points)
        if baseline_response is not None:
            points, baseline_document = apply_frequency_response_baseline(
                points, baseline_response.rows, step.fields["baseline"]
            )
            baseline_document["baseline_response_directory"] = str(baseline_response.directory)
            baseline_document["baseline_response"] = baseline_response.label
            baseline_document["baseline_csv"] = str(baseline_response.csv_path)
        written_baseline_path = (
            write_frequency_response_baseline_json(baseline_path, baseline_document)
            if baseline_document is not None
            else None
        )
        if adaptive_config:
            adaptive_summary = {
                "configuration": adaptive_config,
                "initial_frequency_count": len(step.fields["frequencies_hz"]),
                "refinement_levels_completed": refinement_levels,
                "final_frequency_count": len({point.requested_frequency_hz for point in points}),
                "budget_limited": budget_limited,
            }
        fit_document, fit_values = build_fit_document(points, step.fields.get("fit"))
        write_frequency_response_csv(csv_path, points, fit_values)
        written_fit_path = write_fit_document(fit_path, fit_document)
        calibration = step.fields.get("calibration")
        written_calibration_csv: Path | None = None
        written_calibration_json: Path | None = None
        fixed_point_paths: dict[str, Path] = {}
        calibration_error: str | None = None
        if calibration and calibration.get("enabled", True):
            try:
                calibration_config = normalize_frequency_response_calibration(calibration)
                document, rows = build_frequency_response_calibration(
                    [point.as_csv_row() for point in points], calibration_config, source_csv=csv_path
                )
                written_calibration_csv = write_frequency_response_calibration_csv(calibration_csv_path, rows)
                fixed_point_paths = write_fixed_point_calibration(
                    response_dir, document, rows, calibration_config.fixed_point
                )
                written_calibration_json = write_frequency_response_calibration_json(
                    calibration_json_path, document
                )
            except Exception as exc:  # noqa: BLE001 - retain a valid measurement when derived calibration fails
                calibration_error = f"{type(exc).__name__}: {exc}"
        return self._frequency_response_artifact(
            points=points,
            csv_path=csv_path,
            fit_path=written_fit_path,
            label=label,
            response_dir=response_dir,
            source_channel=source_channel,
            reference_channel=reference_channel,
            response_channel=response_channel,
            baseline_json_path=written_baseline_path,
            adaptive=adaptive_summary,
            calibration_csv_path=written_calibration_csv,
            calibration_json_path=written_calibration_json,
            fixed_point_paths=fixed_point_paths,
            calibration_error=calibration_error,
            resume=resume_summary,
            stop_conditions=step.fields.get("stop_conditions"),
        )

    def _annotate_frequency_point(
        self,
        point: Any,
        *,
        case_id_value: str,
        plan_hash: str,
        reference_channel: int,
        response_channel: int,
        requested_frequency_hz: float,
        requested_vpp: float | None,
        retry_of: str | None = None,
    ) -> Any:
        """Attach stable point/capture evidence without making capture fragile.

        Capture metadata is an audit artifact.  If an older or synthetic capture has
        no writable metadata path, the measured point is retained and the annotation
        failure becomes a warning on that point instead of discarding the measurement.
        """

        capture_path = getattr(point, "capture_package", "")
        metadata_path = getattr(point, "metadata_path", "")
        acquisition = ""
        annotation_error = ""
        if capture_path:
            acquisition = acquisition_id(capture_path, label=case_id_value)
            if metadata_path:
                try:
                    annotate_capture_metadata(
                        metadata_path,
                        case=case_id_value,
                        acquisition=acquisition,
                        requested_frequency_hz=requested_frequency_hz,
                        requested_source_vpp=requested_vpp,
                        reference_channel=reference_channel,
                        response_channel=response_channel,
                        reference_vpp_v=getattr(point, "reference_vpp_v", None),
                        response_vpp_v=getattr(point, "response_vpp_v", None),
                        plan_hash=plan_hash,
                        retry_of=retry_of,
                    )
                except Exception as exc:  # noqa: BLE001 - preserve the measurement evidence
                    annotation_error = f"evidence_annotation_failed: {type(exc).__name__}: {exc}"

        updates: dict[str, Any] = {}
        available = getattr(point, "__dataclass_fields__", {})
        if "case_id" in available:
            updates["case_id"] = case_id_value
        if "acquisition_id" in available:
            updates["acquisition_id"] = acquisition
        if "capture_sync_grade" in available:
            updates["capture_sync_grade"] = CAPTURE_SYNC_GRADE if capture_path else "unknown"
        if "plan_hash" in available:
            updates["plan_hash"] = plan_hash
        if "requested_source_vpp" in available:
            updates["requested_source_vpp"] = requested_vpp
        if "reference_plane" in available:
            updates["reference_plane"] = "scope_input"
        if "signal_level_evidence" in available:
            updates["signal_level_evidence"] = signal_level_evidence(
                requested_source_vpp=requested_vpp,
                reference_vpp_v=getattr(point, "reference_vpp_v", None),
                response_vpp_v=getattr(point, "response_vpp_v", None),
                reference_channel=reference_channel,
                response_channel=response_channel,
            )
        if annotation_error:
            existing_warnings = tuple(getattr(point, "warnings", ()) or ())
            updates["warnings"] = existing_warnings + (annotation_error,)
            if getattr(point, "status", "ok") == "ok":
                updates["status"] = "warning"
        return replace(point, **updates) if updates else point

    def _find_failed_frequency_capture(self, label: str) -> Path | None:
        """Find the most recent partial package left by a failed capture."""

        try:
            candidates = sorted(
                (
                    path
                    for path in self.config.output.directory.glob(f"*_{safe_label(label)}_failed")
                    if path.is_dir()
                ),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError:
            return None
        return candidates[0] if candidates else None

    @staticmethod
    def _resume_csv_from_manifest(run_dir: Path, label: str) -> Path:
        manifest_path = run_dir / "frequency_responses.json"
        try:
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"frequency responses manifest is unreadable: {manifest_path}") from exc
        entries = document.get("responses") if isinstance(document, dict) else None
        if not isinstance(entries, list):
            raise ConfigError(f"frequency responses manifest has no responses array: {manifest_path}")
        selected = next(
            (entry for entry in entries if isinstance(entry, dict) and entry.get("label") == label),
            None,
        )
        if not isinstance(selected, dict):
            raise ConfigError(f"frequency response {label!r} was not found in {manifest_path}")
        directory = Path(str(selected.get("directory", ".")))
        if not directory.is_absolute():
            directory = run_dir / directory
        return directory / str(selected.get("csv", "frequency_response.csv"))

    def _load_frequency_response_resume(
        self,
        plan: RunPlan,
        step: RunStep,
        *,
        label: str,
        requested_amplitudes: list[float | None],
        plan_hash: str,
    ) -> tuple[list[Any], set[tuple[int, float | None, str]], dict[str, Any] | None]:
        raw_path = step.fields.get("resume_from")
        if not raw_path:
            return [], set(), None
        source = Path(str(raw_path))
        if not source.is_absolute():
            source = plan.path.parent / source
        if source.is_dir() and (source / "frequency_responses.json").exists():
            source = self._resume_csv_from_manifest(source, label)
        loaded = load_frequency_response_points(source)
        usable: list[Any] = []
        resumed_keys: set[tuple[int, float | None, str]] = set()
        rejected: list[dict[str, Any]] = []
        expected_amplitudes = {
            None if value is None else float(value) for value in requested_amplitudes
        }
        for point in loaded:
            if point.plan_hash and point.plan_hash != plan_hash:
                raise ConfigError(
                    "resume response plan hash does not match the current plan: "
                    f"{point.plan_hash} != {plan_hash}"
                )
            requested_vpp = point.requested_source_vpp
            if requested_vpp not in expected_amplitudes:
                rejected.append({"index": point.index, "reason": "amplitude_not_in_current_plan"})
                continue
            expected_case = case_id(
                plan_name=plan.name,
                step_index=step.index,
                label=label,
                frequency_hz=point.requested_frequency_hz,
                requested_vpp=requested_vpp,
                reference_channel=step.fields["reference_channel"],
                response_channel=step.fields["response_channel"],
            )
            if point.case_id and point.case_id != expected_case:
                raise ConfigError(
                    "resume response case identity does not match the current plan: "
                    f"{point.case_id} != {expected_case}"
                )
            if point.status == "ok" and point.usable_for_fit:
                usable.append(replace(point, case_id=expected_case, plan_hash=plan_hash))
                resolved_amplitude_index = (
                    requested_amplitudes.index(requested_vpp)
                    if requested_vpp in requested_amplitudes
                    else point.amplitude_index
                )
                resumed_keys.add(
                    self._frequency_response_point_key(
                        resolved_amplitude_index, requested_vpp, point.requested_frequency_hz
                    )
                )
            else:
                rejected.append(
                    {
                        "index": point.index,
                        "case_id": point.case_id or expected_case,
                        "status": point.status,
                        "reason": point.failure_reason or point.error or "not_reusable",
                    }
                )
        return usable, resumed_keys, {
            "source": str(source),
            "reused_point_count": len(usable),
            "rejected_point_count": len(rejected),
            "rejected_points": rejected,
        }

    @staticmethod
    def _frequency_response_point_key(
        amplitude_index: int, requested_vpp: float | None, frequency_hz: float
    ) -> tuple[int, float | None, str]:
        return (
            int(amplitude_index),
            None if requested_vpp is None else float(requested_vpp),
            format(float(frequency_hz), ".12g"),
        )

    @staticmethod
    def _frequency_response_stop_reason(
        points: list[Any], conditions: dict[str, Any] | None
    ) -> str | None:
        if not conditions:
            return None
        failed_count = sum(getattr(point, "status", "") == "failed" for point in points)
        warning_count = sum(getattr(point, "status", "") == "warning" for point in points)
        if "max_failed_points" in conditions and failed_count >= conditions["max_failed_points"]:
            return f"max_failed_points={conditions['max_failed_points']} (actual {failed_count})"
        if "max_warning_points" in conditions and warning_count >= conditions["max_warning_points"]:
            return f"max_warning_points={conditions['max_warning_points']} (actual {warning_count})"
        if "max_consecutive_failed_points" in conditions:
            consecutive = 0
            for point in reversed(points):
                if getattr(point, "status", "") != "failed":
                    break
                consecutive += 1
            if consecutive >= conditions["max_consecutive_failed_points"]:
                return (
                    "max_consecutive_failed_points="
                    f"{conditions['max_consecutive_failed_points']} (actual {consecutive})"
                )
        limit = conditions.get("max_gain_jump_db")
        if limit is not None and len(points) >= 2:
            current = points[-1]
            current_gain = getattr(current, "gain_db", None)
            if current_gain is not None and getattr(current, "status", "") != "failed":
                previous = next(
                    (
                        point
                        for point in reversed(points[:-1])
                        if getattr(point, "amplitude_index", None)
                        == getattr(current, "amplitude_index", None)
                        and getattr(point, "gain_db", None) is not None
                        and getattr(point, "status", "") != "failed"
                    ),
                    None,
                )
                if previous is not None:
                    jump = abs(float(current_gain) - float(previous.gain_db))
                    if jump > float(limit):
                        return f"max_gain_jump_db={limit:g} (actual {jump:g})"
        return None

    def _frequency_response_execution_error(
        self,
        step: RunStep,
        cause: Exception,
        *,
        points: list[Any],
        csv_path: Path,
        fit_path: Path | None,
        label: str,
        response_dir: Path,
        source_channel: int | None,
        reference_channel: int,
        response_channel: int,
        baseline_json_path: Path | None = None,
        adaptive: dict[str, Any] | None = None,
        calibration_csv_path: Path | None = None,
        calibration_json_path: Path | None = None,
        fixed_point_paths: dict[str, Path] | None = None,
        stop_conditions: dict[str, Any] | None = None,
        stop_reason: str | None = None,
    ) -> _FrequencyResponseExecutionError:
        artifact = self._frequency_response_artifact(
            points=points,
            csv_path=csv_path,
            fit_path=fit_path,
            label=label,
            response_dir=response_dir,
            source_channel=source_channel,
            reference_channel=reference_channel,
            response_channel=response_channel,
            baseline_json_path=baseline_json_path,
            adaptive=adaptive,
            calibration_csv_path=calibration_csv_path,
            calibration_json_path=calibration_json_path,
            fixed_point_paths=fixed_point_paths,
            stop_conditions=stop_conditions,
            stop_reason=stop_reason,
            error=cause,
        )
        record = RunStepRecord(
            index=step.index,
            kind=step.kind,
            status="failed",
            fields=step.fields,
            artifact=artifact,
        )
        return _FrequencyResponseExecutionError(record, cause)

    def _frequency_response_artifact(
        self,
        *,
        points: list[Any],
        csv_path: Path,
        fit_path: Path | None,
        label: str,
        response_dir: Path,
        source_channel: int | None,
        reference_channel: int,
        response_channel: int,
        baseline_json_path: Path | None = None,
        adaptive: dict[str, Any] | None = None,
        calibration_csv_path: Path | None = None,
        calibration_json_path: Path | None = None,
        fixed_point_paths: dict[str, Path] | None = None,
        calibration_error: str | None = None,
        resume: dict[str, Any] | None = None,
        stop_conditions: dict[str, Any] | None = None,
        stop_reason: str | None = None,
        error: Exception | None = None,
    ) -> dict[str, Any]:
        failed_points = sum(point.status == "failed" for point in points)
        warning_points = sum(point.status == "warning" for point in points)
        captures = [
            {
                "index": point.index,
                "case_id": getattr(point, "case_id", ""),
                "acquisition_id": getattr(point, "acquisition_id", ""),
                "capture_sync_grade": getattr(
                    point, "capture_sync_grade", CAPTURE_SYNC_GRADE
                ),
                "quality_metrics": getattr(point, "quality_metrics", {}),
                "retry_package": getattr(point, "retry_capture_package", ""),
                "retry_metadata": getattr(point, "retry_metadata_path", ""),
                "package": point.capture_package,
                "metadata": point.metadata_path,
            }
            for point in points
            if point.capture_package
        ]
        response: dict[str, Any] = {
            "status": "failed"
            if error is not None or failed_points
            else ("warning" if warning_points or calibration_error else "ok"),
            "csv": str(csv_path),
            "fit_json": str(fit_path) if fit_path is not None else "",
            "label": label,
            "directory": str(response_dir),
            "baseline_json": str(baseline_json_path) if baseline_json_path is not None else "",
            "calibration_csv": str(calibration_csv_path) if calibration_csv_path is not None else "",
            "calibration_json": str(calibration_json_path) if calibration_json_path is not None else "",
            "fixed_point": {
                key: str(value) for key, value in (fixed_point_paths or {}).items()
            },
            "point_count": len(points),
            "failed_point_count": failed_points,
            "warning_point_count": warning_points,
            "source_channel": source_channel,
            "reference_channel": reference_channel,
            "response_channel": response_channel,
            "captures": captures,
            "evidence": {
                "schema": "wavebench.frequency_response_evidence.v1",
                "plan_hash": next(
                    (
                        getattr(point, "plan_hash", "")
                        for point in points
                        if getattr(point, "plan_hash", "")
                    ),
                    "",
                ),
                "reference_plane": "scope_input",
                "capture_sync_grade": next(
                    (
                        getattr(point, "capture_sync_grade", "")
                        for point in points
                        if getattr(point, "capture_sync_grade", "")
                    ),
                    CAPTURE_SYNC_GRADE,
                ),
                "case_ids": [
                    getattr(point, "case_id", "") for point in points if getattr(point, "case_id", "")
                ],
                "acquisition_ids": [
                    getattr(point, "acquisition_id", "")
                    for point in points
                    if getattr(point, "acquisition_id", "")
                ],
                "excluded_points": [
                    {
                        "case_id": getattr(point, "case_id", ""),
                        "index": point.index,
                        "reason": point.error or point.status,
                    }
                    for point in points
                    if not point.usable_for_fit
                ],
            },
        }
        if error is not None:
            response["error"] = f"{type(error).__name__}: {error}"
        if calibration_error is not None:
            response["calibration_error"] = calibration_error
        if adaptive is not None:
            response["adaptive"] = adaptive
        if resume is not None:
            response["resume"] = resume
        if stop_conditions is not None:
            response["stop_conditions"] = stop_conditions
        if stop_reason is not None:
            response["stop_reason"] = stop_reason
        return {"frequency_response": response}

    @staticmethod
    def _frequency_response_directory(
        run_dir: Path, step_index: int, label: str, *, multiple_responses: bool
    ) -> Path:
        if not multiple_responses:
            return run_dir
        safe_label = "".join(
            character if character.isalnum() or character in {"-", "_"} else "_"
            for character in label
        ).strip("._")
        if not safe_label:
            safe_label = f"response_{step_index:02d}"
        return run_dir / "frequency_response" / f"{step_index:02d}_{safe_label}"

    def _update_frequency_responses_manifest(self, run_dir: Path, record: RunStepRecord) -> None:
        if record.kind != "sweep.frequency_response":
            return
        response = record.artifact.get("frequency_response")
        if not isinstance(response, dict):
            return
        directory_text = response.get("directory")
        if not isinstance(directory_text, str) or not directory_text:
            return
        directory = Path(directory_text)
        try:
            relative_directory = directory.resolve().relative_to(run_dir.resolve())
        except ValueError:
            return
        manifest_path = run_dir / "frequency_responses.json"
        existing: dict[str, Any] = {"schema_version": 1, "responses": []}
        if manifest_path.exists():
            try:
                candidate = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(candidate, dict) and isinstance(candidate.get("responses"), list):
                    existing = candidate
            except json.JSONDecodeError:
                pass
        entry = {
            "step_index": record.index,
            "label": str(response.get("label", f"frequency_response_{record.index:02d}")),
            "directory": str(relative_directory) if str(relative_directory) else ".",
            "csv": "frequency_response.csv",
            "fit_json": "frequency_response_fit.json" if response.get("fit_json") else "",
            "baseline_json": "frequency_response_baseline.json" if response.get("baseline_json") else "",
            "calibration_csv": "frequency_response_calibration.csv" if response.get("calibration_csv") else "",
            "calibration_json": "frequency_response_calibration.json" if response.get("calibration_json") else "",
            "status": response.get("status", record.status),
            "adaptive": response.get("adaptive"),
            "fixed_point": response.get("fixed_point", {}),
            "evidence": response.get("evidence", {}),
            "resume": response.get("resume"),
            "stop_reason": response.get("stop_reason", ""),
        }
        entries = [item for item in existing["responses"] if not (
            isinstance(item, dict) and item.get("label") == entry["label"]
        )]
        entries.append(entry)
        entries.sort(key=lambda item: int(item.get("step_index", 0)))
        existing["schema_version"] = 1
        existing["responses"] = entries
        temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        temporary.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(manifest_path)

    def _run_scope_capture_step(
        self,
        plan: RunPlan,
        step: RunStep,
        *,
        services: RunInstrumentServices | None = None,
    ) -> dict[str, Any]:
        service = self._scope_service_for_capture(plan, step, services=services)
        channel = step.fields.get("channel", self.config.scope.default_channel)
        label = step.fields.get("label", f"{plan.label}_{step.index:02d}_capture")
        autoscale_before_capture = step.fields.get("autoscale_before_capture", False)
        autoscale_settle_s = step.fields.get("autoscale_settle_s", 0.0)
        autoscale_record: dict[str, Any] | None = None
        if autoscale_before_capture:
            service.autoscale()
            if autoscale_settle_s:
                time.sleep(autoscale_settle_s)
            autoscale_record = {"status": "completed", "settle_s": autoscale_settle_s}

        capture = service.capture_waveform(channel=channel, label=label)
        artifact = self._capture_artifact(capture, service)
        if autoscale_record is not None:
            artifact["autoscale_before_capture"] = autoscale_record

        quality_gate = step.fields.get("quality_gate", False)
        auto_recover = step.fields.get("auto_recover", False)
        warnings = list(artifact["quality"]["warnings"])
        if (quality_gate or auto_recover) and warnings and auto_recover:
            artifacts = [artifact]
            attempts: list[dict[str, Any]] = [self._recovery_attempt_record(0, "initial", artifact)]
            consistency = capture_consistency(artifacts, self.config.quality)
            for attempt in range(1, self.config.quality.auto_recover_attempts + 1):
                service.autoscale()
                retry = service.capture_waveform(
                    channel=channel, label=f"{label}_auto_retry{attempt}"
                )
                artifact = self._capture_artifact(retry, service)
                artifacts.append(artifact)
                attempts.append(self._recovery_attempt_record(attempt, "auto_retry", artifact))
                if not artifact["quality"]["warnings"]:
                    consistency = capture_consistency(artifacts, self.config.quality)
                    break
                consistency = capture_consistency(artifacts, self.config.quality)
                if consistency["status"] == "consistent":
                    artifact["quality"] = {
                        **artifact["quality"],
                        "status": "ok_by_consistency",
                        "trusted_by_consistency": True,
                    }
                    break
            artifact["quality_recovery"] = {
                "trigger": "quality_warnings",
                "max_auto_recover_attempts": self.config.quality.auto_recover_attempts,
                "attempts": attempts,
                "consistency": consistency,
            }
        elif quality_gate:
            artifact["quality_gate"] = {
                "status": "warning" if warnings else "ok",
                "warnings": warnings,
            }
        if "expect" in step.fields:
            artifact["expect"] = evaluate_expect(artifact.get("quality", {}), step.fields["expect"])
        if "expect_fft" in step.fields:
            fft_summary = capture_fft_summary(capture)
            artifact["fft"] = fft_summary
            artifact["expect_fft"] = evaluate_expect(fft_summary, step.fields["expect_fft"])
        return artifact

    def _recovery_attempt_record(
        self, index: int, kind: str, artifact: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "index": index,
            "kind": kind,
            "package": artifact["package"],
            "metadata": artifact["metadata"],
            "quality": artifact["quality"],
        }

    def _capture_artifact(self, capture: Any, service: ScopeService) -> dict[str, Any]:
        summary = capture.waveform.summary(
            expected_frequency_hz=service.config.waveform.expected_frequency_hz,
            frequency_tolerance_ratio=service.config.waveform.frequency_tolerance_ratio,
            min_signal_vpp=service.config.waveform.min_signal_vpp,
        )
        return {
            "package": str(capture.package_dir),
            "metadata": str(capture.metadata_path),
            "quality": {
                "status": "warning" if summary.get("quality_warnings") else "ok",
                "warnings": list(summary.get("quality_warnings", [])),
                "frequency_estimate_hz": summary.get("frequency_estimate_hz"),
                "estimated_cycles": summary.get("estimated_cycles"),
                "points_per_cycle": summary.get("points_per_cycle"),
                "voltage_vpp_v": summary.get("voltage_vpp_v"),
                "voltage_mean_v": summary.get("voltage_mean_v"),
                "duty_cycle": summary.get("duty_cycle"),
                "frequency_error_ratio": summary.get("frequency_error_ratio"),
            },
        }

    @contextmanager
    def _run_instrument_services(self, plan: RunPlan) -> Iterator[RunInstrumentServices]:
        instruments = self._plan_instruments(plan)
        with ExitStack() as stack:
            close_errors: list[dict[str, Any]] = []

            def record_close_error(operation: str, exc: BaseException) -> None:
                close_errors.append(
                    {
                        "operation": operation,
                        "type": type(exc).__name__,
                        "error": error_envelope(exc, operation=operation),
                    }
                )

            def close_session(
                session: object,
                instrument: str,
                transport: object | None,
            ) -> None:
                operation = f"session.close.{instrument}"
                try:
                    close = getattr(session, "close", None)
                    if callable(close):
                        close()
                except Exception as exc:  # noqa: BLE001 - close must not skip peers
                    record_close_error(operation, exc)
                finally:
                    # Guarded transports already mark the epoch closed before
                    # backend close.  This fallback also closes the local
                    # state when a test/plugin driver fails to delegate close.
                    state = getattr(transport, "session_state", None)
                    close_state = getattr(state, "close", None)
                    if callable(close_state):
                        try:
                            close_state()
                        except Exception as exc:  # noqa: BLE001
                            record_close_error(f"{operation}.state", exc)

            def release_lease(lease: ResourceLease) -> None:
                try:
                    lease.release()
                except Exception as exc:  # noqa: BLE001 - retain lifecycle evidence
                    record_close_error("session.lease.release", exc)

            resources = self._plan_resource_values(instruments)
            lease_manager = self.lease_manager or ResourceLeaseManager()
            leases = lease_manager.acquire_many(
                resources,
                operation=f"run plan {plan.label}",
            )
            for lease in leases:
                # Borrowed leases are released after all driver sessions close.
                stack.callback(release_lease, lease)
            leases_by_fingerprint = {lease.fingerprint: lease for lease in leases}

            def lease_for(resource: str) -> ResourceLease:
                try:
                    return leases_by_fingerprint[resource_fingerprint(resource)]
                except KeyError as exc:
                    raise ConfigError("run resource lease mapping is incomplete") from exc

            scope: ScopeService | None = None
            source: SourceService | None = None
            power: PowerService | None = None
            dmm: DmmService | None = None
            source_guard = SourceStateGuard()
            power_guard = PowerStateGuard()

            if "scope" in instruments:
                logger = CommandLogger()
                scope_lease = lease_for(self.config.connection.resource)
                bootstrap = ScopeService(
                    config=self.config,
                    logger=logger,
                    lease=scope_lease,
                )
                session = bootstrap.open_session()
                stack.callback(close_session, session, "scope", bootstrap.transport)
                scope = ScopeService(
                    config=self.config,
                    logger=logger,
                    session=session,
                    descriptor=bootstrap.descriptor,
                    transport=bootstrap.transport,
                    session_state=bootstrap.session_state,
                    lease=scope_lease,
                )
            if "source" in instruments:
                logger = CommandLogger()
                source_config = self.config.source
                if source_config is None or not source_config.resource:
                    raise ConfigError("source resource is required by this run plan")
                source_lease = lease_for(source_config.resource)
                bootstrap = SourceService(
                    config=self.config,
                    logger=logger,
                    lease=source_lease,
                    state_guard=source_guard,
                )
                session = bootstrap.open_session()
                stack.callback(close_session, session, "source", bootstrap.transport)
                source = SourceService(
                    config=self.config,
                    logger=logger,
                    session=session,
                    descriptor=bootstrap.descriptor,
                    transport=bootstrap.transport,
                    session_state=bootstrap.session_state,
                    lease=source_lease,
                    state_guard=source_guard,
                )
            if "power" in instruments:
                logger = CommandLogger()
                power_config = self.config.power
                if power_config is None or not power_config.resource:
                    raise ConfigError("power resource is required by this run plan")
                power_lease = lease_for(power_config.resource)
                bootstrap = PowerService(
                    config=self.config,
                    logger=logger,
                    lease=power_lease,
                    state_guard=power_guard,
                )
                session = bootstrap.open_session()
                stack.callback(close_session, session, "power", bootstrap.transport)
                power = PowerService(
                    config=self.config,
                    logger=logger,
                    session=session,
                    descriptor=bootstrap.descriptor,
                    transport=bootstrap.transport,
                    session_state=bootstrap.session_state,
                    lease=power_lease,
                    state_guard=power_guard,
                )
            if "dmm" in instruments:
                logger = CommandLogger()
                dmm_config = self.config.dmm
                if dmm_config is None or not dmm_config.resource:
                    raise ConfigError("dmm resource is required by this run plan")
                dmm_lease = lease_for(dmm_config.resource)
                bootstrap = DmmService(
                    config=self.config,
                    logger=logger,
                    lease=dmm_lease,
                )
                session = bootstrap.open_session()
                stack.callback(close_session, session, "dmm", bootstrap.transport)
                dmm = DmmService(
                    config=self.config,
                    logger=logger,
                    session=session,
                    descriptor=bootstrap.descriptor,
                    transport=bootstrap.transport,
                    session_state=bootstrap.session_state,
                    lease=dmm_lease,
                )

            yield RunInstrumentServices(
                scope=scope,
                source=source,
                power=power,
                dmm=dmm,
                close_errors=close_errors,
            )

    def _plan_resource_values(self, instruments: set[str]) -> list[str]:
        resources: list[str] = []
        if "scope" in instruments:
            resources.append(self.config.connection.resource)
        for kind in ("source", "power", "dmm"):
            if kind not in instruments:
                continue
            section = getattr(self.config, kind)
            if section is None or not section.resource:
                raise ConfigError(f"{kind} resource is required by this run plan")
            resources.append(section.resource)
        return resources

    def _power_service(self, *, services: RunInstrumentServices | None = None) -> PowerService:
        if services is not None and services.power is not None:
            return services.power
        return PowerService(config=self.config, logger=CommandLogger())

    def _source_service(self, *, services: RunInstrumentServices | None = None) -> SourceService:
        if services is not None and services.source is not None:
            return services.source
        return SourceService(config=self.config, logger=CommandLogger())

    def _dmm_service(self, *, services: RunInstrumentServices | None = None) -> DmmService:
        if services is not None and services.dmm is not None:
            return services.dmm
        return DmmService(config=self.config, logger=CommandLogger())

    def _scope_service(self, *, services: RunInstrumentServices | None = None) -> ScopeService:
        if services is not None and services.scope is not None:
            return services.scope
        return ScopeService(config=self.config, logger=CommandLogger())

    def _scope_service_for_capture(
        self,
        plan: RunPlan,
        step: RunStep,
        *,
        services: RunInstrumentServices | None = None,
    ) -> ScopeService:
        config = self.config
        if _has_waveform_overrides(step):
            config = config.with_waveform_overrides(
                points=step.fields.get("points"),
                time_range_s=step.fields.get("time_range_s"),
                expected_frequency_hz=step.fields.get("expect_frequency_hz"),
                frequency_tolerance_ratio=step.fields.get("frequency_tolerance"),
                target_cycles=step.fields.get("target_cycles"),
                window_frequency_hz=step.fields.get("window_frequency_hz"),
                vertical_scale_v_per_div=step.fields.get("vertical_scale_v_per_div"),
                target_vpp=step.fields.get("target_vpp"),
            )
        if "save_csv" in step.fields or "save_npy" in step.fields or "screenshot" in step.fields:
            config = config.with_output_overrides(
                save_csv=step.fields.get("save_csv"),
                save_npy=step.fields.get("save_npy"),
                save_screenshot=step.fields.get("screenshot"),
            )
        if services is not None and services.scope is not None:
            return ScopeService(
                config=config,
                logger=services.scope.logger,
                session=services.scope.session,
                descriptor=services.scope.descriptor,
                transport=services.scope.transport,
                session_state=services.scope.session_state,
                lease=services.scope.lease,
            )
        return ScopeService(config=config, logger=CommandLogger())

    def _scope_service_for_frequency_response(
        self,
        step: RunStep,
        *,
        frequency_hz: float,
        services: RunInstrumentServices | None = None,
    ) -> ScopeService:
        config = self.config.with_waveform_overrides(
            points=step.fields.get("points"),
            time_range_s=step.fields["target_cycles"] / frequency_hz,
            expected_frequency_hz=frequency_hz,
            frequency_tolerance_ratio=step.fields.get("frequency_tolerance"),
            target_cycles=step.fields["target_cycles"],
            window_frequency_hz=frequency_hz,
            min_signal_vpp=step.fields["min_signal_vpp"],
        ).with_output_overrides(
            save_csv=step.fields.get("save_csv"),
            save_npy=True,
            save_json=True,
            save_screenshot=step.fields.get("screenshot"),
        )
        if services is not None and services.scope is not None:
            return ScopeService(
                config=config,
                logger=services.scope.logger,
                session=services.scope.session,
                descriptor=services.scope.descriptor,
                transport=services.scope.transport,
                session_state=services.scope.session_state,
                lease=services.scope.lease,
            )
        return ScopeService(config=config, logger=CommandLogger())


def _has_waveform_overrides(step: RunStep) -> bool:
    return any(
        key in step.fields
        for key in (
            "points",
            "time_range_s",
            "expect_frequency_hz",
            "frequency_tolerance",
            "target_cycles",
            "window_frequency_hz",
            "vertical_scale_v_per_div",
            "target_vpp",
        )
    )


def _status_payload(status: Any) -> dict[str, Any]:
    if hasattr(status, "as_dict"):
        return status.as_dict()
    if hasattr(status, "__dict__"):
        return dict(status.__dict__)
    return {"repr": repr(status)}


def _first_finite_row_value(row: dict[str, Any], *names: str) -> float | None:
    for name in names:
        try:
            value = float(row.get(name))
        except (TypeError, ValueError):
            continue
        if isfinite(value):
            return value
    return None


def _restore_error_message(error: dict[str, Any]) -> str:
    """Expose the first concrete channel error while retaining the full payload."""

    errors = error.get("errors")
    if isinstance(errors, list):
        details = [
            str(item.get("message"))
            for item in errors
            if isinstance(item, dict) and item.get("message")
        ]
        if details:
            return "; ".join(details)
    return str(error.get("message", "source state restore failed"))
