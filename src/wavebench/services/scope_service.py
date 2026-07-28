from __future__ import annotations

import csv
import json
import os
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np

from wavebench.config import WaveBenchConfig
from wavebench.data.package import new_package_dir
from wavebench.errors import ConfigError, WaveBenchError
from wavebench.instruments.api import InstrumentDescriptor, ScopeCouplingPolicy
from wavebench.instruments.capabilities import require_capabilities
from wavebench.instruments.contracts import (
    MultiChannelScopeDriver,
    ScopeAcquisitionStatusDriver,
    ScopeAverageCaptureDriver,
    ScopeAnalysisReadDriver,
    ScopeDriver,
    ScopeDigitalStatusDriver,
    ScopeDigitalWaveformDriver,
    ScopeHistoryTimestampsDriver,
    ScopeMeasurementStatisticsDriver,
    ScopeSnapshotDriver,
)
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import (
    ScopeAcquisitionStatus,
    ScopeAverageCaptureRequest,
    ScopeAverageCaptureResult,
    ScopeCursorReadout,
    ScopeDerivedWaveformMetadata,
    ScopeDigitalChannelStatus,
    ScopeDigitalWaveform,
    ScopeDigitalWaveformRequest,
    ScopeFftStatus,
    ScopeHistoryTimestamps,
    ScopeMeasurementStatistics,
    ScopeSnapshot,
    WaveformData,
)
from wavebench.instruments.registry import resolve_instrument_descriptor
from wavebench.logging import CommandLogger

HIGH_IMPEDANCE_COUPLINGS = {"DCL", "DCLIMIT", "ACL", "ACLIMIT"}
LOW_IMPEDANCE_COUPLINGS = {"DC", "AC"}
def normalize_coupling(value: str) -> str:
    return value.strip().upper()


def is_high_impedance_coupling(value: str) -> bool:
    return normalize_coupling(value) in HIGH_IMPEDANCE_COUPLINGS


def assert_scope_high_impedance(
    coupling: str,
    *,
    channel: int,
    allow_50ohm: bool = False,
    driver: str = "rtm2032",
    coupling_policy: ScopeCouplingPolicy | None = None,
) -> str:
    normalized = normalize_coupling(coupling)
    policy = coupling_policy
    if policy is None:
        policy = resolve_instrument_descriptor(driver, expected_kind="scope").scope_coupling_policy
    if policy == "fixed-high-impedance":
        if normalized in {"AC", "DC", "GND"}:
            return normalized
        raise ConfigError(
            f"scope CH{channel} coupling {normalized!r} is not recognized for RIGOL DS1000Z; "
            "expected AC, DC, or GND. / "
            f"示波器 CH{channel} 耦合值 {normalized!r} 不是已知的 RIGOL DS1000Z 耦合方式。"
        )
    if policy == "switchable-termination" and normalized in HIGH_IMPEDANCE_COUPLINGS:
        return normalized
    if policy == "switchable-termination" and allow_50ohm and normalized in LOW_IMPEDANCE_COUPLINGS:
        return normalized
    if policy == "switchable-termination" and normalized in LOW_IMPEDANCE_COUPLINGS:
        raise ConfigError(
            f"scope CH{channel} coupling is {normalized}, which may use 50 ohm termination; "
            "default capture requires high impedance. Pass --allow-50ohm or set "
            "safety.allow_50ohm = true only when the test setup explicitly accepts this. "
            f"/ 示波器 CH{channel} 当前耦合为 {normalized}，可能是 50Ω 输入；默认要求高阻测量。"
            "只有明确允许 50Ω 时才使用 --allow-50ohm 或 safety.allow_50ohm = true。"
        )
    raise ConfigError(
        f"scope CH{channel} coupling {normalized!r} is not recognized; refusing capture by default. "
        "Known high-impedance values: ACL, ACLimit, DCL, DCLimit. "
        f"/ 示波器 CH{channel} 耦合值 {normalized!r} 无法确认是否高阻，默认拒绝采集。"
    )


@dataclass(frozen=True)
class CaptureResult:
    package_dir: Path
    waveform: WaveformData
    metadata_path: Path
    csv_path: Path | None
    npy_path: Path | None
    screenshot_path: Path | None
    commands_log_path: Path | None

@dataclass(frozen=True)
class MultiCaptureResult:
    package_dir: Path
    waveforms: dict[int, WaveformData]
    metadata_path: Path
    files: dict[str, dict[str, str]]
    screenshot_path: Path | None
    commands_log_path: Path | None

@dataclass
class ScopeService:
    config: WaveBenchConfig
    logger: CommandLogger
    session: ScopeDriver | None = None
    descriptor: InstrumentDescriptor | None = None

    def _require(self, operation: str, *capabilities: str) -> None:
        descriptor = self.descriptor or resolve_instrument_descriptor(
            self.config.scope.driver,
            expected_kind="scope",
        )
        require_capabilities(descriptor, capabilities, operation=operation)

    def _open_scope(self) -> ScopeDriver:
        opened = open_instrument_driver(
            driver_reference=self.config.scope.driver,
            expected_kind="scope",
            resource=self.config.connection.resource,
            configured_backend=self.config.connection.backend,
            timeout_ms=self.config.connection.timeout_ms,
            opc_timeout_ms=self.config.connection.opc_timeout_ms,
            read_retry_attempts=self.config.connection.read_retry_attempts,
            read_retry_delay_ms=self.config.connection.read_retry_delay_ms,
            logger=self.logger,
            settings={"check_errors": self.config.scope.check_errors},
            options=getattr(self.config.scope, "options", {}),
        )
        self.descriptor = opened.descriptor
        return opened.driver

    def open_session(self) -> ScopeDriver:
        return self._open_scope()

    @contextmanager
    def _scope_session(self) -> Iterator[ScopeDriver]:
        if self.session is not None:
            yield self.session
            return
        scope = self._open_scope()
        try:
            yield scope
        finally:
            scope.close()

    def idn(self) -> str:
        self._require("scope.idn", "scope.idn")
        with self._scope_session() as scope:
            return scope.idn()

    def errors(self) -> list[str]:
        self._require("scope.errors", "scope.errors")
        with self._scope_session() as scope:
            return scope.errors()

    def status(self, channel: int) -> ScopeSnapshot:
        self._require("scope.status", "scope.snapshot")
        with self._scope_session() as scope:
            return cast(ScopeSnapshotDriver, scope).get_snapshot(channel)

    def acquisition_status(self) -> ScopeAcquisitionStatus:
        self._require("scope.acquisition_status", "scope.acquisition_status")
        with self._scope_session() as scope:
            return cast(ScopeAcquisitionStatusDriver, scope).get_acquisition_status()

    def capture_average(
        self,
        *,
        channels: tuple[int, ...],
        average_count: int,
        acquisition_stopped: bool,
        allow_50ohm: bool = False,
    ) -> ScopeAverageCaptureResult:
        request = ScopeAverageCaptureRequest(
            channels=channels,
            average_count=average_count,
            acquisition_stopped=acquisition_stopped,
        )
        self._require(
            "scope.capture_average",
            "scope.capture_average",
            "scope.channel_coupling",
        )
        with self._scope_session() as scope:
            descriptor = self.descriptor or resolve_instrument_descriptor(
                self.config.scope.driver,
                expected_kind="scope",
            )
            for channel in request.channels:
                assert_scope_high_impedance(
                    scope.channel_coupling(channel),
                    channel=channel,
                    allow_50ohm=allow_50ohm,
                    driver=descriptor.driver_id,
                    coupling_policy=descriptor.scope_coupling_policy,
                )
            return cast(ScopeAverageCaptureDriver, scope).capture_average(request)

    def history_timestamps(self, channel: int) -> ScopeHistoryTimestamps:
        self._require("scope.history_timestamps", "scope.history_timestamps")
        with self._scope_session() as scope:
            return cast(ScopeHistoryTimestampsDriver, scope).get_history_timestamps(channel)

    def digital_status(self, channel: int) -> ScopeDigitalChannelStatus:
        self._require("scope.digital_status", "scope.digital_status")
        with self._scope_session() as scope:
            return cast(ScopeDigitalStatusDriver, scope).get_digital_status(channel)

    def digital_waveform(
        self,
        *,
        channels: tuple[int, ...],
        acquisition_stopped: bool,
    ) -> ScopeDigitalWaveform:
        try:
            request = ScopeDigitalWaveformRequest(
                channels=channels,
                acquisition_stopped=acquisition_stopped,
            )
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        self._require("scope.digital_waveform", "scope.digital_waveform")
        with self._scope_session() as scope:
            return cast(ScopeDigitalWaveformDriver, scope).get_digital_waveform(
                request
            )

    def measurement_statistics(
        self,
        slot: int,
        *,
        configured_slot: bool,
        include_buffer: bool = False,
        acquisition_stopped: bool = False,
    ) -> ScopeMeasurementStatistics:
        self._require("scope.measurement_statistics", "scope.measurement_statistics")
        with self._scope_session() as scope:
            return cast(ScopeMeasurementStatisticsDriver, scope).get_measurement_statistics(
                slot,
                configured_slot=configured_slot,
                include_buffer=include_buffer,
                acquisition_stopped=acquisition_stopped,
            )

    def math_waveform_metadata(self, math_index: int) -> ScopeDerivedWaveformMetadata:
        self._require("scope.math_metadata", "scope.math_metadata")
        with self._scope_session() as scope:
            return cast(ScopeAnalysisReadDriver, scope).get_math_waveform_metadata(
                math_index
            )

    def fft_status(self, math_index: int, *, configured_fft: bool) -> ScopeFftStatus:
        self._require("scope.fft_status", "scope.fft_status")
        with self._scope_session() as scope:
            return cast(ScopeAnalysisReadDriver, scope).get_fft_status(
                math_index,
                configured_fft=configured_fft,
            )

    def reference_waveform_metadata(
        self,
        reference_index: int,
    ) -> ScopeDerivedWaveformMetadata:
        self._require("scope.reference_metadata", "scope.reference_metadata")
        with self._scope_session() as scope:
            return cast(ScopeAnalysisReadDriver, scope).get_reference_waveform_metadata(
                reference_index
            )

    def cursor_readout(
        self,
        cursor_index: int,
        *,
        configured_cursor: bool,
    ) -> ScopeCursorReadout:
        self._require("scope.cursor_readout", "scope.cursor_readout")
        with self._scope_session() as scope:
            return cast(ScopeAnalysisReadDriver, scope).get_cursor_readout(
                cursor_index,
                configured_cursor=configured_cursor,
            )

    def channel_coupling(self, channel: int) -> str:
        self._require("scope.channel_coupling", "scope.channel_coupling")
        with self._scope_session() as scope:
            return scope.channel_coupling(channel)

    def require_high_impedance(self, channel: int, *, allow_50ohm: bool = False) -> str:
        coupling = self.channel_coupling(channel)
        descriptor = self.descriptor or resolve_instrument_descriptor(
            self.config.scope.driver,
            expected_kind="scope",
        )
        return assert_scope_high_impedance(
            coupling,
            channel=channel,
            allow_50ohm=allow_50ohm,
            driver=self.config.scope.driver,
            coupling_policy=descriptor.scope_coupling_policy,
        )

    def autoscale(self) -> None:
        required = ["scope.autoscale"]
        if self.config.autoscale.check_errors:
            required.append("scope.errors")
        self._require("scope.autoscale", *required)
        with self._scope_session() as scope:
            scope.autoscale(
                wait_opc=self.config.autoscale.wait_opc,
                check_errors=self.config.autoscale.check_errors,
            )

    def set_channel_display(self, channel: int, enabled: bool) -> dict[str, Any]:
        required = ["scope.channel_display"]
        if self.config.scope.check_errors:
            required.append("scope.errors")
        self._require("scope.channel_display", *required)
        with self._scope_session() as scope:
            scope.set_channel_display(
                channel,
                enabled,
                check_errors=self.config.scope.check_errors,
            )
        return {
            "operation": "scope.channel_display",
            "mutates_instrument": True,
            "raw_scpi": False,
            "channel": channel,
            "display": "on" if enabled else "off",
            "affected_settings": [f"CH{channel}.display"],
        }

    def focus_channel(
        self,
        *,
        channel: int,
        time_range_s: float | None = None,
        vertical_scale_v_per_div: float | None = None,
        hide_other_channels: bool = False,
    ) -> dict[str, Any]:
        required = ["scope.focus_channel"]
        if self.config.scope.check_errors:
            required.append("scope.errors")
        self._require("scope.focus_channel", *required)
        with self._scope_session() as scope:
            scope.focus_channel(
                channel,
                time_range_s=time_range_s,
                vertical_scale_v_per_div=vertical_scale_v_per_div,
                hide_other_channels=hide_other_channels,
                check_errors=self.config.scope.check_errors,
            )
        affected = [f"CH{channel}.display"]
        if time_range_s is not None:
            affected.append("timebase.range")
        if vertical_scale_v_per_div is not None:
            affected.append(f"CH{channel}.vertical_scale")
            affected.append(f"CH{channel}.offset")
        if hide_other_channels:
            affected.extend(f"CH{other}.display" for other in range(1, 5) if other != channel)
        return {
            "operation": "scope.focus_channel",
            "mutates_instrument": True,
            "raw_scpi": False,
            "channel": channel,
            "time_range_s": time_range_s,
            "vertical_scale_v_per_div": vertical_scale_v_per_div,
            "hide_other_channels": hide_other_channels,
            "affected_settings": affected,
        }

    def fetch_waveform(self, channel: int) -> WaveformData:
        if self.config.waveform.format.lower() != "real":
            raise ConfigError("MVP-1 only supports waveform.format = 'real'")
        if self.config.waveform.byte_order.lower() != "lsbf":
            raise ConfigError("MVP-1 only supports waveform.byte_order = 'lsbf'")
        required = ["scope.fetch_waveform"]
        if self.config.scope.check_errors:
            required.append("scope.errors")
        self._require("scope.fetch_waveform", *required)
        with self._scope_session() as scope:
            return scope.fetch_waveform(
                channel=channel,
                points=self.config.waveform.points,
                check_errors=self.config.scope.check_errors,
            )

    def _write_waveform_files(self, package_dir: Path, channel: int, waveform: WaveformData) -> dict[str, str]:
        times = waveform.times_s
        files: dict[str, str] = {}
        staged: list[tuple[Path, Path, str]] = []
        promoted: list[Path] = []
        try:
            if self.config.output.save_csv:
                csv_path = package_dir / f"ch{channel}.csv"
                csv_tmp_path = package_dir / f".ch{channel}.csv.tmp"
                staged.append((csv_tmp_path, csv_path, "csv"))
                with csv_tmp_path.open("w", newline="", encoding="utf-8") as file:
                    writer = csv.writer(file)
                    writer.writerow(["index", "time_s", "voltage_v"])
                    for index, (time_s, voltage_v) in enumerate(
                        zip(times, waveform.voltages_v)
                    ):
                        writer.writerow(
                            [index, f"{time_s:.12e}", f"{float(voltage_v):.12e}"]
                        )
            if self.config.output.save_npy:
                npy_path = package_dir / f"ch{channel}.npy"
                npy_tmp_path = package_dir / f".ch{channel}.npy.tmp"
                staged.append((npy_tmp_path, npy_path, "npy"))
                with npy_tmp_path.open("wb") as file:
                    np.save(file, np.column_stack((times, waveform.voltages_v)))
            for temporary, final, kind in staged:
                os.replace(temporary, final)
                promoted.append(final)
                files[kind] = str(final)
        except Exception:
            for final in promoted:
                final.unlink(missing_ok=True)
            raise
        finally:
            for temporary, _, _ in staged:
                temporary.unlink(missing_ok=True)
        return files


    def _write_screenshot_file(self, package_dir: Path, scope: ScopeDriver) -> tuple[Path | None, dict[str, str] | None]:
        if not self.config.output.save_screenshot:
            return None, None
        screenshot_path = package_dir / "screenshot.png"
        try:
            screenshot_path.write_bytes(scope.screenshot_png(include_menu=False, color_scheme="COL"))
        except Exception as exc:
            return None, {"type": type(exc).__name__, "message": str(exc)}
        return screenshot_path, None

    def _waveform_metadata(self, waveform: WaveformData) -> dict[str, Any]:
        return {
            "header": {
                "x_start_s": waveform.header.x_start,
                "x_stop_s": waveform.header.x_stop,
                "x_increment_s": waveform.header.x_increment,
                "points": waveform.header.points,
                "segment": waveform.header.segment,
            },
            "summary": waveform.summary(
                expected_frequency_hz=self.config.waveform.expected_frequency_hz,
                frequency_tolerance_ratio=self.config.waveform.frequency_tolerance_ratio,
            ),
        }

    def _failed_capture_package(
        self,
        *,
        package_dir: Path,
        operation: dict[str, Any],
        exc: Exception,
        commands_log_path: Path | None,
        partial: dict[str, Any] | None = None,
    ) -> None:
        failed_dir = package_dir.with_name(package_dir.name + "_failed")
        package_dir.rename(failed_dir)

        def rewrite_failed_paths(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: rewrite_failed_paths(item) for key, item in value.items()}
            if isinstance(value, list):
                return [rewrite_failed_paths(item) for item in value]
            if isinstance(value, str):
                prefix = str(package_dir)
                if value == prefix or value.startswith(prefix + os.sep):
                    return str(failed_dir) + value[len(prefix) :]
            return value
        (failed_dir / "error.txt").write_text(
            f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
            encoding="utf-8",
        )
        partial_metadata: dict[str, Any] = {
            "instrument": {"resource": self.config.connection.resource},
            "operation": {**operation, "failed": True},
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "files": {"commands": str(failed_dir / "commands.log")} if commands_log_path is not None else {},
        }
        if partial is not None:
            partial_metadata.update(rewrite_failed_paths(partial))
        (failed_dir / "metadata.partial.json").write_text(
            json.dumps(partial_metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def capture_waveform(self, channel: int, label: str) -> CaptureResult:
        required = ["scope.idn", "scope.capture_waveform"]
        if self.config.scope.check_errors:
            required.append("scope.errors")
        if self.config.output.save_screenshot:
            required.append("scope.screenshot")
        self._require("scope.capture", *required)
        package_dir = new_package_dir(self.config.output.directory, label)
        package_dir.mkdir(parents=True, exist_ok=False)
        commands_log_path = package_dir / "commands.log" if self.config.output.save_commands_log else None
        if commands_log_path is not None:
            self.logger.path = commands_log_path
        operation = {
            "command": "scope capture",
            "channel": channel,
            "label": label,
            "time_range_s": self.config.waveform.time_range_s,
            "expected_frequency_hz": self.config.waveform.expected_frequency_hz,
            "target_cycles": self.config.waveform.target_cycles,
            "window_frequency_hz": self.config.waveform.window_frequency_hz,
            "frequency_tolerance_ratio": self.config.waveform.frequency_tolerance_ratio,
            "vertical_scale_v_per_div": self.config.waveform.vertical_scale_v_per_div,
            "target_vpp": self.config.waveform.target_vpp,
        }
        screenshot_path: Path | None = None
        screenshot_error: dict[str, str] | None = None
        try:
            with self._scope_session() as scope:
                instrument_idn = scope.idn()
                capture_kwargs = {
                    "channel": channel,
                    "points": self.config.waveform.points,
                    "check_errors": self.config.scope.check_errors,
                    "time_range_s": self.config.waveform.time_range_s,
                }
                if self.config.waveform.vertical_scale_v_per_div is not None:
                    capture_kwargs["vertical_scale_v_per_div"] = self.config.waveform.vertical_scale_v_per_div
                waveform = scope.capture_waveform(**capture_kwargs)
                screenshot_path, screenshot_error = self._write_screenshot_file(package_dir, scope)
        except Exception as exc:
            self._failed_capture_package(
                package_dir=package_dir,
                operation=operation,
                exc=exc,
                commands_log_path=commands_log_path,
            )
            if isinstance(exc, WaveBenchError):
                raise
            raise

        files = self._write_waveform_files(package_dir, channel, waveform)
        if self.config.output.save_screenshot:
            files["screenshot"] = str(screenshot_path) if screenshot_path is not None else None
        metadata: dict[str, Any] = {
            "instrument": {"idn": instrument_idn, "resource": self.config.connection.resource},
            "operation": {**operation, "triggered_single": True},
            "waveform": self._waveform_metadata(waveform),
            "files": files,
        }
        if screenshot_error is not None:
            metadata["screenshot_error"] = screenshot_error
        metadata_path = package_dir / "metadata.json"
        if self.config.output.save_json:
            metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        return CaptureResult(
            package_dir=package_dir,
            waveform=waveform,
            metadata_path=metadata_path,
            csv_path=Path(files["csv"]) if "csv" in files else None,
            npy_path=Path(files["npy"]) if "npy" in files else None,
            screenshot_path=screenshot_path,
            commands_log_path=commands_log_path,
        )

    def capture_waveforms(self, channels: list[int], label: str) -> MultiCaptureResult:
        if not channels:
            raise ConfigError("at least one channel is required")
        if len(set(channels)) != len(channels):
            raise ConfigError("duplicate channels are not allowed")
        required = ["scope.idn", "scope.capture_waveforms"]
        if self.config.scope.check_errors:
            required.append("scope.errors")
        if self.config.output.save_screenshot:
            required.append("scope.screenshot")
        self._require("scope.capture_multiple", *required)
        package_dir = new_package_dir(self.config.output.directory, label)
        package_dir.mkdir(parents=True, exist_ok=False)
        commands_log_path = package_dir / "commands.log" if self.config.output.save_commands_log else None
        if commands_log_path is not None:
            self.logger.path = commands_log_path
        operation = {
            "command": "scope capture",
            "channels": channels,
            "label": label,
            "time_range_s": self.config.waveform.time_range_s,
            "expected_frequency_hz": self.config.waveform.expected_frequency_hz,
            "target_cycles": self.config.waveform.target_cycles,
            "window_frequency_hz": self.config.waveform.window_frequency_hz,
            "frequency_tolerance_ratio": self.config.waveform.frequency_tolerance_ratio,
            "vertical_scale_v_per_div": self.config.waveform.vertical_scale_v_per_div,
            "target_vpp": self.config.waveform.target_vpp,
        }
        waveforms: dict[int, WaveformData] = {}
        files: dict[str, dict[str, str]] = {}
        channel_metadata: dict[str, Any] = {}
        completed_channels: list[int] = []
        failed_channel: int | None = None
        stage = "open_session"
        screenshot_path: Path | None = None
        screenshot_error: dict[str, str] | None = None
        scope: MultiChannelScopeDriver | None = None
        try:
            with self._scope_session() as opened_scope:
                scope = opened_scope
                try:
                    stage = "identify"
                    instrument_idn = scope.idn()
                    capture_kwargs: dict[str, Any] = {
                        "channels": channels,
                        "points": self.config.waveform.points,
                        "check_errors": self.config.scope.check_errors,
                        "time_range_s": self.config.waveform.time_range_s,
                    }
                    if self.config.waveform.vertical_scale_v_per_div is not None:
                        capture_kwargs["vertical_scale_v_per_div"] = self.config.waveform.vertical_scale_v_per_div

                    def start_channel(channel: int | None) -> None:
                        nonlocal failed_channel, stage
                        if channel is None:
                            failed_channel = None
                            stage = "check_errors"
                        else:
                            failed_channel = channel
                            stage = "read_waveform"

                    def save_waveform(channel: int, waveform: WaveformData) -> None:
                        nonlocal failed_channel, stage
                        failed_channel = channel
                        stage = "write_waveform"
                        key = str(channel)
                        files[key] = self._write_waveform_files(package_dir, channel, waveform)
                        channel_metadata[key] = self._waveform_metadata(waveform)
                        waveforms[channel] = waveform
                        completed_channels.append(channel)
                        failed_channel = None
                        stage = "read_waveform"

                    stage = "acquire"
                    failed_channel = None
                    capture_kwargs["on_channel_start"] = start_channel
                    capture_kwargs["on_waveform"] = save_waveform
                    returned_waveforms = scope.capture_waveforms(**capture_kwargs)
                    for channel in channels:
                        if channel not in waveforms:
                            save_waveform(channel, returned_waveforms[channel])
                    failed_channel = None
                    stage = "screenshot"
                    screenshot_path, screenshot_error = self._write_screenshot_file(
                        package_dir, scope
                    )
                except Exception:
                    if self.config.output.save_screenshot:
                        screenshot_path, screenshot_error = self._write_screenshot_file(
                            package_dir, scope
                        )
                    raise
        except Exception as exc:
            partial_files: dict[str, Any] = dict(files)
            if commands_log_path is not None:
                partial_files["commands"] = str(package_dir / "commands.log")
            if screenshot_path is not None:
                partial_files["screenshot"] = str(package_dir / "screenshot.png")
            self._failed_capture_package(
                package_dir=package_dir,
                operation=operation,
                exc=exc,
                commands_log_path=commands_log_path,
                partial={
                    "completed_channels": completed_channels,
                    "failed_channel": failed_channel,
                    "stage": stage,
                    "channels": channel_metadata,
                    "files": partial_files,
                    **(
                        {"screenshot_error": screenshot_error}
                        if screenshot_error is not None
                        else {}
                    ),
                },
            )
            if isinstance(exc, WaveBenchError):
                raise
            raise

        metadata_files: dict[str, Any] = dict(files)
        if self.config.output.save_screenshot:
            metadata_files["screenshot"] = str(screenshot_path) if screenshot_path is not None else None
        metadata: dict[str, Any] = {
            "instrument": {"idn": instrument_idn, "resource": self.config.connection.resource},
            "operation": {**operation, "triggered_single": True, "trigger_mode": "single_acquisition"},
            "channels": channel_metadata,
            "files": metadata_files,
        }
        if screenshot_error is not None:
            metadata["screenshot_error"] = screenshot_error
        metadata_path = package_dir / "metadata.json"
        if self.config.output.save_json:
            metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        return MultiCaptureResult(
            package_dir=package_dir,
            waveforms=waveforms,
            metadata_path=metadata_path,
            files=files,
            screenshot_path=screenshot_path,
            commands_log_path=commands_log_path,
        )
