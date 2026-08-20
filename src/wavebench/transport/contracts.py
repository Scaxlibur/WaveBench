from __future__ import annotations

from enum import StrEnum


class ReplayPolicy(StrEnum):
    """Caller-selected command replay policy for one transport exchange."""

    SAFE_TO_REPLAY = "safe_to_replay"
    NO_REPLAY = "no_replay"
    READ_CONTINUATION_ONLY = "read_continuation_only"


class TransportPhase(StrEnum):
    BEFORE_SEND = "before_send"
    SENDING = "sending"
    READING = "reading"
    PARSING = "parsing"


class CommandTransmission(StrEnum):
    NOT_SENT = "not_sent"
    SENT = "sent"
    UNKNOWN = "unknown"


class ResponseProgress(StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    COMPLETE = "complete"
    UNKNOWN = "unknown"


class Synchronization(StrEnum):
    PROVEN = "proven"
    UNPROVEN = "unproven"
    LOST = "lost"


__all__ = [
    "CommandTransmission",
    "ReplayPolicy",
    "ResponseProgress",
    "Synchronization",
    "TransportPhase",
]
