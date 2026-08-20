"""Small core guard for Service aliases of an instrument session state."""

from __future__ import annotations

from wavebench.errors import ConfigError
from wavebench.transport.session import SessionHealth


class SessionStateAliasMixin:
    """Keep a Service's session-state reference bound to one connection epoch.

    The public attribute is retained for compatibility with existing Service
    constructors, but once a factory binds a real state object it cannot be
    replaced by ordinary caller code.  Reopening is an explicit lifecycle
    operation: the old state must already be closed, after which the helper
    clears the old aliases before a new factory call can bind a fresh epoch.
    """

    _session_state_bound = False

    def __setattr__(self, name: str, value: object) -> None:
        if name == "session_state" and getattr(self, "_session_state_bound", False):
            raise AttributeError("session_state is read-only after session binding")
        object.__setattr__(self, name, value)
        if name == "session_state" and value is not None:
            object.__setattr__(self, "_session_state_bound", True)

    def _prepare_session_open(self, instrument_kind: str) -> None:
        """Reject an active duplicate open and clear a known closed epoch."""

        state = getattr(self, "session_state", None)
        session = getattr(self, "session", None)
        transport = getattr(self, "transport", None)
        if state is None and session is None and transport is None:
            return
        if state is None or state.health is not SessionHealth.CLOSED:
            raise ConfigError(
                f"{instrument_kind} session is already open; close it before reconnecting"
            )
        # GuardedAuditedTransport marks the state closed before backend close,
        # so a closed state is the only safe proof that the old epoch is gone.
        object.__setattr__(self, "session", None)
        object.__setattr__(self, "transport", None)
        object.__setattr__(self, "session_state", None)
        object.__setattr__(self, "_session_state_bound", False)


__all__ = ["SessionStateAliasMixin"]
