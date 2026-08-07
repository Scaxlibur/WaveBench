from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from wavebench.errors import ResourceBusyError
from wavebench.logging import CommandLogger
from wavebench.services.resource_lease import ResourceLeaseManager
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunService

from test_run_service import make_config, write_plan


def _plan(tmp: str):
    return load_run_plan(
        write_plan(
            tmp,
            """
[[steps]]
kind = "source.status"
channel = 1

[[steps]]
kind = "scope.capture"
channel = 1
""",
        )
    )


def test_run_acquires_all_leases_before_opening_any_session() -> None:
    with TemporaryDirectory() as tmp:
        config = make_config(tmp)
        manager = ResourceLeaseManager(Path(tmp) / "locks")
        held = manager.acquire(config.source.resource or "")
        try:
            service = RunService(config=config, logger=CommandLogger(), lease_manager=manager)
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.SourceService"
            ) as source_cls:
                with pytest.raises(ResourceBusyError, match="resource lease is busy"):
                    with service._run_instrument_services(_plan(tmp)):
                        raise AssertionError("lease conflict should prevent session opening")
                scope_cls.return_value.open_session.assert_not_called()
                source_cls.return_value.open_session.assert_not_called()
        finally:
            held.release()


def test_run_releases_borrowed_leases_after_sessions_close() -> None:
    with TemporaryDirectory() as tmp:
        config = make_config(tmp)
        manager = ResourceLeaseManager(Path(tmp) / "locks")
        service = RunService(config=config, logger=CommandLogger(), lease_manager=manager)
        with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
            "wavebench.services.run_service.SourceService"
        ) as source_cls:
            scope_session = Mock()
            source_session = Mock()
            scope_cls.return_value.open_session.return_value = scope_session
            source_cls.return_value.open_session.return_value = source_session
            with service._run_instrument_services(_plan(tmp)):
                pass
            scope_session.close.assert_called_once()
            source_session.close.assert_called_once()

        reacquired = manager.acquire_many(
            [config.connection.resource, config.source.resource or ""]
        )
        for lease in reacquired:
            lease.release()
