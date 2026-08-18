from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import time
from typing import Any

from wavebench.config import ConnectionConfig
from wavebench.errors import ConnectionError, TransportIOError
from wavebench.logging import CommandLogger

from .contracts import (
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)


def _open_rsinstrument_session(
    rs_instrument_cls: Any,
    resource: str,
    *,
    select_visa: str | None = None,
) -> Any:
    if select_visa is not None:
        options = f"SelectVisa={select_visa}"
        if select_visa == "socketio":
            options += ",AddTermCharToWriteBinBlock=True,DataChunkSize=512"
        return rs_instrument_cls(
            resource,
            True,
            False,
            options,
        )
    try:
        return rs_instrument_cls(resource, True, False)
    except Exception as exc:
        message = str(exc).lower()
        if "rsvisa" not in message and "visa implementation" not in message:
            raise
        return rs_instrument_cls(resource, True, False, "SelectVisa=pyvisa-py")


@dataclass
class RsInstrumentTransport:
    resource: str
    session: Any
    logger: CommandLogger
    select_visa: str | None = None
    read_retry_attempts: int = 1
    read_retry_delay_ms: int = 200

    @classmethod
    def open(
        cls,
        config: ConnectionConfig,
        logger: CommandLogger | None = None,
        *,
        select_visa: str | None = None,
    ) -> "RsInstrumentTransport":
        try:
            from RsInstrument import RsInstrument
        except ImportError as exc:
            raise ConnectionError("RsInstrument is not installed. Run: python -m pip install -e .") from exc
        logger = logger or CommandLogger()
        try:
            session = _open_rsinstrument_session(
                RsInstrument,
                config.resource,
                select_visa=select_visa,
            )
            session.visa_timeout = config.timeout_ms
            session.opc_timeout = config.opc_timeout_ms
            session.instrument_status_checking = False
        except Exception as exc:
            raise ConnectionError(f"failed to open instrument {config.resource}: {exc}") from exc
        return cls(
            resource=config.resource,
            session=session,
            logger=logger,
            select_visa=select_visa,
            read_retry_attempts=config.read_retry_attempts,
            read_retry_delay_ms=config.read_retry_delay_ms,
        )

    def record_event(self, direction: str, text: str) -> None:
        self.logger.record(direction, text)

    def write(self, command: str) -> None:
        self.logger.record("write", command)
        try:
            self.session.write_str(command)
        except Exception as exc:
            raise self._write_error("write", exc) from exc

    def query(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> str:
        replay = ReplayPolicy(replay)
        self._reject_continuation("query", replay)
        self.logger.record("query", command)
        attempts = 0

        def read_once() -> str:
            nonlocal attempts
            attempts += 1
            return self.session.query_str(command).strip()

        try:
            response = self._read_with_policy("query", command, replay, read_once)
        except Exception as exc:
            if isinstance(exc, TransportIOError):
                raise
            raise self._query_error("query", replay, attempts, exc) from exc
        self.logger.record("response", response)
        return response

    def query_float_list(
        self,
        command: str,
        *,
        timeout_ms: int | None = None,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> list[float]:
        replay = ReplayPolicy(replay)
        self._reject_continuation("query_float_list", replay)
        self.logger.record("query", command)
        started = time.perf_counter()
        progress: dict[str, int | None] | None = None
        attempts = 0

        def read_once() -> list[float]:
            nonlocal attempts, progress
            attempts += 1
            with self._temporary_timeout(timeout_ms), self._read_progress(
                "query_float_list"
            ) as current_progress:
                progress = current_progress
                return list(self.session.query_bin_or_ascii_float_list(command))

        try:
            values = self._read_with_policy(
                "query_float_list",
                command,
                replay,
                read_once,
            )
        except Exception as exc:
            self._record_query_telemetry(
                operation="query_float_list",
                started=started,
                status="failed",
                progress=progress,
                replay=replay,
            )
            if isinstance(exc, TransportIOError):
                raise
            raise self._query_error("query_float_list", replay, attempts, exc) from exc
        self.logger.record("response", f"<float_list len={len(values)}>")
        self._record_query_telemetry(
            operation="query_float_list",
            started=started,
            status="ok",
            progress=progress,
            replay=replay,
            items=len(values),
        )
        return values

    def query_bin_block(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> bytes:
        replay = ReplayPolicy(replay)
        self._reject_continuation("query_binary", replay)
        self.logger.record("query_binary", command)
        started = time.perf_counter()
        progress: dict[str, int | None] | None = None
        attempts = 0

        def read_once() -> bytes:
            nonlocal attempts, progress
            attempts += 1
            with self._read_progress("query_binary") as current_progress:
                progress = current_progress
                return bytes(self.session.query_bin_block(command))

        try:
            data = self._read_with_policy(
                "query_binary",
                command,
                replay,
                read_once,
            )
        except Exception as exc:
            self._record_query_telemetry(
                operation="query_binary",
                started=started,
                status="failed",
                progress=progress,
                replay=replay,
            )
            if isinstance(exc, TransportIOError):
                raise
            raise self._query_error("query_binary", replay, attempts, exc) from exc
        self.logger.record("response", f"<bin_block len={len(data)}>")
        self._record_query_telemetry(
            operation="query_binary",
            started=started,
            status="ok",
            progress=progress,
            replay=replay,
            bytes_count=len(data),
        )
        return data

    @contextmanager
    def _temporary_timeout(self, timeout_ms: int | None):
        if timeout_ms is None:
            yield
            return
        if timeout_ms < 1:
            raise ValueError("timeout_ms must be >= 1")
        original_timeout = self.session.visa_timeout
        self.session.visa_timeout = timeout_ms
        try:
            yield
        finally:
            self.session.visa_timeout = original_timeout

    @contextmanager
    def _read_progress(self, operation: str):
        progress = {"transferred": 0, "total": None, "chunks": 0}
        events = getattr(self.session, "events", None)
        if events is None or not hasattr(events, "on_read_handler"):
            yield progress
            return
        previous_handler = events.on_read_handler
        previous_include_data = events.io_events_include_data

        def on_read(args: Any) -> None:
            transferred = int(getattr(args, "transferred_size", 0) or 0)
            total = getattr(args, "total_size", None)
            chunk_ix = int(getattr(args, "chunk_ix", 0) or 0)
            progress["transferred"] = max(progress["transferred"], transferred)
            progress["total"] = int(total) if total is not None else progress["total"]
            progress["chunks"] = max(progress["chunks"], chunk_ix + 1)
            fields = [
                f"operation={operation}_progress",
                f"chunk={chunk_ix + 1}",
                f"transferred_bytes={transferred}",
            ]
            if total is not None:
                fields.append(f"total_bytes={int(total)}")
            if bool(getattr(args, "end_of_transfer", False)):
                fields.append("end=true")
            self.logger.record("telemetry", " ".join(fields))
            if previous_handler is not None:
                previous_handler(args)

        events.io_events_include_data = False
        events.on_read_handler = on_read
        try:
            yield progress
        finally:
            events.on_read_handler = previous_handler
            events.io_events_include_data = previous_include_data

    def _record_query_telemetry(
        self,
        *,
        operation: str,
        started: float,
        status: str,
        progress: dict[str, int | None] | None,
        replay: ReplayPolicy,
        bytes_count: int | None = None,
        items: int | None = None,
    ) -> None:
        elapsed_s = max(time.perf_counter() - started, 0.0)
        transferred = int(progress["transferred"] or 0) if progress else 0
        measured_bytes = bytes_count if bytes_count is not None else transferred or None
        fields = [
            f"operation={operation}",
            f"status={status}",
            f"elapsed_ms={elapsed_s * 1000.0:.3f}",
            f"replay={replay.value}",
        ]
        if measured_bytes is not None:
            fields.append(f"bytes={measured_bytes}")
            throughput = (
                measured_bytes / elapsed_s / (1024.0 * 1024.0)
                if elapsed_s > 0
                else 0.0
            )
            fields.append(f"throughput_mib_s={throughput:.3f}")
        if items is not None:
            fields.append(f"items={items}")
        if progress and progress["chunks"]:
            fields.append(f"chunks={progress['chunks']}")
        self.logger.record("telemetry", " ".join(fields))

    def query_opc(
        self,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> str:
        replay = ReplayPolicy(replay)
        self._reject_continuation("query_opc", replay)
        self.logger.record("query", "*OPC?")
        attempts = 0

        def read_once() -> str:
            nonlocal attempts
            attempts += 1
            return str(self.session.query_opc()).strip()

        try:
            response = self._read_with_policy("query_opc", "*OPC?", replay, read_once)
        except Exception as exc:
            if isinstance(exc, TransportIOError):
                raise
            raise self._query_error("query_opc", replay, attempts, exc) from exc
        self.logger.record("response", response)
        return response

    def _read_with_policy(
        self,
        operation: str,
        command: str,
        replay: ReplayPolicy,
        read_once: Any,
    ) -> Any:
        attempt_limit = (
            self.read_retry_attempts + 1 if replay is ReplayPolicy.SAFE_TO_REPLAY else 1
        )
        last_exc: Exception | None = None
        transmitted_attempts = 0
        for attempt in range(attempt_limit):
            try:
                return read_once()
            except Exception as exc:
                last_exc = exc
                if isinstance(exc, TransportIOError):
                    transmitted_attempts += exc.attempts
                if attempt >= attempt_limit - 1 or not self._can_retry(exc, replay):
                    break
                if self.read_retry_delay_ms > 0:
                    time.sleep(self.read_retry_delay_ms / 1000.0)
                self.logger.record(
                    "retry",
                    f"operation={operation} attempt={attempt + 2}/{attempt_limit}",
                )
        assert last_exc is not None
        if isinstance(last_exc, TransportIOError) and last_exc.attempts != transmitted_attempts:
            raise last_exc.with_attempts(transmitted_attempts) from last_exc
        raise last_exc

    @staticmethod
    def _can_retry(exc: Exception, replay: ReplayPolicy) -> bool:
        return (
            replay is ReplayPolicy.SAFE_TO_REPLAY
            and isinstance(exc, TransportIOError)
            and exc.replay_policy is ReplayPolicy.SAFE_TO_REPLAY
            and exc.response_progress is ResponseProgress.NONE
            and exc.synchronization is Synchronization.PROVEN
            and exc.command_transmission
            in {CommandTransmission.NOT_SENT, CommandTransmission.SENT}
        )

    @staticmethod
    def _reject_continuation(operation: str, replay: ReplayPolicy) -> None:
        if replay is not ReplayPolicy.READ_CONTINUATION_ONLY:
            return
        raise TransportIOError(
            f"RsInstrument does not support read continuation for {operation}",
            operation=operation,
            phase=TransportPhase.BEFORE_SEND,
            replay_policy=replay,
            command_transmission=CommandTransmission.NOT_SENT,
            response_progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
        )

    @staticmethod
    def _query_error(
        operation: str,
        replay: ReplayPolicy,
        attempts: int,
        exc: Exception,
    ) -> TransportIOError:
        return TransportIOError(
            f"RsInstrument {operation} failed with {type(exc).__name__}",
            operation=operation,
            phase=TransportPhase.READING,
            replay_policy=replay,
            command_transmission=CommandTransmission.UNKNOWN,
            response_progress=ResponseProgress.UNKNOWN,
            synchronization=Synchronization.UNPROVEN,
            attempts=attempts,
        )

    @staticmethod
    def _write_error(operation: str, exc: Exception) -> TransportIOError:
        return TransportIOError(
            f"RsInstrument {operation} failed with {type(exc).__name__}",
            operation=operation,
            phase=TransportPhase.SENDING,
            replay_policy=ReplayPolicy.NO_REPLAY,
            command_transmission=CommandTransmission.UNKNOWN,
            response_progress=ResponseProgress.NONE,
            synchronization=Synchronization.UNPROVEN,
            attempts=1,
        )

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass
