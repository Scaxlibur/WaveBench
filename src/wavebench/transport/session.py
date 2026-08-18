from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from typing import Any, Iterable
from uuid import uuid4


class SessionHealth(StrEnum):
    HEALTHY = "healthy"
    UNCERTAIN = "uncertain"
    POISONED = "poisoned"
    CLOSED = "closed"


class SessionPurpose(StrEnum):
    NORMAL = "normal"
    RECOVERY = "recovery"
    VERIFICATION = "verification"
    LIFECYCLE = "lifecycle"


_HEALTH_RANK = {
    SessionHealth.HEALTHY: 0,
    SessionHealth.UNCERTAIN: 1,
    SessionHealth.POISONED: 2,
    SessionHealth.CLOSED: 3,
}


@dataclass
class InstrumentSessionState:
    """Health and verified-field state bound to one concrete connection epoch."""

    epoch_id: str = field(default_factory=lambda: uuid4().hex)
    health: SessionHealth = SessionHealth.HEALTHY
    verified_fields: set[str] = field(default_factory=set)
    last_transition: dict[str, Any] | None = None
    transaction_lock: RLock = field(default_factory=RLock, repr=False)

    def snapshot(self) -> dict[str, object]:
        with self.transaction_lock:
            return {
                "epoch_id": self.epoch_id,
                "health": self.health.value,
                "verified_fields": sorted(self.verified_fields),
                "last_transition": dict(self.last_transition)
                if self.last_transition is not None
                else None,
            }

    def degrade(self, health: SessionHealth, *, reason: str) -> None:
        """Move to an equally or more conservative health state."""

        with self.transaction_lock:
            if _HEALTH_RANK[health] < _HEALTH_RANK[self.health]:
                raise ValueError(
                    f"session health cannot improve through degrade: {self.health} -> {health}"
                )
            previous = self.health
            self.health = health
            if health is not SessionHealth.HEALTHY:
                self.verified_fields.clear()
            self.last_transition = {
                "from": previous.value,
                "to": health.value,
                "reason": reason,
            }

    def close(self) -> None:
        with self.transaction_lock:
            if self.health is SessionHealth.CLOSED:
                return
            self.degrade(SessionHealth.CLOSED, reason="connection_closed")

    def _complete_verification(self, fields: Iterable[str], *, reason: str) -> None:
        """Core coordinator hook; callers and Services must not invoke this directly."""

        normalized = {field for field in fields if field and field.strip() == field}
        with self.transaction_lock:
            if self.health not in {SessionHealth.HEALTHY, SessionHealth.UNCERTAIN}:
                raise ValueError(f"cannot verify a {self.health.value} session")
            previous = self.health
            self.health = SessionHealth.HEALTHY
            self.verified_fields.update(normalized)
            self.last_transition = {
                "from": previous.value,
                "to": SessionHealth.HEALTHY.value,
                "reason": reason,
            }


__all__ = ["InstrumentSessionState", "SessionHealth", "SessionPurpose"]
