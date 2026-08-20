from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from wavebench.config import ConnectionConfig
from wavebench.errors import ConfigError, WaveBenchError
from wavebench.logging import CommandLogger
from wavebench.services.resource_lease import ResourceLease
from wavebench.transport.pyvisa_transport import PyVisaTransport
from wavebench.transport.rsinstrument_transport import RsInstrumentTransport
from wavebench.transport.contracts import ReplayPolicy

from .api import SUPPORTED_PLUGIN_API_VERSION, InstrumentPlugin
from .registry import PluginRegistry, plugin_doctor_records


@dataclass(frozen=True)
class DeclarativeScpiPlugin:
    plugin: InstrumentPlugin
    idn_query: str


@dataclass(frozen=True)
class ScpiProbeResult:
    driver_id: str
    resource: str
    backend: str
    query: str
    response: str
    matched: bool


def load_scpi_plugin(path: str | Path) -> DeclarativeScpiPlugin:
    plugin_path = Path(path)
    try:
        raw = tomllib.loads(plugin_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"unable to read SCPI plugin {plugin_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid SCPI plugin TOML {plugin_path}: {exc}") from exc
    return _parse_scpi_plugin(raw)


def scpi_plugin_doctor_records(
    path: str | Path,
    *,
    probe_resource: str | None = None,
    backend: str = "pyvisa",
    timeout_ms: int = 1_000,
    transport_factory=None,
):
    try:
        scpi_plugin = load_scpi_plugin(path)
    except ConfigError as exc:
        subject = str(path)
        return [("error", subject, str(exc))]
    records = plugin_doctor_records(PluginRegistry((scpi_plugin.plugin,)))
    extra_errors = _validate_scpi_query(scpi_plugin.idn_query)
    doctor_records = [(record.severity, record.subject, record.message) for record in records] + [
        ("error", scpi_plugin.plugin.driver_id, message) for message in extra_errors
    ]
    if probe_resource is None:
        return doctor_records
    if has_scpi_doctor_errors(doctor_records):
        return doctor_records + [
            ("warning", scpi_plugin.plugin.driver_id, "probe skipped because metadata validation failed")
        ]
    return doctor_records + _scpi_probe_doctor_records(
        path,
        probe_resource=probe_resource,
        backend=backend,
        timeout_ms=timeout_ms,
        transport_factory=transport_factory,
    )


def has_scpi_doctor_errors(records) -> bool:
    return any(record[0] == "error" for record in records)


def probe_scpi_plugin(
    path: str | Path,
    *,
    resource: str,
    backend: str = "pyvisa",
    timeout_ms: int = 1_000,
    transport_factory=None,
) -> ScpiProbeResult:
    if not resource.strip():
        raise ConfigError("--resource must be non-empty")
    if timeout_ms < 1:
        raise ConfigError("--timeout-ms must be >= 1")
    scpi_plugin = load_scpi_plugin(path)
    query_errors = _validate_scpi_query(scpi_plugin.idn_query)
    if query_errors:
        raise ConfigError("; ".join(query_errors))
    normalized_backend = backend.strip().lower()
    config = ConnectionConfig(
        backend=normalized_backend,
        resource=resource.strip(),
        timeout_ms=timeout_ms,
        opc_timeout_ms=timeout_ms,
    )
    factory = transport_factory or _transport_factory(normalized_backend)
    with ResourceLease(resource=config.resource, operation="scpi.probe"):
        transport = factory(config, CommandLogger())
        try:
            response = transport.query(scpi_plugin.idn_query, replay=ReplayPolicy.NO_REPLAY).strip()
        finally:
            transport.close()
    return ScpiProbeResult(
        driver_id=scpi_plugin.plugin.driver_id,
        resource=config.resource,
        backend=normalized_backend,
        query=scpi_plugin.idn_query,
        response=response,
        matched=_matches_idn(scpi_plugin.plugin.idn_patterns, response),
    )


def _scpi_probe_doctor_records(
    path: str | Path,
    *,
    probe_resource: str,
    backend: str,
    timeout_ms: int,
    transport_factory=None,
):
    try:
        result = probe_scpi_plugin(
            path,
            resource=probe_resource,
            backend=backend,
            timeout_ms=timeout_ms,
            transport_factory=transport_factory,
        )
    except WaveBenchError as exc:
        return [("error", "probe", str(exc))]
    records = [("ok", "probe", f"idn_response={result.response}")]
    if result.matched:
        records.append(("ok", "probe", "idn matched declared patterns"))
    else:
        records.append(("error", "probe", "idn did not match declared patterns"))
    return records


def _transport_factory(backend: str):
    if backend == "pyvisa":
        return PyVisaTransport.open
    if backend == "rsinstrument":
        return RsInstrumentTransport.open
    raise ConfigError("SCPI probe backend must be one of: pyvisa, rsinstrument")


def _parse_scpi_plugin(raw: Any) -> DeclarativeScpiPlugin:
    if not isinstance(raw, dict):
        raise ConfigError("SCPI plugin file must contain a TOML table")
    scpi = raw.get("scpi", {})
    if not isinstance(scpi, dict):
        raise ConfigError("SCPI plugin field 'scpi' must be a table")
    idn_query = _optional_text(scpi, "idn_query", "*IDN?")
    try:
        plugin = InstrumentPlugin(
            driver_id=_required_text(raw, "driver_id"),
            kind=_required_text(raw, "kind"),
            display_name=_required_text(raw, "display_name"),
            manufacturer=_required_text(raw, "manufacturer"),
            models=_required_text_tuple(raw, "models"),
            capabilities=_required_text_tuple(raw, "capabilities"),
            summary=_required_text(raw, "summary"),
            api_version=_optional_text(raw, "api_version", SUPPORTED_PLUGIN_API_VERSION),
            package=_optional_text(raw, "package", "local"),
            origin="local",
            idn_patterns=_optional_text_tuple(raw, "idn_patterns"),
            config_fields=_optional_text_tuple(raw, "config_fields"),
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    return DeclarativeScpiPlugin(plugin=plugin, idn_query=idn_query)


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"SCPI plugin field {key!r} must be a non-empty string")
    return value.strip()


def _optional_text(raw: dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"SCPI plugin field {key!r} must be a non-empty string")
    return value.strip()


def _required_text_tuple(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    values = raw.get(key)
    result = _text_tuple(values, key)
    if not result:
        raise ConfigError(f"SCPI plugin field {key!r} must contain at least one string")
    return result


def _optional_text_tuple(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    return _text_tuple(raw.get(key, []), key)


def _text_tuple(values: Any, key: str) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise ConfigError(f"SCPI plugin field {key!r} must be a list")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"SCPI plugin field {key!r} must contain strings")
        result.append(value.strip())
    return tuple(result)


def _validate_scpi_query(command: str) -> list[str]:
    errors: list[str] = []
    if "\n" in command or "\r" in command:
        errors.append("scpi.idn_query must be a single-line command")
    if ";" in command:
        errors.append("scpi.idn_query must not contain command separators")
    if not command.endswith("?"):
        errors.append("scpi.idn_query must be a query ending with '?'")
    return errors


def _matches_idn(patterns: tuple[str, ...], response: str) -> bool:
    if not patterns:
        return True
    normalized_response = response.lower()
    return any(pattern.lower() in normalized_response for pattern in patterns)
