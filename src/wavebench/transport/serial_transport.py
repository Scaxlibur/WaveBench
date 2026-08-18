from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from wavebench.config import DmmConfig
from wavebench.errors import ConfigError, ConnectionError, TransportIOError
from wavebench.logging import CommandLogger

from .contracts import (
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)


_PARITY_ALIASES = {
    "N": "N",
    "NONE": "N",
    "O": "O",
    "ODD": "O",
    "E": "E",
    "EVEN": "E",
}

_TERMINATIONS = {
    "LF": b"\n",
    "CRLF": b"\r\n",
}


@dataclass
class SerialTransport:
    resource: str
    session: Any
    logger: CommandLogger
    write_termination: bytes = b"\n"
    read_termination: bytes = b"\n"
    read_retry_attempts: int = 1
    read_retry_delay_ms: int = 200

    @classmethod
    def open(
        cls,
        config: DmmConfig,
        logger: CommandLogger | None = None,
        *,
        read_retry_attempts: int = 1,
        read_retry_delay_ms: int = 200,
    ) -> "SerialTransport":
        try:
            write_termination = _TERMINATIONS[config.write_termination.strip().upper()]
            read_termination = _TERMINATIONS[config.read_termination.strip().upper()]
        except (AttributeError, KeyError) as exc:
            raise ConfigError("serial terminations must be lf or crlf") from exc
        try:
            import serial
        except ImportError as exc:
            raise ConnectionError("pyserial is not installed. Run: python -m pip install pyserial") from exc
        if not config.resource:
            raise ConnectionError("serial resource is not configured")
        logger = logger or CommandLogger()
        try:
            session = serial.Serial(
                port=config.resource,
                baudrate=config.baudrate,
                bytesize=config.bytesize,
                parity=_PARITY_ALIASES[config.parity.upper()],
                stopbits=config.stopbits,
                timeout=config.timeout_ms / 1000.0,
                write_timeout=config.timeout_ms / 1000.0,
                xonxoff=config.xonxoff,
                rtscts=config.rtscts,
                dsrdtr=config.dsrdtr,
            )
        except Exception as exc:
            raise ConnectionError(f"failed to open serial instrument {config.resource}: {exc}") from exc
        return cls(
            resource=config.resource,
            session=session,
            logger=logger,
            write_termination=write_termination,
            read_termination=read_termination,
            read_retry_attempts=read_retry_attempts,
            read_retry_delay_ms=read_retry_delay_ms,
        )

    def record_event(self, direction: str, text: str) -> None:
        self.logger.record(direction, text)

    def write(self, command: str) -> None:
        self.logger.record("write", command)
        try:
            payload = command.rstrip("\r\n").encode("ascii") + self.write_termination
            written = self.session.write(payload)
            if written is not None and written != len(payload):
                raise OSError(f"short serial write: wrote {written} of {len(payload)} bytes")
            if hasattr(self.session, "flush"):
                self.session.flush()
        except Exception as exc:
            raise self._write_error("write", exc) from exc

    def write_bytes(self, command: bytes) -> None:
        self.logger.record("write_binary", f"<bytes len={len(command)}>")
        try:
            self.session.write(command)
            if hasattr(self.session, "flush"):
                self.session.flush()
        except Exception as exc:
            raise self._write_error("write_binary", exc) from exc

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
            payload = command.rstrip("\r\n").encode("ascii") + self.write_termination
            written = self.session.write(payload)
            if written == 0:
                raise TransportIOError(
                    "serial query command was not transmitted",
                    operation="query",
                    phase=TransportPhase.SENDING,
                    replay_policy=replay,
                    command_transmission=CommandTransmission.NOT_SENT,
                    response_progress=ResponseProgress.NONE,
                    synchronization=Synchronization.PROVEN,
                    attempts=0,
                )
            attempts += 1
            if written is not None and written != len(payload):
                raise TransportIOError(
                    "serial query command transmission was partial",
                    operation="query",
                    phase=TransportPhase.SENDING,
                    replay_policy=replay,
                    command_transmission=CommandTransmission.UNKNOWN,
                    response_progress=ResponseProgress.NONE,
                    synchronization=Synchronization.LOST,
                    attempts=attempts,
                )
            if hasattr(self.session, "flush"):
                self.session.flush()
            if hasattr(self.session, "read_until"):
                raw = self.session.read_until(self.read_termination)
            else:
                raw = self.session.readline()
            if not raw:
                raise TransportIOError(
                    "serial query timed out before a response was observed",
                    operation="query",
                    phase=TransportPhase.READING,
                    replay_policy=replay,
                    command_transmission=CommandTransmission.SENT,
                    response_progress=ResponseProgress.NONE,
                    synchronization=Synchronization.UNPROVEN,
                    attempts=attempts,
                )
            if not raw.endswith(self.read_termination):
                raise TransportIOError(
                    "serial response ended before the configured termination",
                    operation="query",
                    phase=TransportPhase.READING,
                    replay_policy=replay,
                    command_transmission=CommandTransmission.SENT,
                    response_progress=ResponseProgress.PARTIAL,
                    synchronization=Synchronization.LOST,
                    attempts=attempts,
                )
            response_bytes = raw[: -len(self.read_termination)]
            if self.read_termination == b"\n" and response_bytes.endswith(b"\r"):
                response_bytes = response_bytes[:-1]
            return response_bytes.decode("ascii")

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
        if timeout_ms is not None and timeout_ms < 1:
            raise ValueError("timeout_ms must be >= 1")
        original_timeout = getattr(self.session, "timeout", None)
        if timeout_ms is not None and hasattr(self.session, "timeout"):
            self.session.timeout = timeout_ms / 1000.0
        try:
            response = self.query(command, replay=replay)
            try:
                return [
                    float(item)
                    for item in response.replace(";", ",").split(",")
                    if item.strip()
                ]
            except ValueError as exc:
                raise TransportIOError(
                    "serial query_float_list response parsing failed",
                    operation="query_float_list",
                    phase=TransportPhase.PARSING,
                    replay_policy=ReplayPolicy(replay),
                    command_transmission=CommandTransmission.SENT,
                    response_progress=ResponseProgress.COMPLETE,
                    synchronization=Synchronization.PROVEN,
                    attempts=1,
                ) from exc
        finally:
            if timeout_ms is not None and hasattr(self.session, "timeout"):
                self.session.timeout = original_timeout

    def query_bin_block(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> bytes:
        replay = ReplayPolicy(replay)
        if replay is ReplayPolicy.READ_CONTINUATION_ONLY:
            self._reject_continuation("query_binary", replay)
        raise TransportIOError(
            "serial transport does not support binary block queries",
            operation="query_binary",
            phase=TransportPhase.BEFORE_SEND,
            replay_policy=replay,
            command_transmission=CommandTransmission.NOT_SENT,
            response_progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
        )

    def query_opc(
        self,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> str:
        return self.query("*OPC?", replay=replay)

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
            f"serial does not support read continuation for {operation}",
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
            f"serial {operation} failed with {type(exc).__name__}",
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
            f"serial {operation} failed with {type(exc).__name__}",
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
