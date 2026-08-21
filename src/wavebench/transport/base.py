from __future__ import annotations

from typing import Protocol

from .contracts import BinaryQueryResult, BinaryResponseFraming, ReplayPolicy

class InstrumentTransport(Protocol):
    resource: str
    def record_event(self, direction: str, text: str) -> None: ...
    def write(self, command: str) -> None: ...
    def write_bytes(self, command: bytes) -> None: ...
    def query(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> str: ...
    def query_float_list(
        self,
        command: str,
        *,
        timeout_ms: int | None = None,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> list[float]: ...
    def query_bin_block(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> bytes: ...
    def query_binary(
        self,
        command: str,
        *,
        framing: BinaryResponseFraming,
        max_bytes: int,
        timeout_ms: int | None = None,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> BinaryQueryResult: ...
    def query_opc(
        self,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> str: ...
    def close(self) -> None: ...
