from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from wavebench.cli import main


def test_lock_status_is_read_only_and_machine_readable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WAVEBENCH_LEASE_DIR", str(tmp_path))
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main(["--json", "lock", "status", "TCPIP::192.0.2.10::INSTR"])

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["schema"] == "wavebench.cli.result.v1"
    assert payload["result"]["held"] is False
    assert payload["result"]["stale_metadata"] is False
