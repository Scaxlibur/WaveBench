from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

from wavebench.config import load_config
from wavebench.errors import WaveBenchError
from wavebench.logging import CommandLogger
from wavebench.services.dmm_service import DmmService
from wavebench.services.power_service import PowerService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a conservative DP800 -> DMM DCV staircase smoke and save CSV."
    )
    parser.add_argument("--config", default="wavebench.toml", help="Path to WaveBench TOML config")
    parser.add_argument("--channel", type=int, default=1, help="DP800 channel to drive")
    parser.add_argument(
        "--targets",
        default="0,1,2,3.3",
        help="Comma-separated voltage targets in volts, e.g. 0,1,2,3.3",
    )
    parser.add_argument("--samples", type=int, default=3, help="DMM samples per target")
    parser.add_argument("--current-limit", type=float, default=0.02, help="Current limit in amperes")
    parser.add_argument("--settle-ms", type=int, default=400, help="Wait time after each power step")
    parser.add_argument(
        "--output",
        default="data/analysis/dmm_dcv_staircase_smoke.csv",
        help="CSV output path",
    )
    return parser


def parse_targets(raw: str) -> list[float]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise ValueError("at least one target voltage is required")
    targets = [float(item) for item in values]
    if any(value < 0 for value in targets):
        raise ValueError("target voltages must be >= 0")
    return targets


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    targets = parse_targets(args.targets)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    config = load_config(args.config)
    power_service = PowerService(config=config, logger=CommandLogger())
    dmm_service = DmmService(config=config, logger=CommandLogger())

    rows: list[dict[str, float | int]] = []
    try:
        power_service.set_voltage_current_limit(args.channel, 0.0, args.current_limit)
        power_service.set_output(args.channel, True)
        for target in targets:
            power_status = power_service.set_voltage_current_limit(args.channel, target, args.current_limit)
            time.sleep(args.settle_ms / 1000.0)
            for sample_index in range(1, args.samples + 1):
                reading = dmm_service.read("dcv")
                rows.append(
                    {
                        "target_v": target,
                        "sample_index": sample_index,
                        "power_set_v": power_status.set_voltage_v or 0.0,
                        "power_measured_v": power_status.measured_voltage_v or 0.0,
                        "dmm_v": reading.value,
                        "dmm_minus_target_v": reading.value - target,
                        "dmm_minus_power_v": reading.value - (power_status.measured_voltage_v or 0.0),
                    }
                )
                print(
                    f"target={target:.6g}V sample={sample_index} "
                    f"power={power_status.measured_voltage_v:.9g}V dmm={reading.value:.9g}V"
                )
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"csv={output_path}")
        return 0
    finally:
        try:
            power_service.set_voltage_current_limit(args.channel, 0.0, args.current_limit)
        finally:
            power_service.set_output(args.channel, False)
            final_status = power_service.status(args.channel)
            print(
                f"restored: CH{args.channel} output={final_status.output} "
                f"set={final_status.set_voltage_v}V/{final_status.set_current_a}A "
                f"measured={final_status.measured_voltage_v}V/{final_status.measured_current_a}A"
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WaveBenchError as exc:
        print(f"wavebench: {exc}")
        raise SystemExit(2)
