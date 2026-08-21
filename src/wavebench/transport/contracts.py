from __future__ import annotations

from dataclasses import dataclass
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


class BinaryResponseFraming(StrEnum):
    """Framing modes whose response boundary can be proven by the transport."""

    DEFINITE_BLOCK = "definite_block"
    MESSAGE = "message"


@dataclass(frozen=True, slots=True)
class BinaryQueryResult:
    """A complete binary response with explicit framing and byte accounting."""

    data: bytes
    framing: BinaryResponseFraming
    declared_length: int | None
    framing_header_bytes: int
    consumed_bytes: int
    transport_trailing_bytes: bytes = b""
    synchronization: Synchronization = Synchronization.PROVEN

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise TypeError("binary query data must be bytes")
        object.__setattr__(self, "framing", BinaryResponseFraming(self.framing))
        object.__setattr__(self, "synchronization", Synchronization(self.synchronization))
        if self.synchronization is not Synchronization.PROVEN:
            raise ValueError("successful binary responses must prove synchronization")
        if not isinstance(self.transport_trailing_bytes, bytes):
            raise TypeError("binary transport trailing data must be bytes")
        for label, value in (
            ("framing_header_bytes", self.framing_header_bytes),
            ("consumed_bytes", self.consumed_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")

        if self.framing is BinaryResponseFraming.DEFINITE_BLOCK:
            if (
                isinstance(self.declared_length, bool)
                or not isinstance(self.declared_length, int)
                or self.declared_length < 0
            ):
                raise ValueError("definite-block responses require a non-negative declared length")
            if self.declared_length != len(self.data):
                raise ValueError("definite-block declared length must equal payload length")
            if not 3 <= self.framing_header_bytes <= 11:
                raise ValueError("definite-block framing header length must be in 3..11")
            length_digits = self.framing_header_bytes - 2
            if self.declared_length >= 10**length_digits:
                raise ValueError("declared length cannot fit the observed framing header")
            expected = (
                self.framing_header_bytes
                + len(self.data)
                + len(self.transport_trailing_bytes)
            )
            if self.consumed_bytes != expected:
                raise ValueError("definite-block consumed byte accounting is inconsistent")
            return

        if self.declared_length is not None:
            raise ValueError("message-framed responses cannot declare a payload length")
        if self.framing_header_bytes != 0:
            raise ValueError("message-framed responses cannot have a framing header")
        if self.transport_trailing_bytes != b"":
            raise ValueError("message-framed responses cannot have transport trailing bytes")
        if self.consumed_bytes != len(self.data):
            raise ValueError("message-framed consumed bytes must equal payload length")


__all__ = [
    "BinaryQueryResult",
    "BinaryResponseFraming",
    "CommandTransmission",
    "ReplayPolicy",
    "ResponseProgress",
    "Synchronization",
    "TransportPhase",
]
