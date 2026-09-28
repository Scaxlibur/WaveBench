from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from wavebench.config import WaveBenchConfig
from wavebench.errors import InstrumentError
from wavebench.logging import CommandLogger
from wavebench.services.scope_service import ScopeService
from wavebench.services.source_service import SourceService


def parse_frequency_list(text: str) -> list[float]:
    values: list[float] = []
    for part in text.split(','):
        part = part.strip()
        if not part:
            continue
        values.append(float(part))
    if not values:
        raise ValueError('at least one frequency is required')
    if any(value <= 0 for value in values):
        raise ValueError('frequencies must be > 0')
    return values


@dataclass(frozen=True)
class DiscreteSweepRow:
    index: int
    set_frequency_hz: float
    measured_frequency_hz: float | None
    frequency_error_ratio: float | None
    frequency_in_tolerance: bool | None
    voltage_vpp_v: float | None
    voltage_rms_v: float | None
    estimated_cycles: float | None
    quality_warnings: list[str]
    package: str

    def as_csv_row(self) -> dict[str, object]:
        return {
            'index': self.index,
            'set_frequency_hz': self.set_frequency_hz,
            'measured_frequency_hz': self.measured_frequency_hz,
            'frequency_error_ratio': self.frequency_error_ratio,
            'frequency_in_tolerance': self.frequency_in_tolerance,
            'voltage_vpp_v': self.voltage_vpp_v,
            'voltage_rms_v': self.voltage_rms_v,
            'estimated_cycles': self.estimated_cycles,
            'quality_warnings': ';'.join(self.quality_warnings),
            'package': self.package,
        }


@dataclass(frozen=True)
class DiscreteSweepResult:
    summary_path: Path
    rows: list[DiscreteSweepRow]


@dataclass
class SweepService:
    config: WaveBenchConfig
    logger: CommandLogger

    def run_discrete(
        self,
        *,
        frequencies_hz: list[float],
        source_channel: int | None,
        scope_channel: int | None,
        target_cycles: float,
        frequency_tolerance: float | None,
        label: str,
        save_csv: bool,
        save_npy: bool,
        source_function: str | None = None,
        source_vpp: float | None = None,
        restore_source_state: bool = False,
    ) -> DiscreteSweepResult:
        source_service = SourceService(config=self.config, logger=self.logger)
        scope_channel = self.config.scope.default_channel if scope_channel is None else scope_channel
        source_channel = (
            self.config.source.default_channel
            if source_channel is None and self.config.source is not None
            else source_channel
        )
        if source_channel is None:
            source_channel = 1

        original_state = None
        if restore_source_state:
            original_state = source_service.snapshot_restorable_state(channel=source_channel)

        try:
            if source_function is not None:
                source_status = source_service.set_function(channel=source_channel, function=source_function)
                if source_status.output.strip().upper() != 'ON':
                    raise InstrumentError(
                        f'source CH{source_channel} output is {source_status.output}; '
                        'turn it on with `wavebench source output --channel <n> on` before running sweep'
                    )
            if source_vpp is not None:
                source_status = source_service.set_amplitude_vpp(channel=source_channel, value_vpp=source_vpp)
                if source_status.output.strip().upper() != 'ON':
                    raise InstrumentError(
                        f'source CH{source_channel} output is {source_status.output}; '
                        'turn it on with `wavebench source output --channel <n> on` before running sweep'
                    )

            summary_dir = self.config.output.directory.parent / 'analysis'
            summary_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            summary_path = summary_dir / f'{stamp}_{label}_summary.csv'
            rows: list[DiscreteSweepRow] = []

            for index, frequency_hz in enumerate(frequencies_hz):
                source_status = source_service.set_frequency(channel=source_channel, value_hz=frequency_hz)
                if source_status.output.strip().upper() != 'ON':
                    raise InstrumentError(
                        f'source CH{source_channel} output is {source_status.output}; '
                        'turn it on with `wavebench source output --channel <n> on` before running sweep'
                    )
                point_label = f'{label}_{index:02d}_{int(frequency_hz)}hz'
                point_config = self.config.with_waveform_overrides(
                    time_range_s=target_cycles / frequency_hz,
                    expected_frequency_hz=frequency_hz,
                    frequency_tolerance_ratio=frequency_tolerance,
                    target_cycles=target_cycles,
                    window_frequency_hz=frequency_hz,
                ).with_output_overrides(save_csv=save_csv, save_npy=save_npy)
                capture = ScopeService(config=point_config, logger=CommandLogger()).capture_waveform(
                    channel=scope_channel,
                    label=point_label,
                )
                metadata: dict[str, Any] = json.loads(capture.metadata_path.read_text(encoding='utf-8'))
                summary = metadata['waveform']['summary']
                rows.append(
                    DiscreteSweepRow(
                        index=index,
                        set_frequency_hz=frequency_hz,
                        measured_frequency_hz=summary.get('frequency_estimate_hz'),
                        frequency_error_ratio=summary.get('frequency_error_ratio'),
                        frequency_in_tolerance=summary.get('frequency_in_tolerance'),
                        voltage_vpp_v=summary.get('voltage_vpp_v'),
                        voltage_rms_v=summary.get('voltage_rms_v'),
                        estimated_cycles=summary.get('estimated_cycles'),
                        quality_warnings=list(summary.get('quality_warnings', [])),
                        package=str(capture.package_dir),
                    )
                )

            with summary_path.open('w', newline='', encoding='utf-8') as file:
                fieldnames = list(rows[0].as_csv_row().keys()) if rows else ['index']
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(row.as_csv_row())
            return DiscreteSweepResult(summary_path=summary_path, rows=rows)
        finally:
            if original_state is not None:
                source_service.restore_restorable_state(original_state)
