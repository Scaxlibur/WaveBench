import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    ScopeConfig,
    SourceConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.drivers.dg4202 import SourceStatus
from wavebench.errors import InstrumentError
from wavebench.logging import CommandLogger
from wavebench.services.sweep_service import SweepService


def make_config(tmp: str) -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig(backend='lan', resource='TCPIP::scope::INSTR', timeout_ms=1000, opc_timeout_ms=1000),
        scope=ScopeConfig(driver='rtm2032', model_hint=None, default_channel=1, reset_before_run=False, check_errors=True),
        autoscale=AutoscaleConfig(wait_opc=True, check_errors=True),
        waveform=WaveformConfig(format='real', byte_order='lsbf', points='DEF'),
        output=OutputConfig(
            directory=Path(tmp) / 'data' / 'raw',
            package_naming='timestamp_label',
            save_csv=False,
            save_npy=True,
            save_json=True,
            save_commands_log=True,
            save_screenshot=False,
        ),
        source_path=Path(tmp) / 'wavebench.toml',
        source=SourceConfig(
            driver='dg4202',
            resource='TCPIP::source::INSTR',
            default_channel=2,
            check_errors=True,
            ensure_fix_mode_on_set_frequency=True,
            settle_ms_after_set_frequency=0,
        ),
    )


class SweepServiceTests(unittest.TestCase):
    def test_discrete_sweep_rejects_source_output_off(self):
        with TemporaryDirectory() as tmp:
            config = make_config(tmp)
            service = SweepService(config=config, logger=CommandLogger())
            off_status = SourceStatus(
                channel=2,
                output='OFF',
                function='SIN',
                frequency_hz=1000.0,
                amplitude=5.0,
                amplitude_unit='VPP',
                offset_v=0.0,
                phase_deg=0.0,
                frequency_mode='FIX',
                sweep_enabled='OFF',
                apply_raw=None,
            )
            with patch('wavebench.services.sweep_service.SourceService') as source_cls:
                source_cls.return_value.set_frequency.return_value = off_status
                with self.assertRaisesRegex(InstrumentError, 'output is OFF'):
                    service.run_discrete(
                        frequencies_hz=[1000.0],
                        source_channel=2,
                        scope_channel=1,
                        target_cycles=10.0,
                        frequency_tolerance=0.05,
                        label='test',
                        save_csv=False,
                        save_npy=True,
                    )

    def test_discrete_sweep_sets_optional_source_shape_before_frequency(self):
        with TemporaryDirectory() as tmp:
            config = make_config(tmp)
            service = SweepService(config=config, logger=CommandLogger())
            on_status = SourceStatus(
                channel=2,
                output='ON',
                function='SIN',
                frequency_hz=1000.0,
                amplitude=3.3,
                amplitude_unit='VPP',
                offset_v=0.0,
                phase_deg=0.0,
                frequency_mode='FIX',
                sweep_enabled='OFF',
                apply_raw=None,
            )
            with patch('wavebench.services.sweep_service.SourceService') as source_cls,                  patch('wavebench.services.sweep_service.ScopeService') as scope_cls:
                source = source_cls.return_value
                source.set_function.return_value = on_status
                source.set_amplitude_vpp.return_value = on_status
                source.set_frequency.return_value = on_status
                metadata = Path(tmp) / 'metadata.json'
                metadata.write_text('{"waveform":{"summary":{"frequency_estimate_hz":1000.0,"frequency_in_tolerance":true,"quality_warnings":[]}}}', encoding='utf-8')
                capture = type('Capture', (), {'metadata_path': metadata, 'package_dir': Path(tmp) / 'pkg'})()
                scope_cls.return_value.capture_waveform.return_value = capture

                service.run_discrete(
                    frequencies_hz=[1000.0],
                    source_channel=2,
                    scope_channel=1,
                    target_cycles=10.0,
                    frequency_tolerance=0.05,
                    label='test',
                    save_csv=False,
                    save_npy=True,
                    source_function='sin',
                    source_vpp=3.3,
                )

                source.set_function.assert_called_once_with(channel=2, function='sin')
                source.set_amplitude_vpp.assert_called_once_with(channel=2, value_vpp=3.3)
                source.set_frequency.assert_called_once_with(channel=2, value_hz=1000.0)

    def test_discrete_sweep_restore_source_state_in_finally(self):
        with TemporaryDirectory() as tmp:
            config = make_config(tmp)
            service = SweepService(config=config, logger=CommandLogger())
            on_status = SourceStatus(
                channel=2,
                output='ON',
                function='SIN',
                frequency_hz=1000.0,
                amplitude=5.0,
                amplitude_unit='VPP',
                offset_v=0.0,
                phase_deg=0.0,
                frequency_mode='FIX',
                sweep_enabled='OFF',
                apply_raw=None,
            )
            with patch('wavebench.services.sweep_service.SourceService') as source_cls,                  patch('wavebench.services.sweep_service.ScopeService') as scope_cls:
                source = source_cls.return_value
                source.snapshot_restorable_state.return_value = 'ORIGINAL'
                source.set_frequency.return_value = on_status
                scope_cls.return_value.capture_waveform.side_effect = RuntimeError('capture failed')

                with self.assertRaisesRegex(RuntimeError, 'capture failed'):
                    service.run_discrete(
                        frequencies_hz=[1000.0],
                        source_channel=2,
                        scope_channel=1,
                        target_cycles=10.0,
                        frequency_tolerance=0.05,
                        label='test',
                        save_csv=False,
                        save_npy=True,
                        restore_source_state=True,
                    )

                source.snapshot_restorable_state.assert_called_once_with(channel=2)
                source.restore_restorable_state.assert_called_once_with('ORIGINAL')

    def test_discrete_sweep_does_not_snapshot_by_default(self):
        with TemporaryDirectory() as tmp:
            config = make_config(tmp)
            service = SweepService(config=config, logger=CommandLogger())
            on_status = SourceStatus(
                channel=2,
                output='ON',
                function='SIN',
                frequency_hz=1000.0,
                amplitude=5.0,
                amplitude_unit='VPP',
                offset_v=0.0,
                phase_deg=0.0,
                frequency_mode='FIX',
                sweep_enabled='OFF',
                apply_raw=None,
            )
            with patch('wavebench.services.sweep_service.SourceService') as source_cls,                  patch('wavebench.services.sweep_service.ScopeService') as scope_cls:
                source = source_cls.return_value
                source.set_frequency.return_value = on_status
                metadata = Path(tmp) / 'metadata.json'
                metadata.write_text('{"waveform":{"summary":{"frequency_estimate_hz":1000.0,"frequency_in_tolerance":true,"quality_warnings":[]}}}', encoding='utf-8')
                capture = type('Capture', (), {'metadata_path': metadata, 'package_dir': Path(tmp) / 'pkg'})()
                scope_cls.return_value.capture_waveform.return_value = capture

                service.run_discrete(
                    frequencies_hz=[1000.0],
                    source_channel=2,
                    scope_channel=1,
                    target_cycles=10.0,
                    frequency_tolerance=0.05,
                    label='test',
                    save_csv=False,
                    save_npy=True,
                )

                source.snapshot_restorable_state.assert_not_called()
                source.restore_restorable_state.assert_not_called()


if __name__ == '__main__':
    unittest.main()
