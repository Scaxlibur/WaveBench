from __future__ import annotations

import argparse
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
from .errors import ConfigError, WaveBenchError
from .cli_parser import build_parser
from .cli_output import (
    _print_arbitrary_probe_results,
    _print_arbitrary_waveform_summary,
    _print_capture_fft_summary,
    _print_capture_package_summary,
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
    _print_scope_history_timestamps,
    _print_scope_digital_status,
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
from .services.frequency_response_calibration import (
    build_frequency_response_calibration,
    load_frequency_response_calibration_config,
    write_frequency_response_calibration_csv,
    write_frequency_response_calibration_json,
)
from .services.sweep_service import SweepService, parse_frequency_list



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



def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
                _print_discovery_results(results)
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
            _print_doctor_records(records)
            return 2 if has_config_doctor_errors(records) else 0
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
            if args.command == "calibrate":
                package = load_run_package(args.path)
                if package.frequency_response_csv_path is None:
                    raise ConfigError("run calibrate requires frequency_response.csv in the run directory")
                calibration = load_frequency_response_calibration_config(args.config)
                if not calibration.enabled:
                    raise ConfigError("calibration.enabled must be true for run calibrate")
                document, rows = build_frequency_response_calibration(
                    package.frequency_response_rows,
                    calibration,
                    source_csv=package.frequency_response_csv_path,
                )
                csv_path = write_frequency_response_calibration_csv(
                    package.path / "frequency_response_calibration.csv", rows
                )
                json_path = write_frequency_response_calibration_json(
                    package.path / "frequency_response_calibration.json", document
                )
                print(f"calibration_csv={csv_path}")
                print(f"calibration_json={json_path}")
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
                _print_run_plan_summary(plan)
                print("safety_limits=ok / 安全上限=通过")
                return 0
            if args.command == "verify":
                plan = load_run_plan(args.plan)
                records = _load_run_service(args).verify(plan)
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
                result = _load_run_service(args).run(plan)
                print(f"run={result.run_dir}")
                print(f"run_json={result.run_json_path}")
                print(f"summary={result.summary_csv_path}")
                print(f"steps={len(result.steps)}")
                return 0
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
                _print_power_status(service.status(channel=args.channel))
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
                _print_source_status(service.status(channel=args.channel))
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
            if args.command == "status":
                channel = args.channel or service.config.scope.default_channel
                _print_scope_snapshot(service.status(channel=channel))
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
        print(f"wavebench: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("wavebench: interrupted", file=sys.stderr)
        return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
