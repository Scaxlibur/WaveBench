from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import ipaddress
import re
import socket
from typing import Callable, Iterable, Sequence

from wavebench.errors import ConfigError, ResourceBusyError
from wavebench.services.resource_lease import ResourceLease


DEFAULT_DISCOVERY_PORTS = (5025, 5555, 111)
SCPI_SOCKET_PORTS = {5025, 5555}


@dataclass(frozen=True)
class PortProbe:
    open: bool
    idn: str | None = None
    note: str = ""


@dataclass(frozen=True)
class DiscoveryResult:
    address: str
    port: int | None
    protocol: str
    resource: str
    source: str
    status: str
    idn: str | None = None
    note: str = ""


def parse_discovery_ports(text: str | Sequence[int]) -> tuple[int, ...]:
    if isinstance(text, str):
        raw_items: Iterable[str | int] = text.split(",")
    else:
        raw_items = text
    ports: list[int] = []
    for item in raw_items:
        stripped = str(item).strip()
        if not stripped:
            continue
        try:
            port = int(stripped)
        except ValueError as exc:
            raise ConfigError(f"invalid discovery port / 发现端口无效: {stripped}") from exc
        if port < 1 or port > 65535:
            raise ConfigError(f"discovery port must be 1..65535 / 发现端口必须为 1..65535: {port}")
        if port not in ports:
            ports.append(port)
    if not ports:
        raise ConfigError("at least one discovery port is required / 至少需要一个发现端口")
    return tuple(ports)


def discover_instruments(
    *,
    subnet: str,
    ports: str | Sequence[int] = DEFAULT_DISCOVERY_PORTS,
    timeout_ms: int = 300,
    workers: int = 64,
    max_hosts: int = 256,
    query_idn: bool = True,
    idn_only: bool = False,
    include_visa: bool = True,
) -> list[DiscoveryResult]:
    parsed_ports = parse_discovery_ports(ports)
    results: list[DiscoveryResult] = []
    if include_visa:
        results.extend(discover_visa_resources(query_idn=query_idn, timeout_ms=timeout_ms))
    results.extend(
        discover_network(
            subnet,
            ports=parsed_ports,
            timeout_ms=timeout_ms,
            workers=workers,
            max_hosts=max_hosts,
            query_idn=query_idn,
            include_open=not idn_only,
        )
    )
    return _dedupe_results(results)


def discover_network(
    subnet: str,
    *,
    ports: Sequence[int] = DEFAULT_DISCOVERY_PORTS,
    timeout_ms: int = 300,
    workers: int = 64,
    max_hosts: int = 256,
    query_idn: bool = True,
    include_open: bool = True,
    tcp_probe: Callable[[str, int, float], bool] | None = None,
    scpi_probe: Callable[[str, int, float], PortProbe] | None = None,
) -> list[DiscoveryResult]:
    network = _parse_network(subnet)
    host_count = _estimated_host_count(network)
    if host_count > max_hosts:
        raise ConfigError(
            f"subnet has {host_count} hosts; raise --max-hosts to scan it / "
            f"网段包含 {host_count} 个主机，如需扫描请提高 --max-hosts"
        )
    hosts = [str(host) for host in network.hosts()]
    if not hosts and network.num_addresses == 1:
        hosts = [str(network.network_address)]
    if workers < 1:
        raise ConfigError("--workers must be >= 1")
    if timeout_ms < 1:
        raise ConfigError("--timeout-ms must be >= 1")
    parsed_ports = parse_discovery_ports(ports)
    timeout_s = timeout_ms / 1000.0
    tcp_probe = tcp_probe or _is_tcp_port_open
    scpi_probe = scpi_probe or _probe_scpi_socket
    results: list[DiscoveryResult] = []
    if not hosts:
        return results
    pool_size = min(workers, len(hosts))
    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        futures = {
            executor.submit(
                _probe_host,
                host,
                parsed_ports,
                timeout_s,
                query_idn,
                include_open,
                tcp_probe,
                scpi_probe,
            ): host
            for host in hosts
        }
        for future in as_completed(futures):
            results.extend(future.result())
    return sorted(results, key=_result_sort_key)


def discover_visa_resources(*, query_idn: bool = False, timeout_ms: int = 1000) -> list[DiscoveryResult]:
    try:
        import pyvisa  # type: ignore[import-not-found]
    except Exception:
        return []
    results: list[DiscoveryResult] = []
    seen: set[str] = set()
    for backend in (None, "@py"):
        try:
            manager = pyvisa.ResourceManager() if backend is None else pyvisa.ResourceManager(backend)
        except Exception:
            continue
        try:
            resources = tuple(str(item) for item in manager.list_resources())
            for resource in resources:
                if resource in seen:
                    continue
                seen.add(resource)
                idn = _query_visa_idn(manager, resource, timeout_ms) if query_idn else None
                results.append(
                    DiscoveryResult(
                        address=_address_from_visa_resource(resource),
                        port=_port_from_visa_resource(resource),
                        protocol="visa",
                        resource=resource,
                        source="visa",
                        status="idn" if idn else "listed",
                        idn=idn,
                    )
                )
        finally:
            try:
                manager.close()
            except Exception:
                pass
    return sorted(results, key=_result_sort_key)


def _parse_network(subnet: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    try:
        return ipaddress.ip_network(subnet, strict=False)
    except ValueError as exc:
        raise ConfigError(f"invalid subnet / 网段无效: {subnet}") from exc


def _estimated_host_count(network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> int:
    if network.prefixlen >= network.max_prefixlen - 1:
        return network.num_addresses
    return max(network.num_addresses - 2, 0)


def _probe_host(
    address: str,
    ports: Sequence[int],
    timeout_s: float,
    query_idn: bool,
    include_open: bool,
    tcp_probe: Callable[[str, int, float], bool],
    scpi_probe: Callable[[str, int, float], PortProbe],
) -> list[DiscoveryResult]:
    results: list[DiscoveryResult] = []
    for port in ports:
        if query_idn and port in SCPI_SOCKET_PORTS:
            probe = scpi_probe(address, port, timeout_s)
            if not probe.open:
                continue
            if probe.idn or include_open:
                results.append(_result_from_port_probe(address, port, probe))
            continue
        if tcp_probe(address, port, timeout_s) and include_open:
            results.append(_open_port_result(address, port))
    return results


def _probe_scpi_socket(address: str, port: int, timeout_s: float) -> PortProbe:
    resource = f"TCPIP::{address}::{port}::SOCKET"
    with ResourceLease(resource=resource, operation="discovery.probe"):
        try:
            with socket.create_connection((address, port), timeout=timeout_s) as sock:
                sock.settimeout(timeout_s)
                try:
                    sock.sendall(b"*IDN?\n")
                except OSError as exc:
                    return PortProbe(open=True, note=f"write failed: {type(exc).__name__}")
                try:
                    raw = sock.recv(4096)
                except socket.timeout:
                    return PortProbe(open=True, note="idn timeout")
                except OSError as exc:
                    return PortProbe(open=True, note=f"read failed: {type(exc).__name__}")
        except OSError:
            return PortProbe(open=False)
    idn = raw.decode("utf-8", errors="replace").strip("\x00\r\n \t")
    return PortProbe(open=True, idn=idn or None)


def _is_tcp_port_open(address: str, port: int, timeout_s: float) -> bool:
    resource = f"TCPIP::{address}::{port}::SOCKET"
    with ResourceLease(resource=resource, operation="discovery.probe"):
        try:
            with socket.create_connection((address, port), timeout=timeout_s):
                return True
        except OSError:
            return False


def _result_from_port_probe(address: str, port: int, probe: PortProbe) -> DiscoveryResult:
    return DiscoveryResult(
        address=address,
        port=port,
        protocol="scpi-socket",
        resource=f"TCPIP::{address}::{port}::SOCKET",
        source="network",
        status="idn" if probe.idn else "open",
        idn=probe.idn,
        note=probe.note,
    )


def _open_port_result(address: str, port: int) -> DiscoveryResult:
    if port == 111:
        return DiscoveryResult(
            address=address,
            port=port,
            protocol="vxi11-candidate",
            resource=f"TCPIP::{address}::INSTR",
            source="network",
            status="open",
            note="VXI-11 RPC port open; verify with idn",
        )
    return DiscoveryResult(
        address=address,
        port=port,
        protocol="tcp",
        resource=f"TCPIP::{address}::{port}::SOCKET",
        source="network",
        status="open",
    )


def _query_visa_idn(manager: object, resource: str, timeout_ms: int) -> str | None:
    with ResourceLease(resource=resource, operation="discovery.visa_idn"):
        session = None
        try:
            session = manager.open_resource(resource)  # type: ignore[attr-defined]
            try:
                session.timeout = timeout_ms
            except Exception:
                pass
            try:
                session.read_termination = "\n"
                session.write_termination = "\n"
            except Exception:
                pass
            return str(session.query("*IDN?")).strip() or None
        except ResourceBusyError:
            raise
        except Exception:
            return None
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass


def _address_from_visa_resource(resource: str) -> str:
    match = re.search(r"TCPIP\d*::([^:]+)", resource, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _port_from_visa_resource(resource: str) -> int | None:
    match = re.search(r"TCPIP\d*::[^:]+::(\d+)::", resource, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _dedupe_results(results: Sequence[DiscoveryResult]) -> list[DiscoveryResult]:
    best: dict[str, DiscoveryResult] = {}
    for result in sorted(results, key=_result_sort_key):
        existing = best.get(result.resource)
        if existing is None or _result_score(result) > _result_score(existing):
            best[result.resource] = result
    return sorted(best.values(), key=_result_sort_key)


def _result_score(result: DiscoveryResult) -> int:
    if result.idn:
        return 3
    if result.status == "listed":
        return 2
    return 1


def _result_sort_key(result: DiscoveryResult) -> tuple[int, int, str, int]:
    try:
        address_value = int(ipaddress.ip_address(result.address)) if result.address else 0
    except ValueError:
        address_value = 0
    return (address_value, result.port or 0, result.resource, -_result_score(result))
