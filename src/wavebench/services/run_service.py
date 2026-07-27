from __future__ import annotations

import shutil
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from wavebench.config import WaveBenchConfig
from wavebench.data.package import new_package_dir
from wavebench.errors import ConfigError, WaveBenchError
from wavebench.instruments.capabilities import require_capabilities
from wavebench.instruments.registry import resolve_instrument_descriptor
from wavebench.logging import CommandLogger
from wavebench.services.power_service import PowerService
from wavebench.services.dmm_service import DmmService
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
        self._check_plan_capabilities(plan)

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
                    record = self._run_step(plan, step, services=services)
                    records.append(record)
                    write_step_record(steps_dir, record)
            except Exception as exc:
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
                    error={"type": type(exc).__name__, "message": str(exc)},
                    restore_state=restore_state,
                    restore_error=restore_error,
                )
                if isinstance(exc, WaveBenchError):
                    raise
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
