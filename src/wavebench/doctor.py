from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Sequence

from .config import DmmConfig, WaveBenchConfig
from .discovery import DEFAULT_DISCOVERY_PORTS, DiscoveryResult, discover_instruments
from .errors import ConfigError, ResourceBusyError
from .instruments.registry import resolve_instrument_descriptor
from .services.resource_lease import ResourceLease
from .transport.serial_transport import SerialTransport


@dataclass(frozen=True)
class DoctorTarget:
    name: str
    driver: str
    resource: str | None
    expected_idn_tokens: tuple[str, ...] = ()
    serial_config: DmmConfig | None = None


@dataclass(frozen=True)
class DoctorRecord:
    severity: str
    target: str
    driver: str
    resource: str
    idn: str | None
    message: str
    suggestion: str = ""


IdnProbe = Callable[[str, int], str | None]
InstrumentDiscoverer = Callable[..., list[DiscoveryResult]]


def doctor_records(
    config: WaveBenchConfig,
    *,
    timeout_ms: int | None = None,
    idn_probe: IdnProbe | None = None,
    discover_subnet: str | None = None,
    discover_ports: str | Sequence[int] = DEFAULT_DISCOVERY_PORTS,
    discover_timeout_ms: int | None = None,
    discover_workers: int = 64,
    discover_max_hosts: int = 256,
    include_visa: bool = True,
    discoverer: InstrumentDiscoverer | None = None,
) -> list[DoctorRecord]:
    timeout = timeout_ms or config.connection.timeout_ms
    targets = _doctor_targets(config)
    records = [_doctor_target(target, timeout_ms=timeout, idn_probe=idn_probe) for target in targets]
    if discover_subnet:
        discovery_results = (discoverer or discover_instruments)(
            subnet=discover_subnet,
            ports=discover_ports,
            timeout_ms=discover_timeout_ms or timeout,
            workers=discover_workers,
            max_hosts=discover_max_hosts,
            query_idn=True,
            idn_only=True,
            include_visa=include_visa,
        )
        records.extend(_candidate_records(targets, records, discovery_results))
    return records


def has_doctor_errors(records: list[DoctorRecord]) -> bool:
    return any(record.severity == "error" for record in records)


def query_resource_idn(resource: str, timeout_ms: int) -> str | None:
    try:
        import pyvisa  # type: ignore[import-not-found]
    except Exception:
        return None
    with ResourceLease(resource=resource, operation="doctor.idn"):
        manager = None
        session = None
        try:
            manager = pyvisa.ResourceManager()
            session = manager.open_resource(resource)
            try:
                session.timeout = timeout_ms
                session.read_termination = "\n"
                session.write_termination = "\n"
            except Exception:
                pass
            return str(session.query("*IDN?")).strip() or None
        except (ResourceBusyError, ConfigError):
            raise
        except Exception:
            return None
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            if manager is not None:
                try:
                    manager.close()
                except Exception:
                    pass


def query_target_idn(target: DoctorTarget, timeout_ms: int) -> str | None:
    if target.serial_config is None:
        return query_resource_idn(target.resource or "", timeout_ms)
    transport = None
    try:
        with ResourceLease(resource=target.resource or "", operation="doctor.idn"):
            try:
                transport = SerialTransport.open(replace(target.serial_config, timeout_ms=timeout_ms))
                return transport.query("*IDN?") or None
            finally:
                if transport is not None:
                    transport.close()
    except (ResourceBusyError, ConfigError):
        raise
    except Exception:
        return None


def _doctor_target(
    target: DoctorTarget,
    *,
    timeout_ms: int,
    idn_probe: IdnProbe | None,
) -> DoctorRecord:
    resource = target.resource or ""
    if not resource:
        return DoctorRecord(
            severity="warning",
            target=target.name,
            driver=target.driver,
            resource="",
            idn=None,
            message="resource not configured / 资源未配置",
            suggestion="set the instrument resource in wavebench.toml / 在 wavebench.toml 中配置资源",
        )
    idn = (
        idn_probe(resource, timeout_ms)
        if idn_probe is not None
        else query_target_idn(target, timeout_ms)
    )
    if not idn:
        return DoctorRecord(
            severity="error",
            target=target.name,
            driver=target.driver,
            resource=resource,
            idn=None,
            message="no *IDN? response / 没有 *IDN? 响应",
            suggestion=_resource_suggestion(resource),
        )
    if target.expected_idn_tokens and not _idn_matches(idn, target.expected_idn_tokens):
        expected = ", ".join(target.expected_idn_tokens)
        return DoctorRecord(
            severity="warning",
            target=target.name,
            driver=target.driver,
            resource=resource,
            idn=idn,
            message=f"IDN does not match expected token(s): {expected} / IDN 与预期型号不匹配",
            suggestion="verify driver/resource mapping in wavebench.toml / 检查配置中的 driver 与 resource 是否对应",
        )
    return DoctorRecord(
        severity="ok",
        target=target.name,
        driver=target.driver,
        resource=resource,
        idn=idn,
        message="reachable / 可达",
    )


def _doctor_targets(config: WaveBenchConfig) -> list[DoctorTarget]:
    targets = [
        DoctorTarget(
            name="scope",
            driver=config.scope.driver,
            resource=config.connection.resource,
            expected_idn_tokens=_scope_expected_tokens(config.scope.driver, config.scope.model_hint),
        )
    ]
    if config.source is not None:
        targets.append(
            DoctorTarget(
                name="source",
                driver=config.source.driver,
                resource=config.source.resource,
                expected_idn_tokens=_driver_expected_tokens(config.source.driver),
            )
        )
    if config.power is not None:
        targets.append(
            DoctorTarget(
                name="power",
                driver=config.power.driver,
                resource=config.power.resource,
                expected_idn_tokens=_driver_expected_tokens(config.power.driver),
            )
        )
    if config.dmm is not None:
        targets.append(
            DoctorTarget(
                name="dmm",
                driver=config.dmm.driver,
                resource=config.dmm.resource,
                expected_idn_tokens=_driver_expected_tokens(config.dmm.driver),
                serial_config=config.dmm if config.dmm.backend.strip().lower() == "serial" else None,
            )
        )
    return targets


def _candidate_records(
    targets: list[DoctorTarget],
    records: list[DoctorRecord],
    discovery_results: list[DiscoveryResult],
) -> list[DoctorRecord]:
    candidates: list[DoctorRecord] = []
    needs_candidate = {record.target for record in records if record.severity != "ok"}
    for target in targets:
        if target.name not in needs_candidate or not target.expected_idn_tokens:
            continue
        for result in discovery_results:
            if not result.idn:
                continue
            if result.resource == target.resource:
                continue
            if not _idn_matches(result.idn, target.expected_idn_tokens):
                continue
            candidates.append(
                DoctorRecord(
                    severity="candidate",
                    target=target.name,
                    driver=target.driver,
                    resource=result.resource,
                    idn=result.idn,
                    message="candidate replacement resource / 可能的替代资源",
                    suggestion=f"consider updating {_resource_config_key(target.name)} to {result.resource} / 可考虑更新配置资源",
                )
            )
    return candidates


def _scope_expected_tokens(driver: str, model_hint: str | None) -> tuple[str, ...]:
    if model_hint:
        return (model_hint,)
    return _driver_expected_tokens(driver)


def _driver_expected_tokens(driver: str) -> tuple[str, ...]:
    try:
        return resolve_instrument_descriptor(driver).idn_patterns
    except ConfigError:
        return ()


def _idn_matches(idn: str, tokens: tuple[str, ...]) -> bool:
    normalized_idn = _normalize_idn(idn)
    return any(_normalize_idn(token) in normalized_idn for token in tokens)


def _normalize_idn(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _resource_suggestion(resource: str) -> str:
    if resource.upper().startswith("TCPIP"):
        return (
            "check power, Ethernet cable, IP address, subnet route, and instrument remote setting / "
            "检查电源、网线、IP、网段路由和仪器远程控制设置"
        )
    if (
        resource.upper().startswith("ASRL")
        or resource.upper().startswith("COM")
        or resource.startswith("\\\\.\\")
        or resource.startswith("/dev/")
    ):
        return "check serial device path, USB adapter, baudrate, and permissions / 检查串口路径、转接器、波特率和权限"
    return "check resource string and instrument connection / 检查资源字符串和仪器连接"


def _resource_config_key(target_name: str) -> str:
    if target_name == "scope":
        return "connection.resource"
    return f"{target_name}.resource"
