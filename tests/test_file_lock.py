from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from wavebench.services import file_lock
from wavebench.services.file_lock import (
    FileLock,
    FileLockBusy,
    FileLockUnavailable,
    probe_lock,
)
from wavebench.services.platform_io import atomic_write_json


def test_exclusive_lock_is_non_blocking_and_releases(tmp_path: Path) -> None:
    path = tmp_path / "state.lock"
    first = FileLock(path)
    second = FileLock(path)
    first.acquire()
    try:
        with pytest.raises(FileLockBusy):
            second.acquire()
        assert first.acquired is True
        assert second.acquired is False
    finally:
        first.release()
    second.acquire()
    second.release()


def test_shared_locks_can_coexist_but_exclusive_cannot(tmp_path: Path) -> None:
    path = tmp_path / "shared.lock"
    first = FileLock(path, mode="shared")
    second = FileLock(path, mode="shared")
    exclusive = FileLock(path)
    first.acquire()
    second.acquire()
    try:
        with pytest.raises(FileLockBusy):
            exclusive.acquire()
    finally:
        second.release()
        first.release()


def test_lock_contention_is_enforced_across_processes(tmp_path: Path) -> None:
    lock_path = tmp_path / "process.lock"
    ready_path = tmp_path / "ready"
    release_path = tmp_path / "release"
    script = """
from pathlib import Path
import sys
import time

from wavebench.services.file_lock import FileLock

lock = FileLock(Path(sys.argv[1]))
lock.acquire()
Path(sys.argv[2]).write_text("ready", encoding="utf-8")
while not Path(sys.argv[3]).exists():
    time.sleep(0.01)
lock.release()
"""
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path), str(ready_path), str(release_path)],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(f"lock helper exited early: {stdout}\n{stderr}")
            time.sleep(0.02)
        assert ready_path.exists(), "lock helper did not acquire its lock"
        with pytest.raises(FileLockBusy):
            FileLock(lock_path).acquire()
    finally:
        release_path.touch()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, f"lock helper failed: {stdout}\n{stderr}"


def test_probe_does_not_create_missing_lock_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.lock"
    assert probe_lock(path) is True
    assert not path.exists()


def test_backend_unavailable_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        file_lock,
        "backend_info",
        lambda: file_lock.LockBackendInfo(
            name="test", available=False, reason="injected-unavailable"
        ),
    )
    with pytest.raises(FileLockUnavailable, match="injected-unavailable"):
        FileLock(tmp_path / "state.lock").acquire()


def test_atomic_json_write_is_utf8_and_replaces_previous_value(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    atomic_write_json(path, {"message": "中文", "value": 2})
    atomic_write_json(path, {"message": "更新", "value": 3})
    assert path.read_text(encoding="utf-8") == '{"message": "更新", "value": 3}\n'
    assert not list(tmp_path.glob("*.tmp"))
