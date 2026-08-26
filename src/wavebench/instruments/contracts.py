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
    DmmSystemInterfaceStatus,
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
    ScopeChannelInputStateV2,
    ScopeHistoryTimestamps,
    ScopeMeasurementStatistics,
    ScopeMeasurementStatisticsRequestV2,
    ScopeMeasurementStatisticsV2,
    ScopeCursorReadout,
    ScopeCursorReadoutV2,
    ScopeDerivedWaveformMetadata,
    ScopeDigitalChannelStatus,
    ScopeDigitalChannelStatusV2,
    ScopeDigitalWaveform,
    ScopeDigitalWaveformRequest,
    ScopeFftStatus,
    ScopeFftStatusV2,
    ScopeSnapshot,
    ScopeSnapshotFieldV2,
    ScopeSnapshotV2,
    SourceAmModulationConfiguration,
    SourceAmModulationProfile,
    SourceBurstConfiguration,
    SourceBurstProfile,
    SourceChannelProfile,
    SourceCouplingConfiguration,
    SourceCouplingProfile,
    SourceCounterProfile,
    SourceFmModulationConfiguration,
    SourceFmModulationProfile,
    SourceHarmonicConfiguration,
    SourceHarmonicProfile,
    SourcePmModulationConfiguration,
    SourcePmModulationProfile,
    SourcePulseConfiguration,
    SourcePulseProfile,
    SourcePwmModulationConfiguration,
    SourcePwmModulationProfile,
    SourceSweepConfiguration,
    SourceSweepProfile,
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
class ScopeSnapshotDriverV2(InstrumentDriver, Protocol):
    def get_snapshot_v2(
        self,
        channel: int,
        *,
        fields: tuple[ScopeSnapshotFieldV2, ...],
    ) -> ScopeSnapshotV2: ...


@runtime_checkable
class ScopeAcquisitionStatusDriver(InstrumentDriver, Protocol):
    def get_acquisition_status(self) -> ScopeAcquisitionStatus: ...


@runtime_checkable
class ScopeChannelInputStateDriverV2(InstrumentDriver, Protocol):
    def get_channel_input_state_v2(self, channel: int) -> ScopeChannelInputStateV2: ...


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
class ScopeDigitalStatusDriverV2(InstrumentDriver, Protocol):
    def get_digital_status_v2(self, channel: int) -> ScopeDigitalChannelStatusV2: ...


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
class ScopeMeasurementStatisticsDriverV2(InstrumentDriver, Protocol):
    def get_measurement_statistics_v2(
        self,
        request: ScopeMeasurementStatisticsRequestV2,
    ) -> ScopeMeasurementStatisticsV2: ...


@runtime_checkable
class ScopeFftStatusDriverV2(InstrumentDriver, Protocol):
    def get_fft_status_v2(
        self,
        math_index: int,
        *,
        configured_fft: bool,
    ) -> ScopeFftStatusV2: ...


@runtime_checkable
class ScopeCursorReadoutDriverV2(InstrumentDriver, Protocol):
    def get_cursor_readout_v2(
        self,
        cursor_index: int | None,
        *,
        configured_cursor: bool,
    ) -> ScopeCursorReadoutV2: ...


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
class SourceChannelProfileDriver(InstrumentDriver, Protocol):
    def get_channel_profile(self, channel: int) -> SourceChannelProfile: ...


@runtime_checkable
class SourceCouplingDriver(InstrumentDriver, Protocol):
    def get_coupling_profile(self) -> SourceCouplingProfile: ...

    def configure_coupling(
        self,
        configuration: SourceCouplingConfiguration,
        *,
        check_errors: bool = True,
    ) -> SourceCouplingProfile: ...


@runtime_checkable
class SourceHarmonicProfileDriver(InstrumentDriver, Protocol):
    def get_harmonic_profile(self, channel: int) -> SourceHarmonicProfile: ...


@runtime_checkable
class SourceHarmonicControlDriver(SourceHarmonicProfileDriver, Protocol):
    def configure_harmonics(
        self,
        channel: int,
        configuration: SourceHarmonicConfiguration,
        *,
        check_errors: bool = True,
    ) -> SourceHarmonicProfile: ...


@runtime_checkable
class SourceAmModulationProfileDriver(InstrumentDriver, Protocol):
    def get_am_modulation_profile(self, channel: int) -> SourceAmModulationProfile: ...


@runtime_checkable
class SourceAmModulationControlDriver(SourceAmModulationProfileDriver, Protocol):
    def configure_am_modulation(
        self,
        channel: int,
        configuration: SourceAmModulationConfiguration,
        *,
        check_errors: bool = True,
    ) -> SourceAmModulationProfile: ...


@runtime_checkable
class SourceFmModulationProfileDriver(InstrumentDriver, Protocol):
    def get_fm_modulation_profile(self, channel: int) -> SourceFmModulationProfile: ...


@runtime_checkable
class SourceFmModulationControlDriver(SourceFmModulationProfileDriver, Protocol):
    def configure_fm_modulation(
        self,
        channel: int,
        configuration: SourceFmModulationConfiguration,
        *,
        check_errors: bool = True,
    ) -> SourceFmModulationProfile: ...


@runtime_checkable
class SourcePmModulationProfileDriver(InstrumentDriver, Protocol):
    def get_pm_modulation_profile(self, channel: int) -> SourcePmModulationProfile: ...


@runtime_checkable
class SourcePmModulationControlDriver(SourcePmModulationProfileDriver, Protocol):
    def configure_pm_modulation(
        self,
        channel: int,
        configuration: SourcePmModulationConfiguration,
        *,
        check_errors: bool = True,
    ) -> SourcePmModulationProfile: ...


@runtime_checkable
class SourcePwmModulationProfileDriver(InstrumentDriver, Protocol):
    def get_pwm_modulation_profile(self, channel: int) -> SourcePwmModulationProfile: ...


@runtime_checkable
class SourcePwmModulationControlDriver(SourcePwmModulationProfileDriver, Protocol):
    def configure_pwm_modulation(
        self,
        channel: int,
        configuration: SourcePwmModulationConfiguration,
        *,
        check_errors: bool = True,
    ) -> SourcePwmModulationProfile: ...


@runtime_checkable
class SourcePulseProfileDriver(InstrumentDriver, Protocol):
    def get_pulse_profile(self, channel: int) -> SourcePulseProfile: ...


@runtime_checkable
class SourcePulseControlDriver(SourcePulseProfileDriver, Protocol):
    def configure_pulse(
        self,
        channel: int,
        configuration: SourcePulseConfiguration,
        *,
        check_errors: bool = True,
    ) -> SourcePulseProfile: ...


@runtime_checkable
class SourceBurstProfileDriver(InstrumentDriver, Protocol):
    def get_burst_profile(self, channel: int) -> SourceBurstProfile: ...


@runtime_checkable
class SourceBurstControlDriver(SourceBurstProfileDriver, Protocol):
    """Fail-closed burst configuration plus explicit non-retryable manual trigger."""

    def configure_burst(
        self,
        channel: int,
        configuration: SourceBurstConfiguration,
        *,
        check_errors: bool = True,
    ) -> SourceBurstProfile: ...

    def trigger_burst(
        self,
        channel: int,
        *,
        check_errors: bool = True,
    ) -> None: ...


@runtime_checkable
class SourceSweepProfileDriver(InstrumentDriver, Protocol):
    def get_sweep_profile(self, channel: int) -> SourceSweepProfile: ...


@runtime_checkable
class SourceSweepControlDriver(InstrumentDriver, Protocol):
    """Fail-closed sweep configuration and explicit single-trigger operations.

    Manual-trigger authorization may be instance-bound. Callers must therefore configure and
    trigger a manual sweep through the same live driver instance.
    """

    def configure_sweep(
        self,
        channel: int,
        configuration: SourceSweepConfiguration,
        *,
        check_errors: bool = True,
    ) -> SourceSweepProfile: ...

    def trigger_sweep(
        self,
        channel: int,
        *,
        check_errors: bool = True,
    ) -> None:
        """Issue one authorized manual trigger after fresh checks; never retry it."""
        ...


@runtime_checkable
class SourceCounterProfileDriver(InstrumentDriver, Protocol):
    def get_counter_profile(self) -> SourceCounterProfile: ...


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
class DmmSystemInterfaceStatusDriver(InstrumentDriver, Protocol):
    def system_interface_status(self) -> DmmSystemInterfaceStatus: ...


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
