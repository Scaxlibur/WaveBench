from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from .models import (
    ArbitraryQueryProbeResult,
    DmmCalculationStatistics,
    DmmCalculationStatus,
    DmmDcvImpedanceConfiguration,
    DmmReading,
    DmmMeasurementProfile,
    DmmTriggerStatus,
    DmmVoltageRangeConfiguration,
    PowerMeasurement,
    PowerProtectionStatus,
    PowerStatus,
    FrequencyResponseTrace,
    InstrumentMeasurementResult,
    MarkerReading,
    ScopeAverageCaptureRequest,
    ScopeAverageCaptureResult,
    ScopeAcquisitionStatus,
    ScopeHistoryTimestamps,
    ScopeMeasurementStatistics,
    ScopeCursorReadout,
    ScopeDerivedWaveformMetadata,
    ScopeDigitalChannelStatus,
    ScopeDigitalWaveform,
    ScopeDigitalWaveformRequest,
    ScopeFftStatus,
    ScopeSnapshot,
    SourceStatus,
    SweepAnalyzerSnapshot,
    SweepPlan,
    WaveformData,
)
from .dg4000 import DG4000DacBlock


@runtime_checkable
class InstrumentDriver(Protocol):
    def idn(self) -> str: ...

    def close(self) -> None: ...


@runtime_checkable
class ScopeDriver(InstrumentDriver, Protocol):
    def errors(self, limit: int = 16) -> list[str]: ...

    def channel_coupling(self, channel: int) -> str: ...

    def autoscale(self, wait_opc: bool = True, check_errors: bool = True) -> None: ...

    def fetch_waveform(
        self,
        channel: int,
        points: str = "dmax",
        check_errors: bool = True,
    ) -> WaveformData: ...

    def capture_waveform(
        self,
        channel: int,
        points: str = "dmax",
        check_errors: bool = True,
        time_range_s: float | None = None,
        vertical_scale_v_per_div: float | None = None,
    ) -> WaveformData: ...

    def screenshot_png(
        self,
        *,
        include_menu: bool = False,
        color_scheme: str = "COL",
    ) -> bytes: ...


@runtime_checkable
class MultiChannelScopeDriver(ScopeDriver, Protocol):
    def capture_waveforms(
        self,
        channels: list[int],
        points: str = "dmax",
        check_errors: bool = True,
        time_range_s: float | None = None,
        vertical_scale_v_per_div: float | None = None,
        on_channel_start: Callable[[int | None], None] | None = None,
        on_waveform: Callable[[int, WaveformData], None] | None = None,
    ) -> dict[int, WaveformData]: ...


@runtime_checkable
class ScopeSnapshotDriver(InstrumentDriver, Protocol):
    def get_snapshot(self, channel: int) -> ScopeSnapshot: ...


@runtime_checkable
class ScopeAcquisitionStatusDriver(InstrumentDriver, Protocol):
    def get_acquisition_status(self) -> ScopeAcquisitionStatus: ...


@runtime_checkable
class ScopeAverageCaptureDriver(InstrumentDriver, Protocol):
    def capture_average(
        self,
        request: ScopeAverageCaptureRequest,
    ) -> ScopeAverageCaptureResult: ...


@runtime_checkable
class ScopeDigitalStatusDriver(InstrumentDriver, Protocol):
    def get_digital_status(self, channel: int) -> ScopeDigitalChannelStatus: ...


@runtime_checkable
class ScopeDigitalWaveformDriver(InstrumentDriver, Protocol):
    def get_digital_waveform(
        self,
        request: ScopeDigitalWaveformRequest,
    ) -> ScopeDigitalWaveform: ...


@runtime_checkable
class ScopeHistoryTimestampsDriver(InstrumentDriver, Protocol):
    def get_history_timestamps(self, channel: int) -> ScopeHistoryTimestamps: ...


@runtime_checkable
class ScopeMeasurementStatisticsDriver(InstrumentDriver, Protocol):
    def get_measurement_statistics(
        self,
        slot: int,
        *,
        configured_slot: bool,
        include_buffer: bool = False,
        acquisition_stopped: bool = False,
    ) -> ScopeMeasurementStatistics: ...


@runtime_checkable
class ScopeAnalysisReadDriver(InstrumentDriver, Protocol):
    def get_math_waveform_metadata(self, math_index: int) -> ScopeDerivedWaveformMetadata: ...

    def get_fft_status(
        self,
        math_index: int,
        *,
        configured_fft: bool,
    ) -> ScopeFftStatus: ...

    def get_reference_waveform_metadata(
        self,
        reference_index: int,
    ) -> ScopeDerivedWaveformMetadata: ...

    def get_cursor_readout(
        self,
        cursor_index: int,
        *,
        configured_cursor: bool,
    ) -> ScopeCursorReadout: ...


@runtime_checkable
class SourceDriver(InstrumentDriver, Protocol):
    def errors(self, limit: int = 8) -> list[str]: ...

    def assert_no_errors(self) -> None: ...

    def get_status(self, channel: int) -> SourceStatus: ...

    def set_frequency(
        self,
        channel: int,
        value_hz: float,
        *,
        ensure_fix_mode: bool = True,
        check_errors: bool = True,
    ) -> SourceStatus: ...

    def set_output(
        self,
        channel: int,
        enabled: bool,
        *,
        check_errors: bool = True,
    ) -> SourceStatus: ...

    def set_function(
        self,
        channel: int,
        function: str,
        *,
        check_errors: bool = True,
    ) -> SourceStatus: ...

    def set_amplitude_vpp(
        self,
        channel: int,
        value_vpp: float,
        *,
        check_errors: bool = True,
    ) -> SourceStatus: ...

    def set_square_duty_cycle(
        self,
        channel: int,
        duty_percent: float,
        *,
        check_errors: bool = True,
    ) -> SourceStatus: ...

    def upload_dg4000_dac14_block(
        self,
        *,
        channel: int,
        block: DG4000DacBlock,
        playback_frequency_hz: float,
        amplitude_vpp: float,
        offset_v: float = 0.0,
        output_on: bool = False,
        check_errors: bool = True,
    ) -> SourceStatus: ...

    def probe_arbitrary_queries(self, channel: int) -> list[ArbitraryQueryProbeResult]: ...


@runtime_checkable
class PowerDriver(InstrumentDriver, Protocol):
    def get_status(self, channel: int) -> PowerStatus: ...

    def get_measurement(self, channel: int) -> PowerMeasurement: ...

    def get_protection_status(self, channel: int) -> PowerProtectionStatus: ...

    def set_protection(self, channel: int, **kwargs: Any) -> PowerProtectionStatus: ...

    def set_voltage_current_limit(
        self,
        channel: int,
        voltage_v: float,
        current_limit_a: float,
        **kwargs: Any,
    ) -> PowerStatus: ...

    def set_output(self, channel: int, enabled: bool, **kwargs: Any) -> PowerStatus: ...


@runtime_checkable
class DmmDriver(InstrumentDriver, Protocol):
    def function_status(self) -> str: ...

    def set_function(self, function: str) -> str: ...

    def apply_function(self, function: str) -> str: ...

    def read(self, function: str = "dcv") -> DmmReading: ...


@runtime_checkable
class DmmMeasurementProfileDriver(InstrumentDriver, Protocol):
    def measurement_profile(self) -> DmmMeasurementProfile: ...


@runtime_checkable
class DmmTriggerStatusDriver(InstrumentDriver, Protocol):
    def trigger_status(self) -> DmmTriggerStatus: ...


@runtime_checkable
class DmmCalculationStatusDriver(InstrumentDriver, Protocol):
    def calculation_status(self) -> DmmCalculationStatus: ...


@runtime_checkable
class DmmCalculationStatisticsDriver(InstrumentDriver, Protocol):
    def calculation_statistics(
        self,
        expected_function: str,
    ) -> DmmCalculationStatistics: ...


@runtime_checkable
class DmmVoltageConfigurationDriver(InstrumentDriver, Protocol):
    def set_voltage_range(
        self,
        function: str,
        range_code: int,
    ) -> DmmVoltageRangeConfiguration: ...

    def set_dcv_impedance(self, impedance: str) -> DmmDcvImpedanceConfiguration: ...


@runtime_checkable
class SweepAnalyzerDriver(InstrumentDriver, Protocol):
    def get_snapshot(self) -> SweepAnalyzerSnapshot: ...

    def fetch_frequency_response(self) -> FrequencyResponseTrace: ...

    def apply_sweep_plan(self, plan: SweepPlan) -> SweepAnalyzerSnapshot: ...

    def trigger_single(self) -> None: ...

    def set_source_output(self, enabled: bool) -> SweepAnalyzerSnapshot: ...

    def read_markers(self) -> tuple[MarkerReading, ...]: ...

    def read_measurements(self) -> tuple[InstrumentMeasurementResult, ...]: ...
