from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from wavebench.logging import CommandLogger
from wavebench.services.resource_lease import ResourceLease
from wavebench.services.source_service import SourceService


def test_one_shot_source_service_passes_owned_lease_to_factory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WAVEBENCH_LEASE_DIR", str(tmp_path))
    descriptor = SimpleNamespace(
        driver_id="example.source",
        kind="source",
        capabilities=("source.idn",),
    )
    config = SimpleNamespace(
        source=SimpleNamespace(
            driver="example.source",
            resource="TCPIP::192.0.2.10::INSTR",
            access="read_write",
            check_errors=True,
        ),
        connection=SimpleNamespace(
            backend="lan",
            timeout_ms=1000,
            opc_timeout_ms=1000,
            read_retry_attempts=0,
            read_retry_delay_ms=0,
        ),
    )
    driver = SimpleNamespace(idn=lambda: "EXAMPLE,SG", close=lambda: None)
    opened = SimpleNamespace(descriptor=descriptor, transport=None, driver=driver)
    service = SourceService(
        config=config,
        logger=CommandLogger(),
        descriptor=descriptor,
    )

    with patch(
        "wavebench.services.source_service.open_instrument_driver",
        return_value=opened,
    ) as factory:
        assert service.idn() == "EXAMPLE,SG"

    lease = factory.call_args.kwargs["lease"]
    assert isinstance(lease, ResourceLease)
    assert lease.resource == "tcpip::192.0.2.10::instr"
    assert not lease.acquired
