"""Run pytest with forwarded output and periodic keepalive lines for idle-limited terminals."""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run pytest while printing a periodic keepalive for terminals with idle timeouts."
    )
    parser.add_argument(
        "--heartbeat-s",
        type=float,
        default=5.0,
        help="Seconds between keepalive lines while pytest is still running (default: 5).",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to pytest; place them after --, for example: -- -q",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.heartbeat_s <= 0:
        raise SystemExit("--heartbeat-s must be > 0")
    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args.pop(0)
    command = [sys.executable, "-m", "pytest", *pytest_args]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def forward_output() -> None:
        assert process.stdout is not None
        for character in iter(lambda: process.stdout.read(1), ""):
            sys.stdout.write(character)
            sys.stdout.flush()

    forwarder = threading.Thread(target=forward_output, daemon=True)
    forwarder.start()
    started = time.monotonic()
    while True:
        try:
            returncode = process.wait(timeout=args.heartbeat_s)
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            print(f"\n[pytest-progress] elapsed={elapsed:.1f}s status=running", flush=True)
            continue
        forwarder.join()
        return returncode


if __name__ == "__main__":
    raise SystemExit(main())
