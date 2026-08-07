from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, cast

from wavebench.config import ConnectionConfig, DmmConfig
from wavebench.errors import ConfigError
from wavebench.logging import CommandLogger
from wavebench.plugins.api import PluginKind
from wavebench.services.access_policy import AccessMode, normalize_access_mode
from wavebench.transport.base import InstrumentTransport
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.pyvisa_transport import PyVisaTransport
from wavebench.transport.rsinstrument_transport import RsInstrumentTransport
from wavebench.transport.serial_transport import SerialTransport

from .api import DriverContext, InstrumentDescriptor
from .capabilities import validate_declared_capabilities
from .contracts import InstrumentDriver
from .registry import resolve_instrument_descriptor

RSINSTRUMENT_BACKENDS = (
    "rsinstrument-socket",
    "rsinstrument",
    "rsinstrument-rsvisa",
    "rsinstrument-pyvisa-py",
)

RSINSTRUMENT_SOCKET_DEFAULT_PORT = 5025


@dataclass(frozen=True)
class OpenedInstrument:
    descriptor: InstrumentDescriptor
    driver: InstrumentDriver
    transport: InstrumentTransport | None = None


def open_instrument_driver(
    *,
    driver_reference: str,
    expected_kind: PluginKind,
    resource: str,
    configured_backend: str,
    timeout_ms: int,
    opc_timeout_ms: int,
    read_retry_attempts: int,
    read_retry_delay_ms: int,
    logger: CommandLogger,
    settings: Mapping[str, object] | None = None,
    options: Mapping[str, object] | None = None,
    serial_config: DmmConfig | None = None,
    access: AccessMode = "read_write",
) -> OpenedInstrument:
    normalized_access = normalize_access_mode(access, "access")
    descriptor = resolve_instrument_descriptor(
        driver_reference,
        expected_kind=expected_kind,
    )
    backend = _select_backend(configured_backend, descriptor.backends)
    _validate_resource_scheme(resource, descriptor.resource_schemes)
    try:
        validated_options = descriptor.validate_options(options or {})
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid options for instrument driver {descriptor.driver_id!r}: {exc}") from exc

    opened_transports: list[InstrumentTransport] = []

    def open_transport() -> InstrumentTransport:
        if opened_transports:
            raise ConfigError(
                f"instrument driver {descriptor.driver_id!r} requested more than one transport; "
                "instrument API v2 factories may open exactly one configured transport"
            )
        concrete_transport = _open_transport(
            backend=backend,
            resource=resource,
            timeout_ms=timeout_ms,
            opc_timeout_ms=opc_timeout_ms,
            read_retry_attempts=read_retry_attempts,
            read_retry_delay_ms=read_retry_delay_ms,
            logger=logger,
            serial_config=serial_config,
        )
        transport = GuardedAuditedTransport(concrete_transport, access=normalized_access)
        opened_transports.append(transport)
        return transport

    context = DriverContext(
        driver_id=descriptor.driver_id,
        kind=descriptor.kind,
        resource=resource,
        backend=backend,
        timeout_ms=timeout_ms,
        opc_timeout_ms=opc_timeout_ms,
        logger=logger,
        _transport_factory=open_transport,
        settings=settings or {},
        options=validated_options,
        access=normalized_access,
    )
    try:
        driver = descriptor.factory(context)
        validate_declared_capabilities(descriptor, driver)
    except Exception as exc:
        _close_factory_failure(driver if "driver" in locals() else None, opened_transports)
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(
            f"failed to create {expected_kind} instrument driver {descriptor.driver_id!r}: {exc}"
        ) from exc
    return OpenedInstrument(
        descriptor=descriptor,
        driver=cast(InstrumentDriver, driver),
        transport=opened_transports[0] if opened_transports else None,
    )


def _select_backend(configured_backend: str, supported: tuple[str, ...]) -> str:
    configured = configured_backend.strip().lower()
    aliases = {"lan": "pyvisa", "visa": "pyvisa", "pyvisa": "pyvisa"}
    normalized = aliases.get(configured, configured)
    if normalized in supported:
        return normalized
    rsinstrument_backends = tuple(
        backend for backend in supported if backend in RSINSTRUMENT_BACKENDS
    )
    if configured == "lan" and rsinstrument_backends and len(rsinstrument_backends) == len(supported):
        return rsinstrument_backends[0]
    if configured in {"visa", "pyvisa"} and supported == ("rsinstrument",):
        return "rsinstrument"
    raise ConfigError(
        f"configured backend {configured_backend!r} is not supported; "
        f"driver supports: {', '.join(supported)}"
    )


def _validate_resource_scheme(resource: str, supported: tuple[str, ...]) -> None:
    if not supported:
        return
    match = re.match(r"[A-Za-z]+", resource.strip())
    configured = match.group(0).lower() if match is not None else "unknown"
    if configured in supported:
        return
    raise ConfigError(
        f"configured resource scheme {configured!r} is not supported; "
        f"driver supports: {', '.join(supported)}"
    )


def _normalize_rsinstrument_socket_resource(resource: str) -> str:
    stripped = resource.strip()
    socket_match = re.fullmatch(
        r"TCPIP\d*::([^:]+)::([0-9]+)::SOCKET",
        stripped,
        flags=re.IGNORECASE,
    )
    if socket_match is not None:
        host, raw_port = socket_match.groups()
        port = int(raw_port)
        if not 1 <= port <= 65_535:
            raise ConfigError("RsInstrument SocketIO TCP port must be between 1 and 65535")
        return f"TCPIP::{host}::{port}::SOCKET"

    instr_match = re.fullmatch(
        r"TCPIP\d*::([^:]+)::INSTR",
        stripped,
        flags=re.IGNORECASE,
    )
    if instr_match is not None:
        host = instr_match.group(1)
        return (
            f"TCPIP::{host}::{RSINSTRUMENT_SOCKET_DEFAULT_PORT}::SOCKET"
        )

    raise ConfigError(
        "RsInstrument SocketIO requires TCPIP::<host>::INSTR or "
        "TCPIP::<host>::<port>::SOCKET; use an explicit VISA-based backend "
        "for other TCPIP resource forms"
    )


def _open_transport(
    *,
    backend: str,
    resource: str,
    timeout_ms: int,
    opc_timeout_ms: int,
    read_retry_attempts: int,
    read_retry_delay_ms: int,
    logger: CommandLogger,
    serial_config: DmmConfig | None,
) -> InstrumentTransport:
    if backend == "serial":
        if serial_config is None:
            raise ConfigError("serial instrument driver requires serial configuration")
        return SerialTransport.open(serial_config, logger=logger)
    connection = ConnectionConfig(
        backend="lan",
        resource=resource,
        timeout_ms=timeout_ms,
        opc_timeout_ms=opc_timeout_ms,
        read_retry_attempts=read_retry_attempts,
        read_retry_delay_ms=read_retry_delay_ms,
    )
    if backend == "pyvisa":
        return PyVisaTransport.open(connection, logger=logger)
    if backend == "rsinstrument":
        return RsInstrumentTransport.open(connection, logger=logger)
    select_visa = {
        "rsinstrument-socket": "socketio",
        "rsinstrument-rsvisa": "rs",
        "rsinstrument-pyvisa-py": "pyvisa-py",
    }.get(backend)
    if select_visa is not None:
        if backend == "rsinstrument-socket":
            resource = _normalize_rsinstrument_socket_resource(resource)
            connection = ConnectionConfig(
                backend="lan",
                resource=resource,
                timeout_ms=timeout_ms,
                opc_timeout_ms=opc_timeout_ms,
                read_retry_attempts=read_retry_attempts,
                read_retry_delay_ms=read_retry_delay_ms,
            )
        return RsInstrumentTransport.open(
            connection,
            logger=logger,
            select_visa=select_visa,
        )
    raise ConfigError(f"unsupported instrument transport backend: {backend}")


def _close_factory_failure(
    driver: object | None,
    opened_transports: list[InstrumentTransport],
) -> None:
    if driver is not None and callable(getattr(driver, "close", None)):
        try:
            driver.close()
            return
        except Exception:
            pass
    for transport in reversed(opened_transports):
        try:
            transport.close()
        except Exception:
            pass
