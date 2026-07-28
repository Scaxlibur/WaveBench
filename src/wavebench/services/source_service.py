from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite
import time
from typing import cast

from wavebench.arbitrary import build_dg4000_dac14_binary_block, load_arbitrary_waveform
from wavebench.config import SourceConfig, WaveBenchConfig
from wavebench.errors import ConfigError
from wavebench.instruments.contracts import (
    SourceAmModulationControlDriver,
    SourceAmModulationProfileDriver,
    SourceBurstControlDriver,
    SourceBurstProfileDriver,
    SourceChannelProfileDriver,
    SourceCouplingDriver,
    SourceCounterProfileDriver,
    SourceDriver,
    SourceFmModulationControlDriver,
    SourceFmModulationProfileDriver,
    SourcePmModulationControlDriver,
    SourcePmModulationProfileDriver,
    SourcePulseControlDriver,
    SourcePulseProfileDriver,
    SourcePwmModulationControlDriver,
    SourcePwmModulationProfileDriver,
    SourceSweepControlDriver,
    SourceSweepProfileDriver,
)
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.capabilities import require_capabilities
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import (
    ArbitraryQueryProbeResult,
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
    SourcePmModulationConfiguration,
    SourcePmModulationProfile,
    SourcePulseConfiguration,
    SourcePulseProfile,
    SourcePwmModulationConfiguration,
    SourcePwmModulationProfile,
    SourceSweepConfiguration,
    SourceSweepProfile,
    SourceStatus,
)
from wavebench.logging import CommandLogger
from wavebench.instruments.registry import resolve_instrument_descriptor
from wavebench.services.source_state import RestorableSourceState


@dataclass
class SourceService:
    config: WaveBenchConfig
    logger: CommandLogger
    session: SourceDriver | None = None
    descriptor: InstrumentDescriptor | None = None

    def _require(self, operation: str, *capabilities: str) -> None:
        source = self._source_config()
        descriptor = self.descriptor or resolve_instrument_descriptor(
            source.driver,
            expected_kind="source",
        )
        require_capabilities(descriptor, capabilities, operation=operation)

    def _source_config(self) -> SourceConfig:
        if self.config.source is None or not self.config.source.resource:
            raise ConfigError("source resource is not configured. Set [source].resource or pass --resource.")
        return self.config.source

    def _open_source(self) -> SourceDriver:
        source = self._source_config()
        opened = open_instrument_driver(
            driver_reference=source.driver,
            expected_kind="source",
            resource=source.resource or "",
            configured_backend=self.config.connection.backend,
            timeout_ms=self.config.connection.timeout_ms,
            opc_timeout_ms=self.config.connection.opc_timeout_ms,
            read_retry_attempts=self.config.connection.read_retry_attempts,
            read_retry_delay_ms=self.config.connection.read_retry_delay_ms,
            logger=self.logger,
            settings={"check_errors": source.check_errors},
            options=getattr(source, "options", {}),
        )
        self.descriptor = opened.descriptor
        return opened.driver

    def open_session(self) -> SourceDriver:
        return self._open_source()

    @contextmanager
    def _source_session(self) -> Iterator[SourceDriver]:
        if self.session is not None:
            yield self.session
            return
        source = self._open_source()
        try:
            yield source
        finally:
            source.close()

    def idn(self) -> str:
        self._require("source.idn", "source.idn")
        with self._source_session() as source:
            return source.idn()

    def errors(self) -> list[str]:
        self._require("source.errors", "source.errors")
        with self._source_session() as source:
            return source.errors()

    def status(self, channel: int | None = None) -> SourceStatus:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.status", "source.status")
        with self._source_session() as source:
            return source.get_status(channel)

    def channel_profile(self, channel: int | None = None) -> SourceChannelProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.channel_profile", "source.channel_profile")
        with self._source_session() as source:
            return cast(SourceChannelProfileDriver, source).get_channel_profile(channel)

    def coupling_profile(self) -> SourceCouplingProfile:
        self._require("source.coupling_profile", "source.coupling_profile")
        with self._source_session() as source:
            return cast(SourceCouplingDriver, source).get_coupling_profile()

    def configure_coupling(
        self,
        configuration: SourceCouplingConfiguration,
    ) -> SourceCouplingProfile:
        if not isinstance(configuration, SourceCouplingConfiguration):
            raise ConfigError("source coupling configuration must be SourceCouplingConfiguration")
        source_cfg = self._source_config()
        required = ["source.coupling_configure"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.coupling_configure", *required)
        with self._source_session() as source:
            return cast(SourceCouplingDriver, source).configure_coupling(
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def am_modulation_profile(self, channel: int | None = None) -> SourceAmModulationProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.modulation_am_profile", "source.modulation_am_profile")
        with self._source_session() as source:
            return cast(SourceAmModulationProfileDriver, source).get_am_modulation_profile(channel)

    def configure_am_modulation(
        self,
        configuration: SourceAmModulationConfiguration,
        channel: int | None = None,
    ) -> SourceAmModulationProfile:
        if not isinstance(configuration, SourceAmModulationConfiguration):
            raise ConfigError(
                "source AM modulation configuration must be SourceAmModulationConfiguration"
            )
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.modulation_am_configure"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.modulation_am_configure", *required)
        with self._source_session() as source:
            return cast(SourceAmModulationControlDriver, source).configure_am_modulation(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def fm_modulation_profile(self, channel: int | None = None) -> SourceFmModulationProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.modulation_fm_profile", "source.modulation_fm_profile")
        with self._source_session() as source:
            return cast(SourceFmModulationProfileDriver, source).get_fm_modulation_profile(channel)

    def configure_fm_modulation(
        self,
        configuration: SourceFmModulationConfiguration,
        channel: int | None = None,
    ) -> SourceFmModulationProfile:
        if not isinstance(configuration, SourceFmModulationConfiguration):
            raise ConfigError(
                "source FM modulation configuration must be SourceFmModulationConfiguration"
            )
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.modulation_fm_configure"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.modulation_fm_configure", *required)
        with self._source_session() as source:
            return cast(SourceFmModulationControlDriver, source).configure_fm_modulation(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def pm_modulation_profile(self, channel: int | None = None) -> SourcePmModulationProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.modulation_pm_profile", "source.modulation_pm_profile")
        with self._source_session() as source:
            return cast(SourcePmModulationProfileDriver, source).get_pm_modulation_profile(channel)

    def configure_pm_modulation(
        self,
        configuration: SourcePmModulationConfiguration,
        channel: int | None = None,
    ) -> SourcePmModulationProfile:
        if not isinstance(configuration, SourcePmModulationConfiguration):
            raise ConfigError(
                "source PM modulation configuration must be SourcePmModulationConfiguration"
            )
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.modulation_pm_configure"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.modulation_pm_configure", *required)
        with self._source_session() as source:
            return cast(SourcePmModulationControlDriver, source).configure_pm_modulation(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def pwm_modulation_profile(self, channel: int | None = None) -> SourcePwmModulationProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.modulation_pwm_profile", "source.modulation_pwm_profile")
        with self._source_session() as source:
            return cast(SourcePwmModulationProfileDriver, source).get_pwm_modulation_profile(
                channel
            )

    def configure_pwm_modulation(
        self,
        configuration: SourcePwmModulationConfiguration,
        channel: int | None = None,
    ) -> SourcePwmModulationProfile:
        if not isinstance(configuration, SourcePwmModulationConfiguration):
            raise ConfigError(
                "source PWM modulation configuration must be SourcePwmModulationConfiguration"
            )
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.modulation_pwm_configure"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.modulation_pwm_configure", *required)
        with self._source_session() as source:
            return cast(SourcePwmModulationControlDriver, source).configure_pwm_modulation(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def pulse_profile(self, channel: int | None = None) -> SourcePulseProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.pulse_profile", "source.pulse_profile")
        with self._source_session() as source:
            return cast(SourcePulseProfileDriver, source).get_pulse_profile(channel)

    def burst_profile(self, channel: int | None = None) -> SourceBurstProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.burst_profile", "source.burst_profile")
        with self._source_session() as source:
            return cast(SourceBurstProfileDriver, source).get_burst_profile(channel)

    def sweep_profile(self, channel: int | None = None) -> SourceSweepProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.sweep_profile", "source.sweep_profile")
        with self._source_session() as source:
            return cast(SourceSweepProfileDriver, source).get_sweep_profile(channel)

    def counter_profile(self) -> SourceCounterProfile:
        self._require("source.counter_profile", "source.counter_profile")
        with self._source_session() as source:
            return cast(SourceCounterProfileDriver, source).get_counter_profile()

    def configure_pulse(
        self,
        configuration: SourcePulseConfiguration,
        channel: int | None = None,
    ) -> SourcePulseProfile:
        if not isinstance(configuration, SourcePulseConfiguration):
            raise ConfigError("source pulse configuration must be SourcePulseConfiguration")
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.pulse_configure", "source.status"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.pulse_configure", *required)
        with self._source_session() as source:
            if source.get_status(channel).output != "OFF":
                raise ConfigError(
                    "controlled source pulse configuration requires output OFF / "
                    "受控信号源脉冲配置要求输出为 OFF"
                )
            return cast(SourcePulseControlDriver, source).configure_pulse(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def configure_burst(
        self,
        configuration: SourceBurstConfiguration,
        channel: int | None = None,
    ) -> SourceBurstProfile:
        if not isinstance(configuration, SourceBurstConfiguration):
            raise ConfigError("source burst configuration must be SourceBurstConfiguration")
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.burst_configure", "source.status"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.burst_configure", *required)
        if configuration.trigger_source == "MANUAL" and self.session is None:
            raise ConfigError(
                "manual source burst configuration requires a persistent source session / "
                "手动触发突发配置要求持久信号源会话"
            )
        with self._source_session() as source:
            if source.get_status(channel).output != "OFF":
                raise ConfigError(
                    "controlled source burst configuration requires output OFF / "
                    "受控信号源突发配置要求输出为 OFF"
                )
            return cast(SourceBurstControlDriver, source).configure_burst(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def trigger_burst(self, channel: int | None = None) -> None:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.burst_trigger"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.burst_trigger", *required)
        if self.session is None:
            raise ConfigError(
                "manual source burst trigger requires the persistent source session that "
                "configured it / 手动突发触发必须复用执行配置的持久信号源会话"
            )
        cast(SourceBurstControlDriver, self.session).trigger_burst(
            channel,
            check_errors=source_cfg.check_errors,
        )

    def configure_sweep(
        self,
        configuration: SourceSweepConfiguration,
        channel: int | None = None,
    ) -> SourceSweepProfile:
        if not isinstance(configuration, SourceSweepConfiguration):
            raise ConfigError("source sweep configuration must be SourceSweepConfiguration")
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.sweep_configure", "source.status"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.sweep_configure", *required)
        if configuration.trigger_source == "MANUAL" and self.session is None:
            raise ConfigError(
                "manual source sweep configuration requires a persistent source session / "
                "手动触发扫频配置要求持久信号源会话"
            )
        with self._source_session() as source:
            status = source.get_status(channel)
            if status.output != "OFF":
                raise ConfigError(
                    "controlled source sweep configuration requires output OFF / "
                    "受控信号源扫频配置要求输出为 OFF"
                )
            if status.amplitude is None or status.amplitude_unit != "VPP":
                raise ConfigError(
                    "controlled source sweep requires a readable VPP amplitude / "
                    "受控信号源扫频要求可读的 VPP 幅度"
                )
            self._check_source_vpp(
                status.amplitude,
                field="source sweep amplitude / 信号源扫频幅度",
            )
            return cast(SourceSweepControlDriver, source).configure_sweep(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def trigger_sweep(self, channel: int | None = None) -> None:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.sweep_trigger"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.sweep_trigger", *required)
        if self.session is None:
            raise ConfigError(
                "manual source sweep trigger requires the persistent source session that "
                "configured it / 手动扫频触发必须复用执行配置的持久信号源会话"
            )
        cast(SourceSweepControlDriver, self.session).trigger_sweep(
            channel,
            check_errors=source_cfg.check_errors,
        )

    def snapshot_restorable_state(self, channel: int | None = None) -> RestorableSourceState:
        return RestorableSourceState.from_status(self.status(channel=channel))

    def restore_restorable_state(self, state: RestorableSourceState) -> SourceStatus:
        self.set_output(channel=state.channel, enabled=False)
        self.set_function(channel=state.channel, function=state.function)
        self.set_amplitude_vpp(channel=state.channel, value_vpp=state.amplitude_vpp)
        self.set_frequency(channel=state.channel, value_hz=state.frequency_hz)
        if state.square_duty_cycle_percent is not None:
            self.set_square_duty_cycle(
                channel=state.channel,
                duty_percent=state.square_duty_cycle_percent,
            )
        return self.set_output(channel=state.channel, enabled=state.output == "ON")

    def set_frequency(self, channel: int | None, value_hz: float) -> SourceStatus:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.set_frequency"]
        if source_cfg.settle_ms_after_set_frequency:
            required.append("source.status")
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.set_frequency", *required)
        with self._source_session() as source:
            status = source.set_frequency(
                channel,
                value_hz,
                ensure_fix_mode=source_cfg.ensure_fix_mode_on_set_frequency,
                check_errors=False,
            )
            if source_cfg.settle_ms_after_set_frequency:
                time.sleep(source_cfg.settle_ms_after_set_frequency / 1000.0)
                status = source.get_status(channel)
            if source_cfg.check_errors:
                source.assert_no_errors()
            return status

    def set_output(self, channel: int | None, enabled: bool) -> SourceStatus:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.output"]
        if enabled:
            required.append("source.status")
        self._require("source.output", *required)
        with self._source_session() as source:
            if enabled:
                status = source.get_status(channel)
                self._check_source_vpp(status.amplitude, field="source output amplitude / 信号源输出幅度")
            return source.set_output(channel, enabled, check_errors=source_cfg.check_errors)

    def set_function(self, channel: int | None, function: str) -> SourceStatus:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.set_function", "source.set_function")
        with self._source_session() as source:
            return source.set_function(channel, function, check_errors=source_cfg.check_errors)

    def set_square_duty_cycle(self, channel: int | None, duty_percent: float) -> SourceStatus:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.set_square_duty_cycle", "source.set_square_duty_cycle")
        with self._source_session() as source:
            return source.set_square_duty_cycle(channel, duty_percent, check_errors=source_cfg.check_errors)

    def set_amplitude_vpp(self, channel: int | None, value_vpp: float) -> SourceStatus:
        source_cfg = self._source_config()
        self._check_source_vpp(value_vpp, field="source amplitude / 信号源幅度")
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.set_amplitude_vpp", "source.set_amplitude_vpp")
        with self._source_session() as source:
            return source.set_amplitude_vpp(channel, value_vpp, check_errors=source_cfg.check_errors)


    def upload_arbitrary_waveform(
        self,
        *,
        channel: int | None,
        file_path: str,
        playback_frequency_hz: float,
        amplitude_vpp: float,
        offset_v: float = 0.0,
        sample_rate_hz: float | None = None,
        max_points: int = 16384,
        byte_order: str = "little",
        output_on: bool = False,
    ) -> SourceStatus:
        source_cfg = self._source_config()
        self._require_finite(
            playback_frequency_hz,
            field="arbitrary waveform playback frequency / 任意波播放频率",
        )
        self._require_finite(offset_v, field="arbitrary waveform offset / 任意波偏置")
        self._check_source_vpp(amplitude_vpp, field="arbitrary waveform amplitude / 任意波幅度")
        channel = source_cfg.default_channel if channel is None else channel
        waveform = load_arbitrary_waveform(
            file_path,
            sample_rate_hz=sample_rate_hz,
            max_points=max_points,
        )
        block = build_dg4000_dac14_binary_block(waveform, byte_order=byte_order)
        self._require("source.arbitrary_upload", "source.arbitrary_upload")
        with self._source_session() as source:
            return source.upload_dg4000_dac14_block(
                channel=channel,
                block=block,
                playback_frequency_hz=playback_frequency_hz,
                amplitude_vpp=amplitude_vpp,
                offset_v=offset_v,
                output_on=output_on,
                check_errors=source_cfg.check_errors,
            )

    def _check_source_vpp(self, value_vpp: float, *, field: str) -> None:
        self._require_finite(value_vpp, field=field)
        limit = self.config.safety_limits.max_source_vpp
        if limit is not None and value_vpp > limit:
            raise ConfigError(
                f"safety limit exceeded / 安全上限已超出: {field} {value_vpp:.12g} Vpp "
                f"> max_source_vpp {limit:.12g} Vpp"
            )

    @staticmethod
    def _require_finite(value: float, *, field: str) -> None:
        if not isfinite(value):
            raise ConfigError(
                f"finite value required / 必须为有限数: {field}"
            )

    def probe_arbitrary_queries(self, channel: int | None = None) -> list[ArbitraryQueryProbeResult]:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.arbitrary_probe", "source.arbitrary_probe")
        with self._source_session() as source:
            return source.probe_arbitrary_queries(channel)
