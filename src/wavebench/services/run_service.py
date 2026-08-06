from __future__ import annotations

import shutil
import time
import json
from math import isfinite
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator

from wavebench.config import WaveBenchConfig
from wavebench.data.package import new_package_dir
from wavebench.data.packages import load_run_package
from wavebench.errors import ConfigError
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
from wavebench.services.scope_service import ScopeService
from wavebench.services.source_service import SourceService
from wavebench.services.source_state import RestorableSourceState


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
        self._check_plan_capabilities(plan)

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
        return instruments

    def run(self, plan: RunPlan) -> RunResult:
        self.check(plan)
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
            restore_error: dict[str, str] | None = None
            try:
                restore_state = snapshot_source_state(
                    plan,
                    source_service_factory=lambda: self._source_service(services=services),
                )
                for step in plan.steps:
                    record = self._run_step(plan, step, run_dir=run_dir, services=services)
                    records.append(record)
                    write_step_record(steps_dir, record)
                    self._update_frequency_responses_manifest(run_dir, record)
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
                write_run_files(
                    plan=plan,
                    run_json_path=run_json_path,
                    summary_csv_path=summary_csv_path,
                    status="failed",
                    records=records,
                    error={"type": type(failure).__name__, "message": str(failure)},
                    restore_state=restore_state,
                    restore_error=restore_error,
                )
                if isinstance(exc, _FrequencyResponseExecutionError):
                    raise failure from None
                raise

            restore_error = restore_source_state(
                restore_state,
                source_service_factory=lambda: self._source_service(services=services),
            )
            if restore_error is not None:
                write_run_files(
                    plan=plan,
                    run_json_path=run_json_path,
                    summary_csv_path=summary_csv_path,
                    status="failed",
                    records=records,
                    error={"type": "ConfigError", "message": "source state restore failed"},
                    restore_state=restore_state,
                    restore_error=restore_error,
                )
                raise ConfigError("run plan source state restore failed: " + restore_error["message"])

            run_status = "failed" if any(record.status == "failed" for record in records) else "ok"
            write_run_files(
                plan=plan,
                run_json_path=run_json_path,
                summary_csv_path=summary_csv_path,
                status=run_status,
                records=records,
                error=None,
                restore_state=restore_state,
                restore_error=None,
            )
            return RunResult(
                run_dir=run_dir,
                run_json_path=run_json_path,
                summary_csv_path=summary_csv_path,
                steps=records,
            )

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
            status = self._source_service(services=services).upload_arbitrary_waveform(
                channel=step.fields.get("channel"),
                file_path=step.fields["file"],
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
        point_index = 0
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
                    try:
                        source_status = source.set_frequency(channel=source_channel, value_hz=frequency_hz)
                    except Exception as exc:
                        points.append(failed_frequency_response_point(
                            index=point_index, amplitude_index=amplitude_index, requested_vpp=requested_vpp,
                            requested_frequency_hz=frequency_hz, error=exc, adaptive_level=adaptive_level,
                            adaptive_parent_start_hz=parent_start, adaptive_parent_stop_hz=parent_stop,
                        ))
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
                        points.append(failed_frequency_response_point(
                            index=point_index, amplitude_index=amplitude_index, requested_vpp=requested_vpp,
                            requested_frequency_hz=frequency_hz, error=error, adaptive_level=adaptive_level,
                            adaptive_parent_start_hz=parent_start, adaptive_parent_stop_hz=parent_stop,
                        ))
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
                    try:
                        amplitude_label = (
                            f"{label}_{point_index:03d}_{frequency_hz:.12g}hz"
                            if requested_vpp is None
                            else f"{label}_a{amplitude_index:02d}_{requested_vpp:.12g}vpp_"
                            f"l{adaptive_level}_{frequency_index:03d}_{frequency_hz:.12g}hz"
                        )
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
                                point = replace(
                                    retry_point,
                                    status="failed" if retry_point.status == "warning" else retry_point.status,
                                    quality_retry_count=1,
                                    initial_warnings=initial_point.warnings,
                                    initial_capture_package=initial_point.capture_package,
                                    initial_metadata_path=initial_point.metadata_path,
                                    error=(
                                        "quality_retry_exhausted: initial warnings="
                                        + " | ".join(initial_point.warnings)
                                        + "; retry warnings=" + " | ".join(retry_point.warnings)
                                    ) if retry_point.status == "warning" else retry_point.error,
                                )
                            except Exception as retry_exc:  # noqa: BLE001 - retain the first capture evidence
                                point = replace(
                                    initial_point,
                                    status="failed",
                                    quality_retry_count=1,
                                    initial_warnings=initial_point.warnings,
                                    initial_capture_package=initial_point.capture_package,
                                    initial_metadata_path=initial_point.metadata_path,
                                    error=f"quality_retry_failed: {type(retry_exc).__name__}: {retry_exc}",
                                )
                        points.append(point)
                    except Exception as exc:  # noqa: BLE001 - retain failed points and continue the sweep
                        points.append(failed_frequency_response_point(
                            index=point_index, amplitude_index=amplitude_index, requested_vpp=requested_vpp,
                            requested_frequency_hz=frequency_hz, error=exc, adaptive_level=adaptive_level,
                            adaptive_parent_start_hz=parent_start, adaptive_parent_stop_hz=parent_stop,
                        ))
                    point_index += 1
                    write_frequency_response_csv(csv_path, points)

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
        )

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
        error: Exception | None = None,
    ) -> dict[str, Any]:
        failed_points = sum(point.status == "failed" for point in points)
        warning_points = sum(point.status == "warning" for point in points)
        captures = [
            {
                "index": point.index,
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
        }
        if error is not None:
            response["error"] = f"{type(error).__name__}: {error}"
        if calibration_error is not None:
            response["calibration_error"] = calibration_error
        if adaptive is not None:
            response["adaptive"] = adaptive
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
            scope: ScopeService | None = None
            source: SourceService | None = None
            power: PowerService | None = None
            dmm: DmmService | None = None

            if "scope" in instruments:
                logger = CommandLogger()
                session = ScopeService(config=self.config, logger=logger).open_session()
                stack.callback(session.close)
                scope = ScopeService(config=self.config, logger=logger, session=session)
            if "source" in instruments:
                logger = CommandLogger()
                session = SourceService(config=self.config, logger=logger).open_session()
                stack.callback(session.close)
                source = SourceService(config=self.config, logger=logger, session=session)
            if "power" in instruments:
                logger = CommandLogger()
                session = PowerService(config=self.config, logger=logger).open_session()
                stack.callback(session.close)
                power = PowerService(config=self.config, logger=logger, session=session)
            if "dmm" in instruments:
                logger = CommandLogger()
                session = DmmService(config=self.config, logger=logger).open_session()
                stack.callback(session.close)
                dmm = DmmService(config=self.config, logger=logger, session=session)

            yield RunInstrumentServices(scope=scope, source=source, power=power, dmm=dmm)

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
