from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np

from .arbitrary import (
    load_arbitrary_waveform,
    validate_waveform_name,
    write_arbitrary_payload_json,
    write_dg4000_dac14_binary_block,
)
from .data.fft import analyze_fft, fft_harmonics
from .errors import ConfigError
from .instruments.api import InstrumentDescriptor
from .instruments.models import (
    DmmCalculationStatistics,
    DmmCalculationStatus,
    DmmMeasurementProfile,
    DmmReading,
    DmmSystemInterfaceStatus,
    DmmTriggerStatus,
    DmmDcvImpedanceConfiguration,
    DmmVoltageRangeConfiguration,
    PowerProtectionStatus,
    PowerStatus,
    ScopeAcquisitionStatus,
    ScopeAverageCaptureResult,
    ScopeHistoryTimestamps,
    ScopeMeasurementStatistics,
    ScopeCursorReadout,
    ScopeDerivedWaveformMetadata,
    ScopeDigitalChannelStatus,
    ScopeDigitalWaveform,
    ScopeFftStatus,
    ScopeSnapshot,
    SourceChannelProfile,
    SourceCounterProfile,
    SourceSweepProfile,
    SourceStatus,
    WaveformData,
)
from .plugins.api import InstrumentPlugin, PluginDoctorRecord
from .plugins.market import MarketPlugin
from .plugins.lifecycle import InstalledPlugin, LifecycleResult
from .plugins.package_inspect import PluginPackage
from .plugins.scpi import DeclarativeScpiPlugin, ScpiProbeResult
from .services.run_plan import RunPlan, RunStep




def _print_plugin_list(plugins: list[InstrumentPlugin]) -> None:
    if not plugins:
        print("no_plugins_found / 未发现插件")
        return
    print("driver_id	kind	origin	models	capabilities")
    for plugin in plugins:
        print(
            f"{plugin.driver_id}	{plugin.kind}	{plugin.origin}	"
            f"{plugin.model_text}	{plugin.capability_text}"
        )


def _print_plugin_info(plugin: InstrumentPlugin) -> None:
    print(f"driver_id={plugin.driver_id}")
    print(f"kind={plugin.kind}")
    print(f"display_name={plugin.display_name}")
    print(f"manufacturer={plugin.manufacturer}")
    print(f"models={plugin.model_text}")
    print(f"origin={plugin.origin}")
    print(f"package={plugin.package}")
    print(f"api_version={plugin.api_version}")
    print(f"summary={plugin.summary}")
    print("capabilities=" + plugin.capability_text)
    if plugin.idn_patterns:
        print("idn_patterns=" + ", ".join(plugin.idn_patterns))
    if plugin.config_fields:
        print("config_fields=" + ", ".join(plugin.config_fields))


def _print_plugin_package(package: PluginPackage) -> None:
    print("package_status=valid / 插件包状态=有效")
    print(f"source_kind={package.source_kind}")
    print(f"distribution={package.distribution}")
    print(f"version={package.version}")
    print("driver_ids=" + ", ".join(package.driver_ids))
    print(f"sha256={package.sha256}")
    print(f"files={package.file_count}")
    print(f"size_bytes={package.size_bytes}")
    print(f"dependencies={len(package.dependencies)}")


def _print_installed_plugins(plugins: tuple[InstalledPlugin, ...]) -> None:
    if not plugins:
        print("no_installed_plugins / 未发现已安装插件")
        return
    print("driver_id\tdistribution\tversion\tstatus\tdetail")
    for plugin in plugins:
        print(
            f"{plugin.driver_id}\t{plugin.distribution}\t{plugin.version}\t"
            f"{plugin.status}\t{plugin.detail}"
        )


def _print_installed_plugin(plugin: InstalledPlugin) -> None:
    print(f"driver_id={plugin.driver_id}")
    print(f"distribution={plugin.distribution}")
    print(f"version={plugin.version}")
    print(f"status={plugin.status}")
    if plugin.wheel_sha256:
        print(f"wheel_sha256={plugin.wheel_sha256}")
    if plugin.detail:
        print(f"detail={plugin.detail}")


def _print_lifecycle_result(result: LifecycleResult) -> None:
    print(f"status={result.status}")
    if result.driver_id:
        print(f"driver_id={result.driver_id}")
    if result.distribution:
        print(f"distribution={result.distribution}")
    if result.version:
        print(f"version={result.version}")


def _print_instrument_descriptor(descriptor: InstrumentDescriptor) -> None:
    _print_plugin_info(descriptor.to_metadata())
    print("aliases=" + ", ".join(descriptor.aliases))
    print("backends=" + ", ".join(descriptor.backends))
    print(
        "resource_schemes="
        + (", ".join(descriptor.resource_schemes) if descriptor.resource_schemes else "any")
    )
    print(f"executable_api={descriptor.api_version}")
    print(
        "wavebench_compat="
        f">={descriptor.wavebench_min_version}, <{descriptor.wavebench_max_version}"
    )
    print(f"distribution={descriptor.distribution}")
    print(f"distribution_version={descriptor.version}")
    print(f"source={descriptor.source}")
    print("permissions=" + ", ".join(descriptor.permissions))


def _print_plugin_doctor(records: list[PluginDoctorRecord]) -> None:
    for record in records:
        print(f"{record.severity}\t{record.subject}\t{record.message}")


def _print_market_search_results(plugins: list[MarketPlugin]) -> None:
    if not plugins:
        print("no_market_plugins_found / 未发现市场插件")
        return
    print("plugin_id\tdriver_id\tkind\tpackage\tversion\tsummary")
    for plugin in plugins:
        print(
            f"{plugin.plugin_id}\t{plugin.driver_id}\t{plugin.kind}\t"
            f"{plugin.package}\t{plugin.version}\t{plugin.summary}"
        )


def _print_market_plugin_info(plugin: MarketPlugin) -> None:
    print(f"plugin_id={plugin.plugin_id}")
    print(f"driver_id={plugin.driver_id}")
    print(f"name={plugin.name}")
    print(f"kind={plugin.kind}")
    print(f"package={plugin.package}")
    print(f"version={plugin.version}")
    print(f"summary={plugin.summary}")
    if plugin.homepage:
        print(f"homepage={plugin.homepage}")
    if plugin.capabilities:
        print("capabilities=" + plugin.capability_text)
    if plugin.tags:
        print("tags=" + plugin.tag_text)


def _print_scpi_plugin_info(scpi_plugin: DeclarativeScpiPlugin) -> None:
    _print_plugin_info(scpi_plugin.plugin)
    print(f"scpi_idn_query={scpi_plugin.idn_query}")


def _print_scpi_doctor(records: list[tuple[str, str, str]]) -> None:
    for severity, subject, message in records:
        print(f"{severity}\t{subject}\t{message}")


def _print_scpi_probe_result(result: ScpiProbeResult) -> None:
    print(f"driver_id={result.driver_id}")
    print(f"resource={result.resource}")
    print(f"backend={result.backend}")
    print(f"query={result.query}")
    print(f"response={result.response}")
    print(f"idn_match={'yes' if result.matched else 'no'}")

def _format_step_summary(step: RunStep) -> str:
    if not step.fields:
        return f"{step.index}: {step.kind}"
    fields = " ".join(f"{key}={value}" for key, value in step.fields.items())
    return f"{step.index}: {step.kind} {fields}"


def _print_run_plan_summary(plan: RunPlan) -> None:
    print(f"plan={plan.path}")
    print(f"experiment={plan.name} label={plan.label}")
    if plan.safety.require_scope_coupling_not:
        blocked = ",".join(plan.safety.require_scope_coupling_not)
        print(f"safety: scope CH{plan.safety.scope_guard_channel} coupling not in [{blocked}]")
    else:
        print("safety: none")
    if plan.restore.source_state:
        if plan.restore.source_channels:
            channels = ",".join(str(channel) for channel in plan.restore.source_channels)
        else:
            channels = "default"
        print(f"restore: basic source state channels={channels}")
    else:
        print("restore: none")
    print(f"steps={len(plan.steps)}")
    for step in plan.steps:
        print(_format_step_summary(step))



def _print_run_preflight(records: list[Any]) -> None:
    print("verify=ok / 预检=通过")
    for record in records:
        print(f"instrument/仪器={record.instrument} resource/资源={record.resource} idn={record.idn}")

def _print_dmm_reading(reading: DmmReading) -> None:
    print(f"{reading.function}: {reading.value:.12g} {reading.unit} raw={reading.raw}")


def _print_dmm_measurement_profile(profile: DmmMeasurementProfile) -> None:
    print(f"function={profile.function}")
    print(f"range_code={profile.range_code if profile.range_code is not None else 'n/a'}")
    if profile.auto_range is None:
        print("auto_range=n/a")
    else:
        print(f"auto_range={'true' if profile.auto_range else 'false'}")
    print(f"impedance={profile.impedance if profile.impedance is not None else 'n/a'}")


def _print_dmm_trigger_status(status: DmmTriggerStatus) -> None:
    print(f"source={status.source}")
    print(f"auto_interval_s={status.auto_interval_s:.12g}")
    print(f"auto_hold={'true' if status.auto_hold else 'false'}")
    print(f"auto_hold_sensitivity={status.auto_hold_sensitivity}")
    print(f"single_count={status.single_count}")
    print(f"external_slope={status.external_slope}")
    print(f"vmc_polarity={status.vmc_polarity}")
    print(f"vmc_pulse_width_s={status.vmc_pulse_width_s:.12g}")


def _print_dmm_calculation_status(status: DmmCalculationStatus) -> None:
    print(f"function={status.function}")
    print(f"statistic_count={status.statistic_count}")
    print(f"db_reference={status.db_reference:.12g}")
    print(f"dbm_reference_ohm={status.dbm_reference_ohm:.12g}")


def _print_dmm_calculation_statistics(result: DmmCalculationStatistics) -> None:
    print(f"function={result.function}")
    print(f"value={result.value:.12g}")
    print(f"count={result.count}")


def _print_dmm_system_interface_status(status: DmmSystemInterfaceStatus) -> None:
    print(f"beeper_enabled={'true' if status.beeper_enabled else 'false'}")
    print(f"language={status.language.lower()}")
    print(f"decimal_format={status.decimal_format.lower()}")
    print(f"separator_format={status.separator_format.lower()}")
    print(f"display_brightness={status.display_brightness}")
    print(f"scan_board_installed={'true' if status.scan_board_installed else 'false'}")
    print(f"lan_interface_installed={'true' if status.lan_interface_installed else 'false'}")
    print(f"dhcp_enabled={'true' if status.dhcp_enabled else 'false'}")
    print(f"gpib_address={status.gpib_address}")
    print(f"rs232_baud={status.rs232_baud}")
    print(f"rs232_parity={status.rs232_parity.lower()}")


def _print_dmm_voltage_range_configuration(
    result: DmmVoltageRangeConfiguration,
) -> None:
    print(f"function={result.function}")
    print(f"previous_range_code={result.previous_range_code}")
    print(f"range_code={result.range_code}")
    print(f"changed={'true' if result.changed else 'false'}")


def _print_dmm_dcv_impedance_configuration(
    result: DmmDcvImpedanceConfiguration,
) -> None:
    print(f"previous_impedance={result.previous_impedance}")
    print(f"impedance={result.impedance}")
    print(f"range_code={result.range_code}")
    print(f"changed={'true' if result.changed else 'false'}")


def _print_scope_snapshot(snapshot: ScopeSnapshot) -> None:
    def scalar(value: object) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            return f"{value:.12g}"
        return str(value)

    sections = (
        ("identity", snapshot.identity),
        ("health", snapshot.health),
        ("channel", snapshot.channel),
        ("timebase", snapshot.timebase),
        ("probe", snapshot.probe),
        ("waveform", snapshot.waveform),
        ("trigger", snapshot.trigger),
    )
    for section_name, section in sections:
        for name, value in vars(section).items():
            if isinstance(value, tuple):
                print(f"{section_name}.{name}=" + ",".join(str(item) for item in value))
            else:
                print(f"{section_name}.{name}={scalar(value)}")


def _print_scope_acquisition_status(status: ScopeAcquisitionStatus) -> None:
    def scalar(value: object) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    print(f"average.count={status.average_count}")
    print(f"average.complete={scalar(status.average_complete)}")
    print(f"segmented.option_installed={scalar(status.segmented_option_installed)}")
    print(f"segmented.enabled={scalar(status.segmented_enabled)}")
    print(f"segmented.maximum_enabled={scalar(status.segmented_maximum_enabled)}")
    print(f"segmented.capacity={scalar(status.segment_capacity)}")
    print(f"segmented.available={scalar(status.segments_available)}")


def _print_scope_average_capture(result: ScopeAverageCaptureResult) -> None:
    print("average.channels=" + ",".join(str(channel) for channel in result.request.channels))
    print(f"average.count={result.request.average_count}")
    print(f"average.complete={'true' if result.average_complete else 'false'}")
    print("average.restored=true")
    print("average.restored_fields=" + ",".join(result.restored_fields))
    for waveform in result.waveforms:
        print(f"average.channel.{waveform.channel}.samples={waveform.sample_count}")


def _print_scope_history_timestamps(table: ScopeHistoryTimestamps) -> None:
    print(f"history.channel={table.channel}")
    print(f"history.count={len(table.entries)}")
    for entry in table.entries:
        prefix = f"history.{entry.position}"
        print(f"{prefix}.relative_s={entry.relative_s:.12g}")
        print(f"{prefix}.date={entry.year:04d}-{entry.month:02d}-{entry.day:02d}")
        print(f"{prefix}.time={entry.hour:02d}:{entry.minute:02d}:{entry.second:.12g}")


def _print_scope_digital_status(status: ScopeDigitalChannelStatus) -> None:
    print(f"digital.channel={status.channel}")
    print(f"digital.group={status.group_start_channel}-{status.group_stop_channel}")
    print(f"digital.displayed={'true' if status.displayed else 'false'}")
    print(f"digital.activity={status.activity}")
    print(f"digital.technology={status.technology}")
    print(f"digital.threshold_v={status.threshold_v:.12g}")
    print(f"digital.threshold_coupled={'true' if status.threshold_coupled else 'false'}")
    print(f"digital.hysteresis={status.hysteresis}")
    print(f"digital.deskew_s={status.deskew_s:.12g}")
    print(f"digital.size={status.size}")
    print(f"digital.position_div={status.position_div:.12g}")
    print(f"digital.label={status.label}")
    print(f"digital.label_enabled={'true' if status.label_enabled else 'false'}")


def _print_scope_digital_waveform(
    waveform: ScopeDigitalWaveform,
    *,
    output_path: Path | None = None,
) -> None:
    print("digital_waveform.channels=" + ",".join(str(item) for item in waveform.channels))
    print(f"digital_waveform.samples={waveform.sample_count}")
    print(f"digital_waveform.dtype={waveform.samples.dtype}")
    print(f"digital_waveform.x_start_s={waveform.x_start_s:.12g}")
    print(f"digital_waveform.x_stop_s={waveform.x_stop_s:.12g}")
    print(f"digital_waveform.x_increment_s={waveform.x_increment_s:.12g}")
    if output_path is not None:
        print(f"digital_waveform.output={output_path}")


def _print_scope_measurement_statistics(stats: ScopeMeasurementStatistics) -> None:
    def number(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.12g}"

    print(f"measurement.slot={stats.slot}")
    print(f"measurement.category={stats.category}")
    print(f"measurement.actual={number(stats.actual)}")
    print(f"measurement.average={number(stats.average)}")
    print(f"measurement.standard_deviation={number(stats.standard_deviation)}")
    print(f"measurement.minimum={number(stats.minimum)}")
    print(f"measurement.maximum={number(stats.maximum)}")
    print(f"measurement.waveform_count={stats.waveform_count}")
    if stats.buffered_values is None:
        print("measurement.buffer=n/a")
    else:
        print("measurement.buffer=" + ",".join(number(value) for value in stats.buffered_values))


def _print_scope_derived_waveform_metadata(
    metadata: ScopeDerivedWaveformMetadata,
) -> None:
    prefix = metadata.source_kind
    print(f"{prefix}.index={metadata.index}")
    print(f"{prefix}.source_catalog={metadata.source_catalog or 'n/a'}")
    print(f"{prefix}.x_start={metadata.x_start:.12g}")
    print(f"{prefix}.x_stop={metadata.x_stop:.12g}")
    print(f"{prefix}.points={metadata.points}")
    print(
        f"{prefix}.values_per_sample="
        + ("n/a" if metadata.values_per_sample is None else str(metadata.values_per_sample))
    )
    print(f"{prefix}.x_increment={metadata.x_increment:.12g}")
    print(f"{prefix}.x_origin={metadata.x_origin:.12g}")
    print(f"{prefix}.y_increment={metadata.y_increment:.12g}")
    print(f"{prefix}.y_origin={metadata.y_origin:.12g}")
    print(f"{prefix}.y_resolution_bits={metadata.y_resolution_bits}")


def _print_scope_fft_status(status: ScopeFftStatus) -> None:
    print(f"fft.math_index={status.math_index}")
    print(f"fft.average_complete={'true' if status.average_complete else 'false'}")
    print(f"fft.resolution_bandwidth_hz={status.resolution_bandwidth_hz:.12g}")
    print(f"fft.sample_rate_hz={status.sample_rate_hz:.12g}")


def _print_scope_cursor_readout(readout: ScopeCursorReadout) -> None:
    def number(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.12g}"

    print(f"cursor.index={readout.cursor_index}")
    print(f"cursor.source={readout.source}")
    print(f"cursor.function={readout.function}")
    print(f"cursor.result={number(readout.result)}")
    print(f"cursor.x_delta_s={number(readout.x_delta_s)}")
    print(f"cursor.inverse_x_delta_hz={number(readout.inverse_x_delta_hz)}")
    print(f"cursor.y_delta={number(readout.y_delta)}")
    print(f"cursor.inverse_y_delta={number(readout.inverse_y_delta)}")
    print(f"cursor.x_ratio={number(readout.x_ratio)}")
    print(f"cursor.y_ratio={number(readout.y_ratio)}")


def _print_scope_mutation_manifest(manifest: dict[str, Any]) -> None:
    print(f"operation={manifest['operation']}")
    print(f"mutates_instrument={str(bool(manifest['mutates_instrument'])).lower()}")
    print(f"raw_scpi={str(bool(manifest['raw_scpi'])).lower()}")
    print(f"channel={manifest['channel']}")
    for key in ("display", "time_range_s", "vertical_scale_v_per_div", "hide_other_channels"):
        if key in manifest and manifest[key] is not None:
            value = manifest[key]
            if isinstance(value, bool):
                value = str(value).lower()
            print(f"{key}={value}")
    print("affected_settings=" + ",".join(str(item) for item in manifest["affected_settings"]))

def _print_dmm_function_status(function: str) -> None:
    print(f"功能 / Function: {function}")


def _print_dmm_function_set(function: str) -> None:
    print(f"功能已切换 / Function set: {function}")


def _print_power_status(status: PowerStatus) -> None:
    set_value = f"{status.set_voltage_v}V/{status.set_current_a}A"
    measured = f"{status.measured_voltage_v}V/{status.measured_current_a}A/{status.measured_power_w}W"
    print(f"CH{status.channel}: output={status.output} mode={status.mode} set={set_value} measured={measured} rating={status.rating}")


def _print_power_protection_status(status: PowerProtectionStatus) -> None:
    ovp = f"enabled={status.ovp_enabled} threshold={status.ovp_threshold_v}V tripped={status.ovp_tripped}"
    ocp = f"enabled={status.ocp_enabled} threshold={status.ocp_threshold_a}A tripped={status.ocp_tripped}"
    print(f"CH{status.channel} protection / 保护: OVP {ovp}; OCP {ocp}")


def _print_source_status(status: SourceStatus) -> None:
    duty = "" if status.square_duty_cycle_percent is None else f" duty={status.square_duty_cycle_percent}%"
    print(f"CH{status.channel}: output={status.output} func={status.function} freq={status.frequency_hz}Hz amp={status.amplitude}{status.amplitude_unit or ''}{duty} offset={status.offset_v}V phase={status.phase_deg}deg")
    print(f"mode={status.frequency_mode} sweep={status.sweep_enabled}")
    if status.apply_raw is not None:
        print(f"apply={status.apply_raw}")


def _print_source_channel_profile(profile: SourceChannelProfile) -> None:
    _print_source_status(profile.status)
    load = "high_impedance" if profile.load_ohm is None else f"{profile.load_ohm:.12g}"
    print(f"load_ohm={load}")
    print(f"polarity={profile.polarity}")
    print(f"noise_enabled={str(profile.noise_enabled).lower()}")
    print(f"noise_scale_percent={profile.noise_scale_percent:.12g}")
    print(f"sync_enabled={str(profile.sync_enabled).lower()}")
    print(f"sync_polarity={profile.sync_polarity}")
    print(f"burst_enabled={str(profile.burst_enabled).lower()}")
    print(f"modulation_enabled={str(profile.modulation_enabled).lower()}")
    print(f"modulation_type={profile.modulation_type}")
    print(f"marker_enabled={str(profile.marker_enabled).lower()}")
    print(f"pulse_hold={profile.pulse_hold}")


def _print_source_sweep_profile(profile: SourceSweepProfile) -> None:
    print(f"CH{profile.channel}: sweep_enabled={str(profile.enabled).lower()}")
    print(
        f"start_hz={profile.start_hz:.12g} stop_hz={profile.stop_hz:.12g} "
        f"center_hz={profile.center_hz:.12g} span_hz={profile.span_hz:.12g}"
    )
    print(f"spacing={profile.spacing} steps={profile.steps}")
    print(
        f"sweep_time_s={profile.sweep_time_s:.12g} "
        f"start_hold_s={profile.start_hold_s:.12g} "
        f"stop_hold_s={profile.stop_hold_s:.12g} "
        f"return_time_s={profile.return_time_s:.12g}"
    )
    print(
        f"trigger_source={profile.trigger_source} "
        f"trigger_slope={profile.trigger_slope} trigger_out={profile.trigger_out}"
    )
    print(
        f"marker_enabled={str(profile.marker_enabled).lower()} "
        f"marker_frequency_hz={profile.marker_frequency_hz:.12g}"
    )


def _print_source_counter_profile(profile: SourceCounterProfile) -> None:
    print(f"counter_enabled={str(profile.enabled).lower()}")
    if profile.measurement is None:
        print("measurement=none")
    else:
        measurement = profile.measurement
        print(f"frequency_hz={measurement.frequency_hz:.12g}")
        print(f"period_s={measurement.period_s:.12g}")
        print(f"duty_cycle_percent={measurement.duty_cycle_percent:.12g}")
        print(f"positive_width_s={measurement.positive_width_s:.12g}")
        print(f"negative_width_s={measurement.negative_width_s:.12g}")
    print(f"coupling={profile.coupling}")
    print(f"impedance_ohm={profile.impedance_ohm:.12g}")
    print(f"attenuation={profile.attenuation}")
    print(f"gate_time={profile.gate_time}")
    print(
        "high_frequency_rejection_enabled="
        f"{str(profile.high_frequency_rejection_enabled).lower()}"
    )
    print(f"trigger_level_v={profile.trigger_level_v:.12g}")
    print(f"sensitivity_percent={profile.sensitivity_percent:.12g}")
    print(f"statistics_enabled={str(profile.statistics_enabled).lower()}")
    print(f"statistics_display={profile.statistics_display}")


def _print_capture_package_summary(package) -> None:
    print(f"package={package.path}")
    print(f"metadata={package.metadata_path}")
    command = package.operation.get("command", "")
    if command:
        print(f"operation={command}")
    resource = package.instrument.get("resource", "")
    if resource:
        print(f"resource={resource}")
    print(f"channels={','.join(str(channel.channel) for channel in package.channels)}")
    for channel in package.channels:
        summary = channel.summary
        print(f"CH{channel.channel}")
        if "samples" in summary:
            print(f"  samples={summary['samples']}")
        if "x_increment_s" in summary:
            print(f"  dt={summary['x_increment_s']:.6e} s")
        if "voltage_vpp_v" in summary:
            print(f"  vpp={summary['voltage_vpp_v']:.6g} V")
        if "voltage_rms_v" in summary:
            print(f"  rms={summary['voltage_rms_v']:.6g} V")
        if "voltage_mean_v" in summary:
            print(f"  mean={summary['voltage_mean_v']:.6g} V")
        if summary.get("frequency_estimate_hz") is not None:
            print(f"  frequency≈{summary['frequency_estimate_hz']:.6g} Hz")
        if summary.get("duty_cycle") is not None:
            print(f"  duty={summary['duty_cycle']:.6g}")
        if summary.get("rise_time_s") is not None:
            print(f"  rise_time={summary['rise_time_s']:.6e} s")
        if summary.get("fall_time_s") is not None:
            print(f"  fall_time={summary['fall_time_s']:.6e} s")
        warnings = summary.get("quality_warnings", [])
        for warning in warnings:
            print(f"  warning={warning}")
        for kind, path in sorted(channel.files.items()):
            print(f"  {kind}={path}")


def _print_capture_fft_summary(
    package,
    *,
    max_harmonic_order: int = 5,
    expected_frequency_hz: float | None = None,
    frequency_tolerance_ratio: float = 0.05,
) -> None:
    if max_harmonic_order < 1:
        raise ConfigError("--harmonics must be >= 1")
    if expected_frequency_hz is not None and expected_frequency_hz <= 0:
        raise ConfigError("--fft-expect-frequency must be > 0")
    if frequency_tolerance_ratio < 0:
        raise ConfigError("--fft-frequency-tolerance must be >= 0")
    print("FFT")
    for channel in package.channels:
        npy_text = channel.files.get("npy")
        print(f"CH{channel.channel}")
        if not npy_text:
            print("  warning=missing npy artifact")
            continue
        npy_path = _resolve_capture_file_path(package.path, npy_text)
        try:
            analysis = _analyze_fft(np.load(npy_path), max_harmonic_order=max_harmonic_order)
        except Exception as exc:  # report-style inspect should keep other channels readable
            print(f"  warning=fft unavailable: {type(exc).__name__}: {exc}")
            continue
        print(f"  window={analysis['window']}")
        print(f"  samples={analysis['samples']}")
        print(f"  sample_rate≈{analysis['sample_rate_hz']:.6g} Hz")
        print(f"  resolution≈{analysis['resolution_hz']:.6g} Hz")
        print(f"  peak_frequency≈{analysis['peak_frequency_hz']:.6g} Hz")
        if expected_frequency_hz is not None:
            error_ratio = abs(analysis["peak_frequency_hz"] - expected_frequency_hz) / expected_frequency_hz
            print(f"  peak_frequency_error≈{error_ratio:.3%}")
            print(f"  peak_frequency_ok={error_ratio <= frequency_tolerance_ratio}")
        print(f"  peak_amplitude≈{analysis['peak_amplitude_v']:.6g} V")
        print(f"  noise_floor≈{analysis['noise_floor_v']:.6g} V")
        thd = analysis.get("thd_ratio")
        if thd is not None:
            print(f"  thd≈{thd:.3%}")
        for harmonic in analysis["harmonics"]:
            print(
                f"  harmonic_{harmonic['order']:g}≈{harmonic['frequency_hz']:.6g} Hz "
                f"amplitude≈{harmonic['amplitude_v']:.6g} V"
            )
        for warning in analysis["warnings"]:
            print(f"  warning={warning}")



def _analyze_fft(waveform: Any, *, max_harmonic_order: int = 5) -> dict[str, Any]:
    return analyze_fft(waveform, max_harmonic_order=max_harmonic_order)


def _fft_harmonics(
    frequencies: Any, amplitudes: Any, fundamental_hz: float, *, max_order: int = 5
) -> list[dict[str, float]]:
    return fft_harmonics(frequencies, amplitudes, fundamental_hz, max_order=max_order)


def _resolve_capture_file_path(package_dir: Path, file_text: str) -> Path:
    path = Path(file_text.replace("\\", "/"))
    if path.is_absolute() or path.exists():
        return path
    root = _project_root_from_capture_path(package_dir)
    candidate = root / path
    if candidate.exists():
        return candidate
    return package_dir / path.name


def _project_root_from_capture_path(package_dir: Path) -> Path:
    parts = package_dir.parts
    if len(parts) >= 3 and parts[-3:-1] == ("data", "raw"):
        return Path(*parts[:-3]) if len(parts[:-3]) > 0 else Path(".")
    return package_dir.parent


def _print_discovery_results(results: list[Any]) -> None:
    if not results:
        print("no_instruments_found / 未发现仪器")
        return
    print("address	port	status	protocol	source	resource	idn	note")
    for item in results:
        port = "" if item.port is None else str(item.port)
        idn = "" if item.idn is None else item.idn
        print(
            f"{item.address}	{port}	{item.status}	{item.protocol}	"
            f"{item.source}	{item.resource}	{idn}	{item.note}"
        )


def _print_doctor_records(records: list[Any]) -> None:
    print("severity\ttarget\tdriver\tresource\tidn\tmessage\tsuggestion")
    for item in records:
        idn = "" if item.idn is None else item.idn
        print(
            f"{item.severity}\t{item.target}\t{item.driver}\t{item.resource}\t"
            f"{idn}\t{item.message}\t{item.suggestion}"
        )


def _print_arbitrary_probe_results(results: list[Any]) -> None:
    for item in results:
        response = "" if item.response is None else item.response
        exception = "" if item.exception is None else f" exception={item.exception}"
        active_errors = [err for err in item.errors if not (err.startswith("0") or "No error" in err)]
        error_text = " | ".join(active_errors) if active_errors else "0"
        print(
            f"{item.label}: accepted={item.accepted} command={item.command} "
            f"response={response} errors={error_text}{exception}"
        )


def _print_arbitrary_waveform_summary(args: argparse.Namespace, *, dry_run: bool = True) -> None:
    name = validate_waveform_name(args.name)
    waveform = load_arbitrary_waveform(
        args.file,
        sample_rate_hz=args.sample_rate,
        max_points=args.max_points,
    )
    summary = waveform.summary()
    print(f"arb_name={name}")
    print(f"channel={args.channel}")
    print(f"file={summary['source_path']}")
    print(f"points={summary['points']}")
    print(f"input={summary['input_min']:.6g}..{summary['input_max']:.6g} mean={summary['input_mean']:.6g}")
    print(f"normalized={summary['normalized_min']:.6g}..{summary['normalized_max']:.6g}")
    print(f"dac14={summary['dac14_min']}..{summary['dac14_max']}")
    if summary["sample_rate_hz"] is not None:
        print(f"sample_rate={summary['sample_rate_hz']:.6g} Hz")
    print(f"amplitude={args.amplitude:.6g} Vpp")
    if args.frequency is not None:
        print(f"frequency={args.frequency:.6g} Hz")
    print(f"offset={args.offset:.6g} V")
    print(f"output_on={bool(args.output_on)}")
    if args.export_payload:
        output = write_arbitrary_payload_json(
            waveform,
            args.export_payload,
            name=name,
            channel=args.channel,
            amplitude_vpp=args.amplitude,
            offset_v=args.offset,
        )
        print(f"payload={output}")
    if args.export_dg4000_dac_block:
        output = write_dg4000_dac14_binary_block(
            waveform,
            args.export_dg4000_dac_block,
            byte_order=args.dg4000_byte_order,
        )
        print(f"dg4000_dac_block={output}")
        print(f"dg4000_byte_order={args.dg4000_byte_order}")
        print("dg4000_byte_order_status=dg4202_hardware_validated_2026-05-01")
    print(f"dry_run={str(dry_run).lower()}")
    if dry_run:
        print("upload=not_requested")


def _print_waveform_summary(waveform: WaveformData) -> None:
    summary = waveform.summary()
    print(f"CH{summary['channel']} waveform fetched")
    print(f"samples={summary['samples']}")
    print(f"time={summary['x_start_s']:.6e}..{summary['x_stop_s']:.6e} s")
    print(f"dt={summary['x_increment_s']:.6e} s")
    print(f"voltage={summary['voltage_min_v']:.6g}..{summary['voltage_max_v']:.6g} V")
    print(f"vpp={summary['voltage_vpp_v']:.6g} V")
    print(f"rms={summary['voltage_rms_v']:.6g} V")
    print(f"mean={summary['voltage_mean_v']:.6g} V")
    frequency = summary.get("frequency_estimate_hz")
    if frequency is not None:
        print(f"frequency≈{frequency:.6g} Hz")
    estimated_cycles = summary.get("estimated_cycles")
    if estimated_cycles is not None:
        print(f"estimated_cycles≈{estimated_cycles:.3g}")
    frequency_error = summary.get("frequency_error_ratio")
    if frequency_error is not None:
        print(f"frequency_error≈{frequency_error:.3%}")
    for warning in summary.get("quality_warnings", []):
        print(f"warning={warning}")
