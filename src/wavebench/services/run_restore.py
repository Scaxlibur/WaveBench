from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from wavebench.errors import SessionHealthError, TransportIOError, error_envelope
from wavebench.services.run_plan import RunPlan
from wavebench.services.source_state import RestorableSourceState


class SourceRestoreService(Protocol):
    def snapshot_restorable_state(self, channel: int | None = None) -> RestorableSourceState: ...

    def restore_restorable_state(self, state: RestorableSourceState) -> object: ...


SourceServiceFactory = Callable[[], SourceRestoreService]


def snapshot_source_state(
    plan: RunPlan,
    *,
    source_service_factory: SourceServiceFactory,
) -> list[RestorableSourceState] | None:
    if not plan.restore.source_state:
        return None
    service = source_service_factory()
    channels = plan.restore.source_channels or (None,)
    return [service.snapshot_restorable_state(channel=channel) for channel in channels]


def restore_source_state(
    states: list[RestorableSourceState] | None,
    *,
    source_service_factory: SourceServiceFactory,
) -> dict[str, Any] | None:
    if not states:
        return None
    errors: list[dict[str, str | int | None]] = []
    service = source_service_factory()
    for state in states:
        try:
            service.restore_restorable_state(state)
        except (TransportIOError, SessionHealthError) as exc:
            # A gated/structured transport failure is authoritative.  Do not
            # issue further restore writes on an uncertain or poisoned epoch.
            envelope = error_envelope(
                exc,
                operation=f"restore.source.{state.channel}",
            )
            errors.append(
                {
                    "channel": state.channel,
                    "type": type(exc).__name__,
                    "message": envelope["message"],
                    "error": envelope,
                }
            )
            break
        except Exception as exc:  # pragma: no cover - defensive, covered through mocks
            envelope = error_envelope(
                exc,
                operation=f"restore.source.{state.channel}",
            )
            errors.append(
                {
                    "channel": state.channel,
                    "type": type(exc).__name__,
                    "message": envelope["message"],
                    "error": envelope,
                }
            )
    if errors:
        return {
            "type": "RestoreError",
            "message": "source state restore failed",
            "errors": errors,
        }
    return None
