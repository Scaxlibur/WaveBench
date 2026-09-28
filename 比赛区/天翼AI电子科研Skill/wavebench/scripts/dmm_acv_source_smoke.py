from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path

from wavebench.config import load_config
from wavebench.errors import WaveBenchError
from wavebench.logging import CommandLogger
from wavebench.services.dmm_service import DmmService
from wavebench.services.source_service import SourceService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a conservative DG4202 -> DMM ACV smoke and save CSV."
    )
    parser.add_argument("--config", default="wavebench.toml", help="Path to WaveBench TOML config")
    parser.add_argument("--channel", type=int, default=2, help="DG4202 channel to drive")
    parser.add_argument("--targets-vpp", default="1.0,2.0", help="Comma-separated sine-wave Vpp targets")
    parser.add_argument("--samples", type=int, default=3, help="DMM samples per target")
    parser.add_argument("--frequency-hz", type=float, default=1000.0, help="Sine frequency in Hz")
    parser.add_argument("--settle-ms", type=int, default=400, help="Wait time after each source step")
    parser.add_argument("--output", default="data/analysis/dmm_acv_source_smoke.csv", help="CSV output path")
    return parser


def parse_targets(raw: str) -> list[float]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise ValueError("at least one Vpp target is required")
    targets = [float(item) for item in values]
    if any(value <= 0 for value in targets):
        raise ValueError("target Vpp values must be > 0")
    return targets



def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    targets = parse_targets(args.targets_vpp)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    config = load_config(args.config)
    source_service = SourceService(config=config, logger=CommandLogger())
    dmm_service = DmmService(config=config, logger=CommandLogger())

    original = source_service.snapshot_restorable_state(args.channel)
    rows: list[dict[str, float | int]] = []
    try:
        source_service.set_frequency(args.channel, args.frequency_hz)
        source_service.set_function(args.channel, "sin")
        source_service.set_amplitude_vpp(args.channel, targets[0])
        source_service.set_output(args.channel, True)
        for target_vpp in targets:
            source_status = source_service.set_amplitude_vpp(args.channel, target_vpp)
            time.sleep(args.settle_ms / 1000.0)
            theoretical_vrms = target_vpp / (2.0 * math.sqrt(2.0))
            for sample_index in range(1, args.samples + 1):
                reading = dmm_service.read("acv")
                rows.append({
                    "target_vpp": target_vpp,
                    "frequency_hz": args.frequency_hz,
                    "sample_index": sample_index,
                    "source_vpp": source_status.amplitude,
                    "theoretical_vrms": theoretical_vrms,
                    "dmm_vrms": reading.value,
                    "dmm_minus_theoretical_v": reading.value - theoretical_vrms,
                })
                print(
                    f"target={target_vpp:.6g}Vpp sample={sample_index} "
                    f"theoretical={theoretical_vrms:.9g}Vrms dmm={reading.value:.9g}Vrms"
                )
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"csv={output_path}")
        return 0
    finally:
        source_service.restore_restorable_state(original)
        final_status = source_service.status(args.channel)
        print(
            f"restored: CH{args.channel} output={final_status.output} "
            f"func={final_status.function} freq={final_status.frequency_hz}Hz "
            f"amp={final_status.amplitude}VPP offset={final_status.offset_v}V"
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WaveBenchError as exc:
        print(f"wavebench: {exc}")
        raise SystemExit(2)
