from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from wavebench.config import ConnectionConfig
from wavebench.errors import ConnectionError, SessionCloseError, TransportIOError
from wavebench.logging import CommandLogger

from .contracts import (
    BinaryQueryResult,
    BinaryResponseFraming,
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)


@dataclass
class PyVisaTransport:
    resource: str
    resource_manager: Any
    session: Any
    logger: CommandLogger
    read_retry_attempts: int = 1
    read_retry_delay_ms: int = 200

    @classmethod
    def open(cls, config: ConnectionConfig, logger: CommandLogger | None = None) -> "PyVisaTransport":
        try:
            import pyvisa
        except ImportError as exc:
            raise ConnectionError("pyvisa is not installed. Run: python -m pip install pyvisa") from exc
        logger = logger or CommandLogger()
        try:
            try:
                resource_manager = pyvisa.ResourceManager()
            except ValueError:
                resource_manager = pyvisa.ResourceManager("@py")
            session = resource_manager.open_resource(config.resource)
            session.timeout = config.timeout_ms
            try:
                session.read_termination = "\n"
            except Exception:
                pass
            try:
                session.write_termination = "\n"
            except Exception:
                pass
        except Exception as exc:
            raise ConnectionError(f"failed to open instrument {config.resource}: {exc}") from exc
        return cls(
            resource=config.resource,
            resource_manager=resource_manager,
            session=session,
            logger=logger,
            read_retry_attempts=config.read_retry_attempts,
            read_retry_delay_ms=config.read_retry_delay_ms,
        )

    def record_event(self, direction: str, text: str) -> None:
        self.logger.record(direction, text)

    def write(self, command: str) -> None:
        self.logger.record("write", command)
        try:
            written = self.session.write(command)
            if written == 0:
                raise self._not_sent_error("write")
        except TransportIOError:
            raise
        except Exception as exc:
            raise self._write_error("write", exc) from exc

    def write_bytes(self, command: bytes) -> None:
        self.logger.record("write_binary", f"<bytes len={len(command)}>")
        if not hasattr(self.session, "write_raw"):
            raise self._not_sent_error("write_binary")
        try:
            written = self.session.write_raw(command)
            if written == 0:
                raise self._not_sent_error("write_binary")
            if written is not None and written != len(command):
                raise OSError(
                    f"short pyvisa binary write: wrote {written} of {len(command)} bytes"
                )
        except TransportIOError:
            raise
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
            attempts += 1
            return str(self.session.query(command)).strip()

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
        original_timeout = None
        attempts = 0

        def read_once() -> list[float]:
            nonlocal attempts
            attempts += 1
            response = self.session.query(command)
            try:
                return self._parse_ascii_float_list(response)
            except (TypeError, ValueError) as exc:
                raise TransportIOError(
                    "pyvisa query_float_list response parsing failed",
                    operation="query_float_list",
                    phase=TransportPhase.PARSING,
                    replay_policy=replay,
                    command_transmission=CommandTransmission.SENT,
                    response_progress=ResponseProgress.COMPLETE,
                    synchronization=Synchronization.PROVEN,
                    attempts=attempts,
                ) from exc

        try:
            if timeout_ms is not None:
                original_timeout = self.session.timeout
                self.session.timeout = timeout_ms
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
                replay=replay.value,
            )
            if isinstance(exc, TransportIOError):
                raise
            raise self._query_error("query_float_list", replay, attempts, exc) from exc
        finally:
            if timeout_ms is not None:
                self.session.timeout = original_timeout
        self.logger.record("response", f"<float_list len={len(values)}>")
        self._record_query_telemetry(
            operation="query_float_list",
            started=started,
            status="ok",
            replay=replay.value,
            items=len(values),
        )
        return values

    @staticmethod
    def _parse_ascii_float_list(response: object) -> list[float]:
        return [
            float(item)
            for item in str(response).replace(";", ",").split(",")
            if item.strip()
        ]

    def query_bin_block(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> bytes:
        replay = ReplayPolicy(replay)
        self._reject_continuation("query_binary", replay)
        self.logger.record("query_binary", command)
        if not hasattr(self.session, "query_binary_values"):
            raise TransportIOError(
                "pyvisa session does not support binary block queries",
                operation="query_binary",
                phase=TransportPhase.BEFORE_SEND,
                replay_policy=replay,
                command_transmission=CommandTransmission.NOT_SENT,
                response_progress=ResponseProgress.NONE,
                synchronization=Synchronization.PROVEN,
                attempts=0,
            )
        started = time.perf_counter()
        attempts = 0

        def read_once() -> bytes:
            nonlocal attempts
            attempts += 1
            return bytes(self.session.query_binary_values(command, datatype="B", container=bytes))

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
                replay=replay.value,
            )
            if isinstance(exc, TransportIOError):
                raise
            raise self._query_error("query_binary", replay, attempts, exc) from exc
        self.logger.record("response", f"<bin_block len={len(data)}>")
        self._record_query_telemetry(
            operation="query_binary",
            started=started,
            status="ok",
            replay=replay.value,
            bytes_count=len(data),
        )
        return data

    def query_binary(
        self,
        command: str,
        *,
        framing: BinaryResponseFraming,
        max_bytes: int,
        timeout_ms: int | None = None,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> BinaryQueryResult:
        replay = ReplayPolicy(replay)
        BinaryResponseFraming(framing)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        if timeout_ms is not None and (
            isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms < 1
        ):
            raise ValueError("timeout_ms must be a positive integer")
        self._reject_continuation("query_binary", replay)
        raise TransportIOError(
            "pyvisa has not passed the R1.3 binary framing conformance gate",
            operation="query_binary",
            phase=TransportPhase.BEFORE_SEND,
            replay_policy=replay,
            command_transmission=CommandTransmission.NOT_SENT,
            response_progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
            reason_code="binary_framing_unsupported",
            consumed_bytes=0,
            discarded_bytes=0,
        )

    def _record_query_telemetry(
        self,
        *,
        operation: str,
        started: float,
        status: str,
        replay: str,
        bytes_count: int | None = None,
        items: int | None = None,
    ) -> None:
        elapsed_s = max(time.perf_counter() - started, 0.0)
        fields = [
            f"operation={operation}",
            f"status={status}",
            f"elapsed_ms={elapsed_s * 1000.0:.3f}",
            f"replay={replay}",
        ]
        if bytes_count is not None:
            fields.append(f"bytes={bytes_count}")
            throughput = bytes_count / elapsed_s / (1024.0 * 1024.0) if elapsed_s > 0 else 0.0
            fields.append(f"throughput_mib_s={throughput:.3f}")
        if items is not None:
            fields.append(f"items={items}")
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
            return str(self.session.query("*OPC?")).strip()

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
        attempts = self.read_retry_attempts + 1 if replay is ReplayPolicy.SAFE_TO_REPLAY else 1
        last_exc: Exception | None = None
        transmitted_attempts = 0
        for attempt in range(attempts):
            try:
                return read_once()
            except Exception as exc:
                last_exc = exc
                if isinstance(exc, TransportIOError):
                    transmitted_attempts += exc.attempts
                if attempt >= attempts - 1 or not self._can_retry(exc, replay):
                    break
                if self.read_retry_delay_ms > 0:
                    time.sleep(self.read_retry_delay_ms / 1000.0)
                self.logger.record(
                    "retry",
                    f"operation={operation} attempt={attempt + 2}/{attempts}",
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
            f"pyvisa does not support read continuation for {operation}",
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
            f"pyvisa {operation} failed with {type(exc).__name__}",
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
            f"pyvisa {operation} failed with {type(exc).__name__}",
            operation=operation,
            phase=TransportPhase.SENDING,
            replay_policy=ReplayPolicy.NO_REPLAY,
            command_transmission=CommandTransmission.UNKNOWN,
            response_progress=ResponseProgress.NONE,
            synchronization=Synchronization.UNPROVEN,
            attempts=1,
        )

    @staticmethod
    def _not_sent_error(operation: str) -> TransportIOError:
        return TransportIOError(
            f"pyvisa {operation} command was not transmitted",
            operation=operation,
            phase=TransportPhase.BEFORE_SEND,
            replay_policy=ReplayPolicy.NO_REPLAY,
            command_transmission=CommandTransmission.NOT_SENT,
            response_progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
        )

    def close(self) -> None:
        failures: list[tuple[str, BaseException]] = []
        try:
            self.session.close()
        except Exception as exc:
            failures.append(("session", exc))
        try:
            self.resource_manager.close()
        except Exception as exc:
            failures.append(("resource_manager", exc))
        if failures:
            raise SessionCloseError(failures)
