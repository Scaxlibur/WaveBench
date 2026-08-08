from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wavebench.errors import ConfigError, ResourceBusyError
from wavebench.services import file_lock
from wavebench.services import resource_lease as lease_module
from wavebench.services.resource_lease import (
    ResourceLease,
    ResourceLeaseManager,
    normalize_resource,
    resource_fingerprint,
)


def test_resource_identity_normalizes_visa_case_and_serial_paths() -> None:
    assert normalize_resource(" TCPIP::Bench::INSTR ") == "tcpip::bench::instr"
    assert normalize_resource("/dev/ttyUSB0") == "/dev/ttyUSB0"
    assert normalize_resource("COM3") == "com3"
    assert normalize_resource(r"\\.\COM3") == "com3"
    assert normalize_resource("COM003") == "com3"
    assert resource_fingerprint("TCPIP::Bench::INSTR") == resource_fingerprint(
        "tcpip::bench::instr"
    )


def test_windows_default_lease_directory_uses_local_app_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lease_module, "_is_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\\Users\\tester\\AppData\\Local")

    directory = lease_module.default_lease_directory()

    assert str(directory).endswith("WaveBench/resource-leases-v1")
    assert "AppData" in str(directory)


def test_lease_busy_error_and_private_metadata(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(tmp_path)
    first = manager.acquire("TCPIP::192.0.2.10::INSTR", operation="run")
    try:
        with pytest.raises(ResourceBusyError, match="fingerprint") as raised:
            manager.acquire("tcpip::192.0.2.10::instr", operation="other")
        assert raised.value.code == "resource_busy"
        assert raised.value.exit_code == 7
        if os.name != "nt":
            assert first.lock_path.stat().st_mode & 0o777 == 0o600
        payload = json.loads(first.metadata_path.read_text(encoding="utf-8"))
        assert payload["schema"] == "wavebench.resource_lease.v1"
        assert payload["lease_id"]
        assert "192.0.2.10" not in first.metadata_path.read_text(encoding="utf-8")
        assert first.status()["held"] is True
    finally:
        first.release()


def test_release_and_stale_cleanup_keep_lock_file(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(tmp_path)
    lease = manager.acquire("TCPIP::bench::INSTR")
    path = lease.lock_path
    assert lease.clear_stale_metadata() is False
    lease.release()

    assert path.exists()
    assert lease.status()["held"] is False
    lease.metadata_path.write_text(
        json.dumps({"schema": "wavebench.resource_lease.v1", "lease_id": "stale"}),
        encoding="utf-8",
    )
    assert lease.status()["stale_metadata"] is True
    assert lease.clear_stale_metadata() is True
    assert path.exists()
    assert not lease.metadata_path.exists()


def test_acquire_many_releases_partial_batch_on_busy_resource(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(tmp_path)
    held = manager.acquire("TCPIP::held::INSTR")
    try:
        with pytest.raises(ResourceBusyError):
            manager.acquire_many(["TCPIP::free::INSTR", "TCPIP::held::INSTR"])
        free = manager.acquire("TCPIP::free::INSTR")
        free.release()
    finally:
        held.release()


def test_acquire_many_deduplicates_windows_com_aliases(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(tmp_path)

    leases = manager.acquire_many(["COM3", r"\\.\COM3"])

    try:
        assert len(leases) == 1
        assert leases[0].resource == "com3"
    finally:
        for lease in leases:
            lease.release()


def test_hold_many_releases_all_leases(tmp_path: Path) -> None:
    manager = ResourceLeaseManager(tmp_path)
    with manager.hold_many(["TCPIP::a::INSTR", "TCPIP::b::INSTR"]) as leases:
        assert len(leases) == 2
        assert all(lease.acquired for lease in leases)
    assert all(
        not ResourceLease(resource, directory=tmp_path).status()["held"]
        for resource in ("TCPIP::a::INSTR", "TCPIP::b::INSTR")
    )


def test_unavailable_lock_backend_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        file_lock,
        "backend_info",
        lambda: file_lock.LockBackendInfo(
            name="test", available=False, reason="injected-unavailable"
        ),
    )
    with pytest.raises(ConfigError, match="injected-unavailable"):
        ResourceLease("TCPIP::bench::INSTR", directory=tmp_path).acquire()
