from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryFile

import numpy as np

from .config import load_config, vertical_scale_from_vpp
from .data.packages import load_capture_package, load_run_package
from .discovery import discover_instruments
from .doctor import doctor_records, has_doctor_errors as has_config_doctor_errors
from .report.html import write_run_report_html, write_run_report_pdf
from .report.index import write_report_index
from .errors import ConfigError, WaveBenchError, error_envelope
from .cli_parser import build_parser
from .cli_output import (
    _print_arbitrary_probe_results,
    _print_arbitrary_waveform_summary,
    _print_capture_fft_summary,
    _print_capture_package_summary,
    _print_capability_explanation,
    _print_discovery_results,
    _print_doctor_records,
    _print_dmm_function_set,
    _print_dmm_function_status,
    _print_dmm_calculation_statistics,
    _print_dmm_calculation_status,
    _print_dmm_dcv_impedance_configuration,
    _print_dmm_measurement_profile,
    _print_dmm_reading,
    _print_dmm_system_interface_status,
    _print_dmm_trigger_status,
    _print_dmm_voltage_range_configuration,
    _print_market_plugin_info,
    _print_market_search_results,
    _print_instrument_descriptor,
    _print_plugin_doctor,
    _print_plugin_info,
    _print_plugin_list,
    _print_plugin_package,
    _print_installed_plugin,
    _print_installed_plugins,
    _print_lifecycle_result,
    _print_scpi_doctor,
    _print_scpi_probe_result,
    _print_scpi_plugin_info,
    _print_power_protection_status,
    _print_power_status,
    _print_run_plan_summary,
    _print_run_preflight,
    _print_scope_acquisition_status,
    _print_scope_average_capture,
    _print_scope_channel_input_state,
    _print_scope_history_timestamps,
    _print_scope_digital_status,
    _print_scope_digital_status_v2,
    _print_scope_digital_waveform,
    _print_scope_measurement_statistics,
    _print_scope_cursor_readout,
    _print_scope_derived_waveform_metadata,
    _print_scope_fft_status,
    _print_scope_snapshot,
    _print_source_channel_profile,
    _print_source_burst_profile,
    _print_source_counter_profile,
    _print_source_pulse_profile,
    _print_source_sweep_profile,
    _print_source_status,
    _print_waveform_summary,
)
from .logging import CommandLogger
from .instruments.registry import build_instrument_registry
from .instruments.registry import resolve_instrument_descriptor
from .instruments.scope_extensions import (
    ErrorCheckSpec,
    ScopeContinuousAcquisitionRequest,
    ScopeScreenshot,
    ScopeScreenshotRequest,
    ScopeTraceData,
    ScopeTraceRef,
)
from .instruments.rf_source_extensions import (
    RfCwRequest,
    RfModulatedOutputRequest,
    RfModulationDisableRequest,
    RfModulationKind,
    RfModulationRequest,
    RfOutputRequest,
    RfPulseConfigureRequest,
    RfPulsePolarity,
    RfSweepConfigureRequest,
)
from .mcp_http import (
    resolve_mcp_token,
    serve_mcp_http,
)
from .plugins.market import load_market_index
from .plugins.lifecycle import PluginLifecycle
from .plugins.package_inspect import inspect_plugin_package
from .plugins.api import PluginDoctorRecord
from .plugins.registry import build_plugin_registry, has_doctor_errors, plugin_doctor_records
from .plugins.scpi import has_scpi_doctor_errors, load_scpi_plugin, probe_scpi_plugin, scpi_plugin_doctor_records
from .services.scope_service import ScopeService
from .services.source_service import SourceService
from .services.rf_source_service import RfSourceService
from .services.power_service import PowerService
from .services.dmm_service import DmmService
from .services.run_plan import format_run_plan_schema, load_run_plan
from .services.run_templates import (
    RunTemplateOptions,
    list_run_templates,
    parse_frequencies,
    render_run_template,
    write_run_template,
)
from .services.run_service import RunService
from .services.capability_explain import explain_operation
from .services.operation_specs import get_operation_spec
from .services.run_compare import RunCompareError, compare_run_packages_result
from .services.frequency_response_resume import build_frequency_response_resume
from .services.execution_intent import (
    build_execution_intent,
    load_execution_intent,
    write_execution_intent,
)
from .services.resource_lease import ResourceLease
from .services.frequency_response_calibration import (
    build_frequency_response_calibration,
    load_frequency_response_calibration_config,
    write_frequency_response_calibration_csv,
    write_frequency_response_calibration_json,
    write_fixed_point_calibration,
)
from .services.sweep_service import SweepService, parse_frequency_list


CLI_RESULT_SCHEMA = "wavebench.cli.result.v1"



def _load_service(args: argparse.Namespace) -> ScopeService:
    config = load_config(args.config)
    if args.resource:
        config = config.with_resource(args.resource)
    if (
        getattr(args, "points", None)
        or getattr(args, "time_range", None) is not None
        or getattr(args, "expect_frequency", None) is not None
        or getattr(args, "frequency_tolerance", None) is not None
        or getattr(args, "target_cycles", None) is not None
        or getattr(args, "window_frequency", None) is not None
        or getattr(args, "vertical_scale", None) is not None
        or getattr(args, "target_vpp", None) is not None
    ):
        expected_frequency = getattr(args, "expect_frequency", None)
        window_frequency = getattr(args, "window_frequency", None) or expected_frequency
        target_cycles = getattr(args, "target_cycles", None)
        time_range = getattr(args, "time_range", None)
        vertical_scale = getattr(args, "vertical_scale", None)
        target_vpp = getattr(args, "target_vpp", None)
        if target_vpp is not None:
            if target_vpp <= 0:
                raise ConfigError("--target-vpp must be > 0")
            if vertical_scale is None:
                vertical_scale = vertical_scale_from_vpp(target_vpp)
        if vertical_scale is not None and vertical_scale <= 0:
            raise ConfigError("--vertical-scale must be > 0")
        if target_cycles is not None:
            if window_frequency is None or window_frequency <= 0:
                raise ConfigError("--target-cycles requires --window-frequency or --expect-frequency > 0")
            if target_cycles <= 0:
                raise ConfigError("--target-cycles must be > 0")
            if time_range is None:
                time_range = target_cycles / window_frequency
        config = config.with_waveform_overrides(
            points=getattr(args, "points", None),
            time_range_s=time_range,
            expected_frequency_hz=expected_frequency,
            frequency_tolerance_ratio=getattr(args, "frequency_tolerance", None),
            target_cycles=target_cycles,
            window_frequency_hz=window_frequency,
            vertical_scale_v_per_div=vertical_scale,
            target_vpp=target_vpp,
        )
    if getattr(args, "no_csv", False) or getattr(args, "no_npy", False) or getattr(args, "screenshot", False):
        config = config.with_output_overrides(
            save_csv=False if getattr(args, "no_csv", False) else None,
            save_npy=False if getattr(args, "no_npy", False) else None,
            save_screenshot=True if getattr(args, "screenshot", False) else None,
        )
    return ScopeService(config=config, logger=CommandLogger())


def _load_source_service(args: argparse.Namespace) -> SourceService:
    config = load_config(args.config)
    if args.resource:
        config = config.with_source_resource(args.resource)
    probe_timeout_ms = getattr(args, "probe_timeout_ms", None)
    if probe_timeout_ms is not None:
        config = config.with_connection_timeout_ms(probe_timeout_ms)
    return SourceService(config=config, logger=CommandLogger())


def _load_rf_source_service(args: argparse.Namespace) -> RfSourceService:
    config = load_config(args.config)
    if args.resource:
        config = config.with_rf_source_resource(args.resource)
    return RfSourceService(config=config, logger=CommandLogger())


def _load_power_service(args: argparse.Namespace) -> PowerService:
    config = load_config(args.config)
    if args.resource:
        config = config.with_power_resource(args.resource)
    return PowerService(config=config, logger=CommandLogger())


def _load_dmm_service(args: argparse.Namespace) -> DmmService:
    config = load_config(args.config)
    if args.resource:
        config = config.with_dmm_resource(args.resource)
    return DmmService(config=config, logger=CommandLogger())


def _load_run_service(args: argparse.Namespace) -> RunService:
    config = load_config(args.config)
    if args.resource:
        config = config.with_resource(args.resource)
    return RunService(config=config, logger=CommandLogger())


def _load_sweep_service(args: argparse.Namespace) -> SweepService:
    config = load_config(args.config)
    if args.resource:
        config = config.with_resource(args.resource)
    if getattr(args, "source_resource", None):
        config = config.with_source_resource(args.source_resource)
    return SweepService(config=config, logger=CommandLogger())


def _capability_target(args: argparse.Namespace):
    spec = get_operation_spec(args.operation)
    if spec is None or spec.instrument_kind is None:
        return None, args.access or "read_write"
    config = load_config(args.config) if args.config else None
    kind = args.kind or spec.instrument_kind
    if kind != spec.instrument_kind:
        raise ConfigError(
            f"capability explain operation {args.operation!r} requires instrument kind "
            f"{spec.instrument_kind!r}, not {kind!r}"
        )
    section = getattr(config, kind, None) if config is not None else None
    driver = args.driver or getattr(section, "driver", None)
    access = args.access or getattr(section, "access", None) or "read_write"
    if not driver:
        return None, access
    return resolve_instrument_descriptor(driver, expected_kind=kind), access


def _json_payload(value: object) -> object:
    if isinstance(value, (list, tuple)):
        return [_json_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_payload(item) for key, item in value.items()}
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return as_dict()
    if is_dataclass(value):
        return asdict(value)
    return value


def _source_basic_configure_v2_request(args: argparse.Namespace):
    from .instruments.source_extensions import (
        PatchAction,
        PatchValue,
        SourceBasicConfigureRequest,
        SourceBasicPatch,
        SourceWaveformKind,
    )

    values = {
        "waveform_kind": args.waveform,
        "frequency_hz": args.frequency_hz,
        "amplitude_vpp": args.amplitude_vpp,
        "offset_v": args.offset_v,
        "square_duty_cycle_percent": args.square_duty_cycle_percent,
    }
    if all(value is None for value in values.values()):
        raise ConfigError("source basic-configure-v2 requires at least one basic field")

    waveform = (
        PatchValue(PatchAction.SET, SourceWaveformKind(args.waveform))
        if args.waveform is not None
        else PatchValue(PatchAction.KEEP)
    )

    def patch_value(value: object):
        return (
            PatchValue(PatchAction.SET, value)
            if value is not None
            else PatchValue(PatchAction.KEEP)
        )

    return SourceBasicConfigureRequest(
        channel=args.channel,
        patch=SourceBasicPatch(
            waveform_kind=waveform,
            frequency_hz=patch_value(args.frequency_hz),
            amplitude_vpp=patch_value(args.amplitude_vpp),
            offset_v=patch_value(args.offset_v),
            square_duty_cycle_percent=patch_value(args.square_duty_cycle_percent),
        ),
    )


def _source_cross_channel_configure_v2_request(
    args: argparse.Namespace,
    request_type: type[object],
) -> object:
    try:
        return request_type(channels=tuple(args.channel), enabled=args.state == "on")  # type: ignore[operator]
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _source_modulation_configure_v2_request(args: argparse.Namespace):
    from .instruments.source_extensions import SourceModulationConfigureRequest

    try:
        return SourceModulationConfigureRequest(
            channel=args.channel,
            depth_percent=args.depth_percent,
            internal_frequency_hz=args.internal_frequency_hz,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _source_pulse_configure_v2_request(args: argparse.Namespace):
    from .instruments.source_extensions import SourcePulseConfigureRequest

    try:
        return SourcePulseConfigureRequest(
            channel=args.channel,
            width_s=args.width_s,
            delay_s=args.delay_s,
            leading_transition_s=args.leading_transition_s,
            trailing_transition_s=args.trailing_transition_s,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _source_pm_modulation_configure_v2_request(args: argparse.Namespace):
    from .instruments.source_extensions import SourcePmModulationConfigureRequest

    try:
        return SourcePmModulationConfigureRequest(
            channel=args.channel,
            phase_deviation_deg=args.phase_deviation_deg,
            internal_frequency_hz=args.internal_frequency_hz,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _source_fm_modulation_configure_v2_request(args: argparse.Namespace):
    from .instruments.source_extensions import SourceFmModulationConfigureRequest

    try:
        return SourceFmModulationConfigureRequest(
            channel=args.channel,
            frequency_deviation_hz=args.frequency_deviation_hz,
            internal_frequency_hz=args.internal_frequency_hz,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _source_pwm_modulation_configure_v2_request(args: argparse.Namespace):
    from .instruments.source_extensions import SourcePwmModulationConfigureRequest

    try:
        return SourcePwmModulationConfigureRequest(
            channel=args.channel,
            internal_frequency_hz=args.internal_frequency_hz,
            duty_deviation_percent=args.duty_deviation_percent,
            width_deviation_s=args.width_deviation_s,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _source_sweep_configure_v2_request(args: argparse.Namespace):
    from .instruments.source_extensions import (
        SourceSweepConfigureRequest,
        SourceSweepSpacing,
    )

    try:
        return SourceSweepConfigureRequest(
            channel=args.channel,
            start_hz=args.start_hz,
            stop_hz=args.stop_hz,
            spacing=SourceSweepSpacing(args.spacing),
            steps=args.steps,
            sweep_time_s=args.sweep_time_s,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _source_arbitrary_storage_v2_request(
    args: argparse.Namespace,
) -> tuple[object, bytes]:
    from .instruments.source_extensions import (
        SourceArbitraryStorageRequest,
        SourceStorageWriteMode,
    )

    payload_path = Path(args.payload_file)
    try:
        payload = payload_path.read_bytes()
    except OSError as exc:
        raise ConfigError(
            f"source.arbitrary_storage_v2 payload file is unreadable: {payload_path}"
        ) from exc
    write_mode = {
        "create-only": SourceStorageWriteMode.CREATE_ONLY,
        "replace-if-digest-matches": SourceStorageWriteMode.REPLACE_IF_DIGEST_MATCHES,
    }[args.write_mode]
    try:
        return (
            SourceArbitraryStorageRequest(
                channel=args.channel,
                slot_id=args.slot_id,
                write_mode=write_mode,
                payload_sha256="sha256:" + sha256(payload).hexdigest(),
                payload_size_bytes=len(payload),
                expected_previous_sha256=args.expected_previous_sha256,
            ),
            payload,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _source_arbitrary_select_v2_request(args: argparse.Namespace):
    from .instruments.source_extensions import (
        SourceArbitraryPlaybackMode,
        SourceArbitrarySelectRequest,
    )

    playback_mode = {
        "dds": SourceArbitraryPlaybackMode.DDS,
        "true-arb": SourceArbitraryPlaybackMode.TRUE_ARB,
    }[args.playback_mode]
    try:
        return SourceArbitrarySelectRequest(
            channel=args.channel,
            slot_id=args.slot_id,
            playback_mode=playback_mode,
            playback_frequency_hz=args.playback_frequency_hz,
            sample_rate_hz=args.sample_rate_hz,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _source_burst_configure_v2_request(args: argparse.Namespace):
    from .instruments.source_extensions import SourceBurstConfigureRequest

    try:
        return SourceBurstConfigureRequest(
            channel=args.channel,
            cycles=args.cycles,
            phase_deg=args.phase_deg,
            internal_period_s=args.internal_period_s,
            delay_s=args.delay_s,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _scope_error_check(args: argparse.Namespace) -> ErrorCheckSpec | None:
    policy = getattr(args, "error_policy", None)
    if policy is None:
        return None
    try:
        return ErrorCheckSpec(
            policy=policy,
            timing=args.error_timing,
            max_records=args.error_max_records,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc


def _scope_trace_ref(args: argparse.Namespace) -> ScopeTraceRef:
    try:
        return ScopeTraceRef(
            kind=args.trace_kind,
            index=args.trace_index,
            name=args.trace_name,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc


def _scope_trace_points(raw: str) -> str | int:
    if raw == "dmax":
        return raw
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError("scope trace --points must be 'dmax' or a positive integer") from exc
    if value < 1:
        raise ConfigError("scope trace --points must be 'dmax' or a positive integer")
    return value


def _new_cli_output_path(raw: str, *, suffix: str, label: str) -> Path:
    path = Path(raw).expanduser()
    if path.suffix.lower() != suffix:
        raise ConfigError(f"{label} must use the {suffix} suffix")
    if path.exists():
        raise ConfigError(f"{label} already exists: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryFile(dir=path.parent):
            pass
    except OSError as exc:
        raise ConfigError(f"{label} directory is not writable: {path.parent}") from exc
    return path


def _write_scope_artifact(path: Path, payload: dict[str, object]) -> None:
    try:
        with path.open("x", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
            file.write("\n")
    except OSError as exc:
        raise ConfigError(f"failed to write scope operation artifact: {path}") from exc


def _scope_error_payload(exc: BaseException) -> dict[str, object]:
    payload: dict[str, object] = error_envelope(exc)
    diagnostics = getattr(exc, "scope_operation_diagnostics", None)
    if isinstance(diagnostics, Mapping):
        payload["operation_diagnostics"] = _json_payload(dict(diagnostics))
    artifact_error = getattr(exc, "scope_failure_artifact_error", None)
    if artifact_error == "write_failed":
        payload["scope_artifact"] = {
            "status": "failed",
            "reason_code": "write_failed",
        }
    cleanup_error = getattr(exc, "scope_output_cleanup_error", None)
    if cleanup_error == "remove_failed":
        payload["scope_output"] = {
            "status": "partial_cleanup_failed",
            "reason_code": "remove_failed",
        }
    source_operation_artifact = getattr(exc, "source_operation_artifact", None)
    if isinstance(source_operation_artifact, Mapping):
        payload["source_operation_artifact"] = _json_payload(
            dict(source_operation_artifact)
        )
    return payload


def _write_scope_failure_artifact(path: Path, exc: WaveBenchError) -> None:
    diagnostics = getattr(exc, "scope_operation_diagnostics", None)
    if not isinstance(diagnostics, Mapping):
        return
    try:
        _write_scope_artifact(
            path,
            {
                "schema": "wavebench.scope.result.v1",
                "status": "failed",
                "result": None,
                "diagnostics": _json_payload(dict(diagnostics)),
                "observed_state": None,
                "error": _scope_error_payload(exc),
                "files": {"artifact": path.name},
            },
        )
    except ConfigError:
        setattr(exc, "scope_failure_artifact_error", "write_failed")


def _attach_scope_result_diagnostics(
    exc: WaveBenchError,
    payload: Mapping[str, object],
) -> None:
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, Mapping):
        setattr(exc, "scope_operation_diagnostics", dict(diagnostics))


def _remove_failed_scope_output(path: Path, exc: WaveBenchError) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        setattr(exc, "scope_output_cleanup_error", "remove_failed")


def _scope_output_write_error(
    *,
    artifact_path: Path,
    result_payload: Mapping[str, object],
    message: str,
) -> ConfigError:
    error = ConfigError(message)
    _attach_scope_result_diagnostics(error, result_payload)
    _write_scope_failure_artifact(artifact_path, error)
    return error


def _emit_scope_extension_result(
    payload: dict[str, object],
    *,
    json_mode: bool,
) -> None:
    if json_mode:
        _emit_json_result(payload)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))


def _emit_json_result(payload: object, *, status: str = "ok", exit_code: int = 0) -> None:
    print(
        json.dumps(
            {
                "schema": CLI_RESULT_SCHEMA,
                "status": status,
                "exit_code": exit_code,
                "result": payload,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def _run_plan_payload(plan) -> dict[str, object]:
    return {
        "name": plan.name,
        "label": plan.label,
        "safety": _json_payload(plan.safety),
        "restore": _json_payload(plan.restore),
        "steps": [
            {
                "index": step.index,
                "kind": step.kind,
                "fields": _json_payload(step.fields),
            }
            for step in plan.steps
        ],
    }



def _main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if "--json" in raw_argv:
        raw_argv = [item for item in raw_argv if item != "--json"]
        raw_argv.insert(0, "--json")
    args = parser.parse_args(raw_argv)
    try:
        if args.domain == "plugin":
            lifecycle_commands = {
                "install",
                "installed",
                "remove",
                "upgrade",
                "downgrade",
                "recover",
            }
            if args.command == "package":
                if args.package_command == "check":
                    import tempfile

                    with tempfile.TemporaryDirectory(prefix="wavebench-plugin-check-") as temporary:
                        package = inspect_plugin_package(
                            args.path,
                            build_directory=temporary,
                            python_executable=sys.executable,
                        )
                    _print_plugin_package(package)
                    return 0
            if args.command in lifecycle_commands or (
                args.command == "info" and args.installed
            ):
                lifecycle = PluginLifecycle(python_executable=sys.executable)
                if args.command == "installed":
                    _print_installed_plugins(lifecycle.installed())
                    return 0
                if args.command == "info":
                    _print_installed_plugin(lifecycle.info(args.driver_id))
                    return 0
                if args.command == "install":
                    _print_lifecycle_result(lifecycle.install(args.path, dry_run=args.dry_run))
                    return 0
                if args.command == "remove":
                    _print_lifecycle_result(lifecycle.remove(args.driver_id, dry_run=args.dry_run))
                    return 0
                if args.command == "upgrade":
                    _print_lifecycle_result(lifecycle.upgrade(args.path, dry_run=args.dry_run))
                    return 0
                if args.command == "downgrade":
                    _print_lifecycle_result(lifecycle.downgrade(args.path, dry_run=args.dry_run))
                    return 0
                if args.command == "recover":
                    _print_lifecycle_result(lifecycle.recover())
                    return 0
            if getattr(args, "load", False):
                executable_registry = build_instrument_registry()
                if args.command == "info":
                    descriptor = executable_registry.resolve(args.driver_id)
                    _print_instrument_descriptor(descriptor)
                    return 0
                loaded = executable_registry.load_all()
                if args.command == "list":
                    descriptors = loaded.descriptors
                    if args.kind is not None:
                        descriptors = tuple(item for item in descriptors if item.kind == args.kind)
                    _print_plugin_list([item.to_metadata() for item in descriptors])
                    for error in loaded.load_errors:
                        print(f"error\t{error.source}\t{error.message}")
                    return 2 if loaded.load_errors else 0
                if args.command == "doctor":
                    records = [
                        *(
                            PluginDoctorRecord("error", error.source, error.message)
                            for error in loaded.load_errors
                        ),
                        *(
                            PluginDoctorRecord(
                                "ok",
                                descriptor.driver_id,
                                "executable descriptor valid / 可执行描述符有效",
                            )
                            for descriptor in loaded.descriptors
                        ),
                    ]
                    _print_plugin_doctor(records)
                    return 2 if loaded.load_errors else 0
            result = build_plugin_registry(include_entry_points=getattr(args, "include_entry_points", False))
            registry = result.registry
            if args.command == "list":
                _print_plugin_list(registry.list_plugins(kind=args.kind))
                return 0
            if args.command == "info":
                _print_plugin_info(registry.get(args.driver_id))
                return 0
            if args.command == "doctor":
                records = plugin_doctor_records(registry, load_errors=result.load_errors)
                _print_plugin_doctor(records)
                return 2 if has_doctor_errors(records) else 0
            if args.command == "market":
                market = load_market_index(args.index)
                if args.market_command == "search":
                    _print_market_search_results(market.search(args.query))
                    return 0
                if args.market_command == "info":
                    _print_market_plugin_info(market.get(args.plugin_id))
                    return 0
            if args.command == "scpi":
                if args.scpi_command == "check":
                    records = scpi_plugin_doctor_records(args.path)
                    _print_scpi_doctor(records)
                    return 2 if has_scpi_doctor_errors(records) else 0
                if args.scpi_command == "doctor":
                    if args.resource and not args.probe:
                        raise ConfigError("--resource requires --probe")
                    if args.probe and not args.resource:
                        raise ConfigError("--probe requires --resource")
                    records = scpi_plugin_doctor_records(
                        args.path,
                        probe_resource=args.resource if args.probe else None,
                        backend=args.backend,
                        timeout_ms=args.timeout_ms,
                    )
                    _print_scpi_doctor(records)
                    return 2 if has_scpi_doctor_errors(records) else 0
                if args.scpi_command == "info":
                    _print_scpi_plugin_info(load_scpi_plugin(args.path))
                    return 0
                if args.scpi_command == "probe":
                    result = probe_scpi_plugin(
                        args.path,
                        resource=args.resource,
                        backend=args.backend,
                        timeout_ms=args.timeout_ms,
                    )
                    _print_scpi_probe_result(result)
                    return 0 if result.matched else 2
        if args.domain == "net":
            if args.command == "discover":
                results = discover_instruments(
                    subnet=args.subnet,
                    ports=args.ports,
                    timeout_ms=args.timeout_ms,
                    workers=args.workers,
                    max_hosts=args.max_hosts,
                    query_idn=not args.no_idn,
                    idn_only=args.idn_only,
                    include_visa=not args.no_visa,
                )
                if args.json:
                    _emit_json_result(_json_payload(results))
                else:
                    _print_discovery_results(results)
                return 0
        if args.domain == "capability":
            if args.command == "explain":
                if args.candidates:
                    spec = get_operation_spec(args.operation)
                    if spec is None:
                        result = explain_operation(args.operation)
                        candidates_payload = {
                            "operation": args.operation,
                            "candidates": [],
                            "explanation": result.as_dict(),
                        }
                        if args.json:
                            _emit_json_result(candidates_payload, status=result.status, exit_code=2)
                        else:
                            _print_capability_explanation(result)
                        return 2
                    if spec.instrument_kind is None:
                        raise ConfigError("--candidates requires an instrument operation")
                    registry = build_instrument_registry()
                    loaded = registry.load_all()
                    candidates = [
                        explain_operation(
                            args.operation,
                            descriptor=descriptor,
                            access=args.access or "read_write",
                        ).as_dict()
                        for descriptor in loaded.descriptors
                        if descriptor.kind == spec.instrument_kind
                    ]
                    supported = [item for item in candidates if item["status"] == "supported"]
                    candidates_payload = {
                        "operation": args.operation,
                        "candidates": candidates,
                        "supported_count": len(supported),
                    }
                    if args.json:
                        _emit_json_result(
                            candidates_payload,
                            status="ok" if supported else "failed",
                            exit_code=0 if supported else 2,
                        )
                    else:
                        print(f"operation={args.operation}")
                        print(f"supported_count={len(supported)}")
                        for item in candidates:
                            print(
                                f"candidate={item.get('driver_id') or 'n/a'}"
                                f"\tstatus={item['status']}"
                                f"\tmissing={','.join(item['missing_capabilities']) or 'none'}"
                            )
                    return 0 if supported else 2
                descriptor, access = _capability_target(args)
                result = explain_operation(
                    args.operation,
                    descriptor=descriptor,
                    access=access,
                )
                if args.json:
                    _emit_json_result(result.as_dict(), status=result.status, exit_code=0 if result.status == "supported" else 2)
                else:
                    _print_capability_explanation(result)
                return 0 if result.status == "supported" else 2
        if args.domain == "lock":
            if args.command == "status":
                payload = ResourceLease(
                    resource=args.resource,
                    lock_id=args.lock_id,
                ).status()
                if args.json:
                    _emit_json_result(payload)
                else:
                    for key, value in payload.items():
                        print(f"{key}={value}")
                return 0
        if args.domain == "doctor":
            records = doctor_records(
                load_config(args.config),
                timeout_ms=args.timeout_ms,
                discover_subnet=args.discover_subnet,
                discover_ports=args.discover_ports,
                discover_timeout_ms=args.discover_timeout_ms,
                discover_workers=args.discover_workers,
                discover_max_hosts=args.discover_max_hosts,
                include_visa=not args.no_visa,
            )
            failed = has_config_doctor_errors(records)
            if args.json:
                _emit_json_result(
                    _json_payload(records),
                    status="failed" if failed else "ok",
                    exit_code=2 if failed else 0,
                )
            else:
                _print_doctor_records(records)
            return 2 if failed else 0
        if args.domain == "tui":
            if args.refresh_interval <= 0:
                raise ConfigError("--refresh-interval must be > 0 / 刷新间隔必须 > 0")
            from .tui import run as run_tui

            return run_tui(
                config_path=args.config,
                resource=args.resource,
                fake=args.fake,
                refresh_interval_s=args.refresh_interval,
                log_path=args.log_file,
            )
        if args.domain == "capture":
            if args.command == "inspect":
                package = load_capture_package(args.path)
                _print_capture_package_summary(package)
                if args.fft:
                    _print_capture_fft_summary(
                        package,
                        max_harmonic_order=args.harmonics,
                        expected_frequency_hz=args.fft_expect_frequency,
                        frequency_tolerance_ratio=args.fft_frequency_tolerance,
                )
                return 0
        if args.domain == "mcp":
            if args.command == "serve":
                token = resolve_mcp_token(args.token, args.token_env)
                serve_mcp_http(
                    host=args.host,
                    port=args.port,
                    token=token,
                    config_path=args.config,
                )
                return 0
        if args.domain == "run":
            if args.command == "intent":
                plan = load_run_plan(args.plan)
                service = _load_run_service(args)
                service.check(plan)
                intent = build_execution_intent(plan, service.config)
                if args.output:
                    output = write_execution_intent(intent, args.output)
                    if not args.json:
                        print(f"intent={output}")
                if args.json or not args.output:
                    print(json.dumps(intent.as_dict(), indent=2, ensure_ascii=False))
                return 0
            if args.command == "calibrate":
                package = load_run_package(args.path)
                response = package.select_frequency_response(args.response)
                if response.csv_path is None:
                    raise ConfigError("run calibrate requires frequency_response.csv in the run directory")
                calibration = load_frequency_response_calibration_config(args.config)
                if not calibration.enabled:
                    raise ConfigError("calibration.enabled must be true for run calibrate")
                document, rows = build_frequency_response_calibration(
                    response.rows,
                    calibration,
                    source_csv=response.csv_path,
                )
                csv_path = write_frequency_response_calibration_csv(
                    response.directory / "frequency_response_calibration.csv", rows
                )
                fixed_paths = write_fixed_point_calibration(
                    response.directory, document, rows, calibration.fixed_point
                )
                json_path = write_frequency_response_calibration_json(
                    response.directory / "frequency_response_calibration.json", document
                )
                print(f"calibration_csv={csv_path}")
                print(f"calibration_json={json_path}")
                for name, path in fixed_paths.items():
                    print(f"calibration_fixed_{name}={path}")
                return 0
            if args.command == "compare":
                if len(args.paths) < 2:
                    raise ConfigError("run compare requires at least two run directories")
                try:
                    comparison = compare_run_packages_result(
                        args.paths,
                        response_labels=args.response,
                        output_path=args.output,
                        gain_tolerance_db=args.gain_tolerance_db,
                        phase_tolerance_deg=args.phase_tolerance_deg,
                    )
                except RunCompareError as exc:
                    raise ConfigError(str(exc)) from exc
                payload = comparison.as_dict()
                if args.json or args.format == "json":
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                else:
                    print(f"compare_schema={payload['schema_version']}")
                    print(f"reference={payload['reference'].get('path', '')}")
                    for item in payload["comparisons"]:
                        summary = item.get("summary", {})
                        print(
                            "candidate="
                            f"{item.get('candidate', {}).get('path', '')}"
                            f"\tstatus={item.get('status', '')}"
                            f"\tmatched={summary.get('matched', 0)}"
                            f"\tmissing_reference={summary.get('missing_reference', 0)}"
                            f"\tmissing_candidate={summary.get('missing_candidate', 0)}"
                        )
                    if payload.get("errors"):
                        print(f"errors={len(payload['errors'])}")
                if args.output and not (args.json or args.format == "json"):
                    print(f"compare_json={args.output}")
                statuses = [item.get("status") for item in payload.get("comparisons", [])]
                if payload.get("errors") or any(
                    status in {"incompatible", "unavailable", "duplicate", "out_of_tolerance"}
                    for status in statuses
                ):
                    return 2
                return 0
            if args.command == "resume":
                manifest = build_frequency_response_resume(
                    args.path,
                    args.plan,
                    response_label=args.response,
                )
                source_path = Path(args.path)
                output = (
                    Path(args.output)
                    if args.output
                    else (source_path / "frequency_response_resume.json" if source_path.is_dir() else source_path.parent / "frequency_response_resume.json")
                )
                if output.suffix.lower() != ".json":
                    output = output / "frequency_response_resume.json"
                manifest.write(output)
                print(f"resume_json={output}")
                print(f"source_csv={manifest.source_csv}")
                print(f"reusable={len(manifest.reusable_cases)}")
                print(f"pending={len(manifest.pending_cases)}")
                print(f"rejected={len(manifest.rejected_points)}")
                return 0
            if args.command == "report":
                if args.pdf_output and not args.pdf:
                    raise ConfigError("run report --pdf-output requires --pdf")
                package = load_run_package(args.path)
                output = Path(args.output) if args.output else package.path / "report.html"
                pdf_output = (
                    Path(args.pdf_output) if args.pdf_output else output.with_suffix(".pdf")
                )
                if args.pdf and output.resolve() == pdf_output.resolve():
                    raise ConfigError("run report HTML and PDF outputs must use different paths")
                if args.pdf and output.suffix.lower() == ".pdf":
                    raise ConfigError("run report --output is an HTML path and must not use a .pdf suffix")
                if args.pdf and pdf_output.suffix.lower() in {".htm", ".html"}:
                    raise ConfigError(
                        "run report --pdf-output is a PDF path and must not use an HTML suffix"
                    )
                output = write_run_report_html(package, output_path=output)
                print(f"report={output}")
                if args.pdf:
                    pdf = write_run_report_pdf(package, output_path=pdf_output)
                    print(f"pdf={pdf}")
                return 0
            if args.command == "report-index":
                result = write_report_index(args.paths, args.output)
                print(f"manifest_json={result.manifest_json_path}")
                print(f"manifest_csv={result.manifest_csv_path}")
                print(f"runs={result.count}")
                print(f"index_html={result.index_html_path}")
                return 0
            if args.command == "check":
                plan = load_run_plan(args.plan)
                _load_run_service(args).check(plan)
                if args.json:
                    _emit_json_result(
                        {
                            "plan": _run_plan_payload(plan),
                            "safety_limits": "ok",
                        }
                    )
                else:
                    _print_run_plan_summary(plan)
                    print("safety_limits=ok / 安全上限=通过")
                return 0
            if args.command == "verify":
                plan = load_run_plan(args.plan)
                records = _load_run_service(args).verify(plan)
                if args.json:
                    _emit_json_result(_json_payload(records))
                else:
                    _print_run_preflight(records)
                return 0
            if args.command == "schema":
                print(format_run_plan_schema())
                return 0
            if args.command == "template":
                if args.list:
                    for item in list_run_templates():
                        print(f"{item.name}\t{item.description}")
                    return 0
                if not args.template:
                    raise ConfigError("run template requires a template name or --list")
                if not args.output and not args.print_template:
                    raise ConfigError("run template requires --output or --print")
                template_options = RunTemplateOptions(
                    frequency_hz=args.frequency,
                    frequencies_hz=parse_frequencies(args.frequencies),
                    vpp=args.vpp,
                    source_channel=args.source_channel,
                    scope_channel=args.scope_channel,
                    reference_channel=args.reference_channel,
                    response_channel=args.response_channel,
                    frequency_response_fit=args.frequency_response_fit,
                    power_channel=args.power_channel,
                    voltage_v=args.voltage,
                    current_limit_a=args.current_limit,
                )
                if args.print_template:
                    print(render_run_template(args.template, options=template_options), end="")
                if args.output:
                    output = write_run_template(args.template, args.output, force=args.force, options=template_options)
                    print(f"template={args.template}")
                    print(f"output={output}")
                return 0
            if args.command == "plan":
                plan = load_run_plan(args.plan)
                expected_intent = (
                    load_execution_intent(args.intent) if args.intent else None
                )
                service = _load_run_service(args)
                if expected_intent is None:
                    result = service.run(plan)
                else:
                    result = service.run(plan, execution_intent=expected_intent)
                failed = any(step.status == "failed" for step in result.steps)
                if args.json:
                    _emit_json_result(
                        {
                            "run_dir": str(result.run_dir),
                            "run_json": str(result.run_json_path),
                            "summary": str(result.summary_csv_path),
                            "steps": _json_payload(result.steps),
                        },
                        status="failed" if failed else "ok",
                        exit_code=2 if failed else 0,
                    )
                else:
                    print(f"run={result.run_dir}")
                    print(f"run_json={result.run_json_path}")
                    print(f"summary={result.summary_csv_path}")
                    print(f"steps={len(result.steps)}")
                return 2 if failed else 0
        if args.domain == "dmm":
            service = _load_dmm_service(args)
            if args.command == "idn":
                print(service.idn())
                return 0
            if args.command == "read":
                _print_dmm_reading(service.read(function=args.function))
                return 0
            if args.command == "profile":
                _print_dmm_measurement_profile(service.measurement_profile())
                return 0
            if args.command == "function":
                if args.dmm_function_command == "status":
                    _print_dmm_function_status(service.function_status())
                    return 0
                if args.dmm_function_command == "set":
                    _print_dmm_function_set(service.set_function(function=args.function))
                    return 0
            if args.command == "range" and args.dmm_range_command == "set":
                _print_dmm_voltage_range_configuration(
                    service.set_voltage_range(
                        function=args.function,
                        range_code=args.range_code,
                    )
                )
                return 0
            if args.command == "impedance" and args.dmm_impedance_command == "set":
                _print_dmm_dcv_impedance_configuration(
                    service.set_dcv_impedance(args.impedance)
                )
                return 0
            if args.command == "trigger" and args.dmm_trigger_command == "status":
                _print_dmm_trigger_status(service.trigger_status())
                return 0
            if args.command == "calculation":
                if args.dmm_calculation_command == "status":
                    _print_dmm_calculation_status(service.calculation_status())
                    return 0
                if args.dmm_calculation_command == "statistics":
                    _print_dmm_calculation_statistics(
                        service.calculation_statistics(
                            args.function,
                            calculation_active_confirmed=args.calculation_active_confirmed,
                        )
                    )
                    return 0
            if (
                args.command == "system-interface"
                and args.dmm_system_interface_command == "status"
            ):
                _print_dmm_system_interface_status(service.system_interface_status())
                return 0
        if args.domain == "power":
            service = _load_power_service(args)
            if args.command == "idn":
                print(service.idn())
                return 0
            if args.command == "status":
                result = service.status(channel=args.channel)
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    _print_power_status(result)
                return 0
            if args.command == "set":
                _print_power_status(
                    service.set_voltage_current_limit(
                        channel=args.channel,
                        voltage_v=args.voltage,
                        current_limit_a=args.current_limit,
                    )
                )
                return 0
            if args.command == "output":
                _print_power_status(service.set_output(channel=args.channel, enabled=args.state == "on"))
                return 0
            if args.command == "protection":
                if args.protection_command == "status":
                    _print_power_protection_status(service.protection_status(channel=args.channel))
                    return 0
                if args.protection_command == "set":
                    _print_power_protection_status(
                        service.set_protection(
                            channel=args.channel,
                            ovp_threshold_v=args.ovp_threshold,
                            ovp_enabled=None if args.ovp is None else args.ovp == "on",
                            ocp_threshold_a=args.ocp_threshold,
                            ocp_enabled=None if args.ocp is None else args.ocp == "on",
                        )
                    )
                    return 0
        if args.domain == "source":
            if args.command == "arb-load":
                if args.amplitude <= 0:
                    raise ConfigError("--amplitude must be > 0")
                if args.dry_run:
                    _print_arbitrary_waveform_summary(args, dry_run=True)
                    return 0
                if args.frequency is None or args.frequency <= 0:
                    raise ConfigError("--frequency must be > 0 when uploading")
                service = _load_source_service(args)
                _print_arbitrary_waveform_summary(args, dry_run=False)
                status = service.upload_arbitrary_waveform(
                    channel=args.channel,
                    file_path=args.file,
                    playback_frequency_hz=args.frequency,
                    amplitude_vpp=args.amplitude,
                    offset_v=args.offset,
                    sample_rate_hz=args.sample_rate,
                    max_points=args.max_points,
                    byte_order=args.dg4000_byte_order,
                    output_on=args.output_on,
                )
                print("upload=ok")
                _print_source_status(status)
                return 0
            if args.command == "arbitrary-storage-v2":
                request, storage_payload = _source_arbitrary_storage_v2_request(args)
                service = _load_source_service(args)
                _, payload = service.mutate_arbitrary_storage_v2(
                    request,
                    payload=storage_payload,
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            service = _load_source_service(args)
            if args.command == "idn":
                print(service.idn())
                return 0
            if args.command == "errors":
                for item in service.errors():
                    print(item)
                return 0
            if args.command == "arb-probe":
                _print_arbitrary_probe_results(service.probe_arbitrary_queries(channel=args.channel))
                return 0
            if args.command == "status":
                result = service.status(channel=args.channel)
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    _print_source_status(result)
                return 0
            if args.command == "snapshot-v2":
                from wavebench.instruments.source_extensions import (
                    source_snapshot_v2_operation_artifact,
                )

                payload = source_snapshot_v2_operation_artifact(service.snapshot_v2())
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "arbitrary-select-v2":
                _, payload = service.select_arbitrary_v2(
                    _source_arbitrary_select_v2_request(args)
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "combine-configure-v2":
                from wavebench.instruments.source_extensions import (
                    SourceCombineConfigureRequest,
                )

                _, payload = service.configure_combine_v2(
                    _source_cross_channel_configure_v2_request(
                        args,
                        SourceCombineConfigureRequest,
                    )
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "coupling-configure-v2":
                from wavebench.instruments.source_extensions import (
                    SourceCouplingConfigureRequest,
                )

                _, payload = service.configure_coupling_v2(
                    _source_cross_channel_configure_v2_request(
                        args,
                        SourceCouplingConfigureRequest,
                    )
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "tracking-configure-v2":
                from wavebench.instruments.source_extensions import (
                    SourceTrackingConfigureRequest,
                )

                _, payload = service.configure_tracking_v2(
                    _source_cross_channel_configure_v2_request(
                        args,
                        SourceTrackingConfigureRequest,
                    )
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "phase-relation-configure-v2":
                from wavebench.instruments.source_extensions import (
                    SourcePhaseRelationConfigureRequest,
                )

                _, payload = service.configure_phase_relation_v2(
                    _source_cross_channel_configure_v2_request(
                        args,
                        SourcePhaseRelationConfigureRequest,
                    )
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "basic-configure-v2":
                _, payload = service.configure_basic_v2(
                    _source_basic_configure_v2_request(args)
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "output-v2":
                from wavebench.instruments.source_extensions import SourceOutputRequest

                _, payload = service.set_output_v2(
                    SourceOutputRequest(
                        channel=args.channel,
                        enabled=args.state == "on",
                    )
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "harmonics-configure-v2":
                from wavebench.instruments.source_extensions import (
                    SourceHarmonicConfigureRequest,
                    SourceHarmonicPreset,
                )

                _, payload = service.configure_harmonics_v2(
                    SourceHarmonicConfigureRequest(
                        channel=args.channel,
                        order=args.order,
                        preset=SourceHarmonicPreset(args.preset),
                    )
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "harmonics-disable-v2":
                from wavebench.instruments.source_extensions import SourceHarmonicDisableRequest

                _, payload = service.disable_harmonics_v2(
                    SourceHarmonicDisableRequest(channel=args.channel)
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "modulation-configure-v2":
                _, payload = service.configure_modulation_v2(
                    _source_modulation_configure_v2_request(args)
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "pulse-configure-v2":
                _, payload = service.configure_pulse_v2(
                    _source_pulse_configure_v2_request(args)
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "pm-modulation-configure-v2":
                _, payload = service.configure_pm_modulation_v2(
                    _source_pm_modulation_configure_v2_request(args)
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "fm-modulation-configure-v2":
                _, payload = service.configure_fm_modulation_v2(
                    _source_fm_modulation_configure_v2_request(args)
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "pwm-modulation-configure-v2":
                _, payload = service.configure_pwm_modulation_v2(
                    _source_pwm_modulation_configure_v2_request(args)
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "sweep-configure-v2":
                _, payload = service.configure_sweep_v2(
                    _source_sweep_configure_v2_request(args)
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "burst-configure-v2":
                _, payload = service.configure_burst_v2(
                    _source_burst_configure_v2_request(args)
                )
                if args.json:
                    _emit_json_result(payload)
                else:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if args.command == "profile":
                _print_source_channel_profile(service.channel_profile(channel=args.channel))
                return 0
            if args.command == "pulse-profile":
                _print_source_pulse_profile(service.pulse_profile(channel=args.channel))
                return 0
            if args.command == "burst-profile":
                _print_source_burst_profile(service.burst_profile(channel=args.channel))
                return 0
            if args.command == "sweep-profile":
                _print_source_sweep_profile(service.sweep_profile(channel=args.channel))
                return 0
            if args.command == "counter-profile":
                _print_source_counter_profile(service.counter_profile())
                return 0
            if args.command == "set-freq":
                _print_source_status(service.set_frequency(channel=args.channel, value_hz=args.value_hz))
                return 0
            if args.command == "output":
                _print_source_status(service.set_output(channel=args.channel, enabled=args.state.lower() == "on"))
                return 0
            if args.command == "set-func":
                _print_source_status(service.set_function(channel=args.channel, function=args.function))
                return 0
            if args.command == "set-vpp":
                _print_source_status(service.set_amplitude_vpp(channel=args.channel, value_vpp=args.value_vpp))
                return 0
            if args.command == "set-duty":
                _print_source_status(service.set_square_duty_cycle(channel=args.channel, duty_percent=args.duty_percent))
                return 0
        if args.domain == "rf-source":
            service = _load_rf_source_service(args)
            if args.command == "idn":
                print(service.idn())
                return 0
            if args.command == "status":
                result = service.snapshot()
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    print(json.dumps(_json_payload(result), indent=2, ensure_ascii=False))
                return 0
            if args.command == "trigger":
                result = service.trigger_snapshot(args.port)
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    print(json.dumps(_json_payload(result), indent=2, ensure_ascii=False))
                return 0
            if args.command == "set-frequency":
                result = service.configure_cw(
                    RfCwRequest(port_id=args.port, frequency_hz=args.frequency_hz)
                )
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    print(json.dumps(_json_payload(result), indent=2, ensure_ascii=False))
                return 0
            if args.command == "set-power":
                result = service.configure_cw(
                    RfCwRequest(port_id=args.port, power_dbm=args.power_dbm)
                )
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    print(json.dumps(_json_payload(result), indent=2, ensure_ascii=False))
                return 0
            if args.command == "modulation":
                if args.modulation_command == "disable":
                    result = service.disable_modulation(
                        RfModulationDisableRequest(
                            port_id=args.port,
                            kind=RfModulationKind(args.modulation_kind),
                        )
                    )
                    if args.json:
                        _emit_json_result(_json_payload(result))
                    else:
                        print(json.dumps(_json_payload(result), indent=2, ensure_ascii=False))
                    return 0
                modulation_kind = {
                    "configure-am": RfModulationKind.AM,
                    "configure-fm": RfModulationKind.FM,
                    "configure-pm": RfModulationKind.PM,
                    "enable-output-am": RfModulationKind.AM,
                    "enable-output-fm": RfModulationKind.FM,
                    "enable-output-pm": RfModulationKind.PM,
                }[args.modulation_command]
                request_fields = {
                    "port_id": args.port,
                    "kind": modulation_kind,
                    "internal_frequency_hz": args.internal_frequency_hz,
                }
                if modulation_kind is RfModulationKind.AM:
                    request_fields["depth_percent"] = args.depth_percent
                elif modulation_kind is RfModulationKind.FM:
                    request_fields["frequency_deviation_hz"] = args.frequency_deviation_hz
                else:
                    request_fields["phase_deviation_rad"] = args.phase_deviation_rad
                request = RfModulationRequest(**request_fields)
                if args.modulation_command.startswith("enable-output-"):
                    result = service.enable_modulated_output(
                        RfModulatedOutputRequest(modulation=request)
                    )
                else:
                    result = service.configure_modulation(request)
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    print(json.dumps(_json_payload(result), indent=2, ensure_ascii=False))
                return 0
            if args.command == "pulse":
                result = service.configure_pulse(
                    RfPulseConfigureRequest(
                        port_id=args.port,
                        period_s=args.period_s,
                        width_s=args.width_s,
                        polarity=RfPulsePolarity(args.polarity),
                    )
                )
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    print(json.dumps(_json_payload(result), indent=2, ensure_ascii=False))
                return 0
            if args.command == "sweep":
                result = service.configure_sweep(
                    RfSweepConfigureRequest(
                        port_id=args.port,
                        start_frequency_hz=args.start_frequency_hz,
                        stop_frequency_hz=args.stop_frequency_hz,
                        points=args.points,
                        dwell_s=args.dwell_s,
                    )
                )
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    print(json.dumps(_json_payload(result), indent=2, ensure_ascii=False))
                return 0
            if args.command == "output":
                result = service.set_output(
                    RfOutputRequest(port_id=args.port, enabled=args.state == "on")
                )
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    print(json.dumps(_json_payload(result), indent=2, ensure_ascii=False))
                return 0
        if args.domain == "sweep":
            service = _load_sweep_service(args)
            if args.command == "discrete":
                try:
                    frequencies = parse_frequency_list(args.frequencies)
                except ValueError as exc:
                    raise ConfigError(str(exc)) from exc
                scope_channel = args.scope_channel or service.config.scope.default_channel
                ScopeService(config=service.config, logger=CommandLogger()).require_high_impedance(scope_channel, allow_50ohm=args.allow_50ohm)
                result = service.run_discrete(
                    frequencies_hz=frequencies,
                    source_channel=args.source_channel,
                    scope_channel=args.scope_channel,
                    target_cycles=args.target_cycles,
                    frequency_tolerance=args.frequency_tolerance,
                    label=args.label,
                    save_csv=not args.no_csv,
                    save_npy=not args.no_npy,
                    source_function=args.source_func,
                    source_vpp=args.source_vpp,
                    restore_source_state=args.restore_source_state,
                )
                for row in result.rows:
                    measured = "n/a" if row.measured_frequency_hz is None else f"{row.measured_frequency_hz:.6g}"
                    ok = "n/a" if row.frequency_in_tolerance is None else str(row.frequency_in_tolerance)
                    print(f"{row.index}: set={row.set_frequency_hz:.6g}Hz measured≈{measured}Hz ok={ok} package={row.package}")
                print(f"summary={result.summary_path}")
                return 0
        if args.domain == "scope":
            service = _load_service(args)
            if args.command == "idn":
                print(service.idn())
                return 0
            if args.command == "errors":
                for item in service.errors():
                    print(item)
                return 0
            if args.command == "screenshot":
                if args.screenshot_command == "profile":
                    result = service.screenshot_profile()
                    _emit_scope_extension_result(result.as_dict(), json_mode=args.json)
                    return 0
                output_path = _new_cli_output_path(
                    args.output,
                    suffix=".png",
                    label="scope screenshot output",
                )
                artifact_path = _new_cli_output_path(
                    args.artifact or f"{output_path}.json",
                    suffix=".json",
                    label="scope screenshot artifact",
                )
                if output_path.resolve() == artifact_path.resolve():
                    raise ConfigError("scope screenshot output and artifact paths must differ")
                request = ScopeScreenshotRequest(
                    menu_mode=args.menu_mode,
                    color_mode=args.color_mode,
                )
                try:
                    result = service.screenshot_v2(
                        request,
                        error_check=_scope_error_check(args),
                    )
                except WaveBenchError as exc:
                    _write_scope_failure_artifact(artifact_path, exc)
                    raise
                if not isinstance(result.value, ScopeScreenshot):
                    raise ConfigError("scope screenshot Service returned an invalid result")
                payload = result.as_dict()
                try:
                    with output_path.open("xb") as file:
                        file.write(result.value.data)
                except OSError as exc:
                    error = _scope_output_write_error(
                        artifact_path=artifact_path,
                        result_payload=payload,
                        message="failed to write scope screenshot output",
                    )
                    _remove_failed_scope_output(output_path, error)
                    raise error from exc
                payload["files"] = {
                    "screenshot": output_path.name,
                    "artifact": artifact_path.name,
                }
                try:
                    _write_scope_artifact(artifact_path, payload)
                except ConfigError as exc:
                    _attach_scope_result_diagnostics(exc, payload)
                    setattr(exc, "scope_failure_artifact_error", "write_failed")
                    _remove_failed_scope_output(output_path, exc)
                    raise
                payload["files"] = {
                    "screenshot": str(output_path),
                    "artifact": str(artifact_path),
                }
                _emit_scope_extension_result(payload, json_mode=args.json)
                return 0
            if args.command == "acquisition":
                error_check = _scope_error_check(args)
                if args.acquisition_command == "status":
                    result = service.acquisition_run_state()
                elif args.acquisition_command == "start":
                    result = service.start_acquisition(
                        ScopeContinuousAcquisitionRequest(args.trigger_mode),
                        error_check=error_check,
                    )
                elif args.acquisition_command == "single":
                    result = service.acquire_single(error_check=error_check)
                else:
                    result = service.stop_acquisition(error_check=error_check)
                _emit_scope_extension_result(result.as_dict(), json_mode=args.json)
                return 0
            if args.command == "trace":
                source = _scope_trace_ref(args)
                if args.trace_command == "metadata":
                    result = service.trace_metadata(source)
                    _emit_scope_extension_result(result.as_dict(), json_mode=args.json)
                    return 0
                output_path = _new_cli_output_path(
                    args.output,
                    suffix=".npy",
                    label="scope trace output",
                )
                artifact_path = _new_cli_output_path(
                    args.artifact or f"{output_path}.json",
                    suffix=".json",
                    label="scope trace artifact",
                )
                if output_path.resolve() == artifact_path.resolve():
                    raise ConfigError("scope trace output and artifact paths must differ")
                try:
                    result = service.fetch_trace(
                        source,
                        points=_scope_trace_points(args.trace_points),
                        error_check=_scope_error_check(args),
                    )
                except WaveBenchError as exc:
                    _write_scope_failure_artifact(artifact_path, exc)
                    raise
                if not isinstance(result.value, ScopeTraceData):
                    raise ConfigError("scope trace Service returned an invalid result")
                payload = result.as_dict()
                try:
                    with output_path.open("xb") as file:
                        np.save(file, result.value.values, allow_pickle=False)
                except OSError as exc:
                    error = _scope_output_write_error(
                        artifact_path=artifact_path,
                        result_payload=payload,
                        message="failed to write scope trace output",
                    )
                    _remove_failed_scope_output(output_path, error)
                    raise error from exc
                payload["files"] = {
                    "trace": output_path.name,
                    "artifact": artifact_path.name,
                }
                try:
                    _write_scope_artifact(artifact_path, payload)
                except ConfigError as exc:
                    _attach_scope_result_diagnostics(exc, payload)
                    setattr(exc, "scope_failure_artifact_error", "write_failed")
                    _remove_failed_scope_output(output_path, exc)
                    raise
                payload["files"] = {
                    "trace": str(output_path),
                    "artifact": str(artifact_path),
                }
                _emit_scope_extension_result(payload, json_mode=args.json)
                return 0
            if args.command == "status":
                channel = args.channel or service.config.scope.default_channel
                summary = getattr(service, "status_summary", None)
                if callable(summary):
                    result = summary(channel=channel, strict=args.strict)
                else:
                    result = service.status(channel=channel)
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    _print_scope_snapshot(result)
                return 0
            if args.command == "channel-input-state":
                channel = args.channel or service.config.scope.default_channel
                result = service.channel_input_state_v2(channel)
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    _print_scope_channel_input_state(result)
                return 0
            if args.command == "acquisition-status":
                _print_scope_acquisition_status(service.acquisition_status())
                return 0
            if args.command == "capture-average":
                _print_scope_average_capture(
                    service.capture_average(
                        channels=tuple(args.channel),
                        average_count=args.average_count,
                        acquisition_stopped=args.acquisition_stopped,
                        allow_50ohm=args.allow_50ohm,
                    )
                )
                return 0
            if args.command == "history-timestamps":
                channel = args.channel or service.config.scope.default_channel
                _print_scope_history_timestamps(service.history_timestamps(channel=channel))
                return 0
            if args.command == "digital-status":
                _print_scope_digital_status(service.digital_status(channel=args.channel))
                return 0
            if args.command == "digital-status-v2":
                result = service.digital_status_v2(channel=args.channel)
                if args.json:
                    _emit_json_result(_json_payload(result))
                else:
                    _print_scope_digital_status_v2(result)
                return 0
            if args.command == "digital-waveform":
                output_path = None
                if args.output:
                    output_path = Path(args.output).expanduser()
                    if output_path.suffix.lower() != ".npy":
                        raise ConfigError("digital waveform --output must use the .npy suffix")
                    if output_path.exists():
                        raise ConfigError(
                            f"digital waveform output already exists: {output_path}"
                        )
                    try:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        with TemporaryFile(dir=output_path.parent):
                            pass
                    except OSError as exc:
                        raise ConfigError(
                            f"digital waveform output directory is not writable "
                            f"{output_path.parent}: {exc}"
                        ) from exc
                waveform = service.digital_waveform(
                    channels=tuple(args.channel),
                    acquisition_stopped=args.acquisition_stopped,
                )
                if output_path is not None:
                    try:
                        with output_path.open("xb") as file:
                            np.save(file, waveform.samples, allow_pickle=False)
                    except OSError as exc:
                        raise ConfigError(
                            f"failed to write digital waveform output {output_path}: {exc}"
                        ) from exc
                _print_scope_digital_waveform(waveform, output_path=output_path)
                return 0
            if args.command == "measurement-statistics":
                _print_scope_measurement_statistics(
                    service.measurement_statistics(
                        args.slot,
                        configured_slot=args.configured_slot,
                        include_buffer=args.include_buffer,
                        acquisition_stopped=args.acquisition_stopped,
                    )
                )
                return 0
            if args.command == "math-metadata":
                _print_scope_derived_waveform_metadata(
                    service.math_waveform_metadata(args.index)
                )
                return 0
            if args.command == "fft-status":
                _print_scope_fft_status(
                    service.fft_status(
                        args.index,
                        configured_fft=args.configured_fft,
                    )
                )
                return 0
            if args.command == "reference-metadata":
                _print_scope_derived_waveform_metadata(
                    service.reference_waveform_metadata(args.index)
                )
                return 0
            if args.command == "cursor-readout":
                _print_scope_cursor_readout(
                    service.cursor_readout(
                        args.index,
                        configured_cursor=args.configured_cursor,
                    )
                )
                return 0
            if args.command in {"auto", "autoscale"}:
                service.autoscale()
                print("AUToscale completed")
                return 0
            if args.command == "fetch":
                channel = args.channel or service.config.scope.default_channel
                service.require_high_impedance(channel, allow_50ohm=args.allow_50ohm)
                waveform = service.fetch_waveform(channel=channel)
                _print_waveform_summary(waveform)
                return 0
            if args.command == "capture":
                channels = args.channel or [service.config.scope.default_channel]
                for channel in channels:
                    service.require_high_impedance(channel, allow_50ohm=args.allow_50ohm)
                if len(channels) == 1:
                    result = service.capture_waveform(channel=channels[0], label=args.label)
                    _print_waveform_summary(result.waveform)
                    print(f"package={result.package_dir}")
                    if result.csv_path is not None:
                        print(f"csv={result.csv_path}")
                    if result.npy_path is not None:
                        print(f"npy={result.npy_path}")
                    if result.screenshot_path is not None:
                        print(f"screenshot={result.screenshot_path}")
                    if result.commands_log_path is not None:
                        print(f"commands_log={result.commands_log_path}")
                    return 0
                result = service.capture_waveforms(channels=channels, label=args.label)
                for channel in channels:
                    _print_waveform_summary(result.waveforms[channel])
                    files = result.files.get(str(channel), {})
                    if "csv" in files:
                        print(f"ch{channel}_csv={files['csv']}")
                    if "npy" in files:
                        print(f"ch{channel}_npy={files['npy']}")
                print(f"package={result.package_dir}")
                if result.screenshot_path is not None:
                    print(f"screenshot={result.screenshot_path}")
                if result.commands_log_path is not None:
                    print(f"commands_log={result.commands_log_path}")
                return 0
        parser.error("unknown command")
    except WaveBenchError as exc:
        if getattr(args, "json", False):
            print(json.dumps(_scope_error_payload(exc), indent=2, ensure_ascii=False))
        else:
            print(f"wavebench: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        if getattr(args, "json", False):
            print(json.dumps(error_envelope(KeyboardInterrupt()), indent=2, ensure_ascii=False))
        else:
            print("wavebench: interrupted", file=sys.stderr)
        return 130
    return 1


def main(argv: list[str] | None = None) -> int:
    """Run the CLI, optionally wrapping every non-interactive result as JSON."""

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if "--json" not in raw_argv:
        return _main(raw_argv)

    forwarded = [item for item in raw_argv if item != "--json"]
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = _main(["--json", *forwarded])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        diagnostics = stderr.getvalue()
        if diagnostics:
            sys.stderr.write(diagnostics)
        if code == 0:
            _emit_json_result(stdout.getvalue().strip() or None, exit_code=0)
            return 0
        message = (
            diagnostics.strip().splitlines()[-1]
            if diagnostics.strip()
            else "invalid command line arguments"
        )
        print(json.dumps(error_envelope(ConfigError(message)), indent=2, ensure_ascii=False))
        return code
    diagnostics = stderr.getvalue()
    if diagnostics:
        sys.stderr.write(diagnostics)
    text_output = stdout.getvalue().strip()
    try:
        payload = json.loads(text_output) if text_output else None
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and payload.get("schema") in {
        "wavebench.cli.result.v1",
        "wavebench.error.v1",
    }:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return code
    if isinstance(payload, dict) and payload.get("schema"):
        result_payload: object = payload
    else:
        result_payload = text_output or None
    _emit_json_result(
        result_payload,
        status="ok" if code == 0 else "failed",
        exit_code=code,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
