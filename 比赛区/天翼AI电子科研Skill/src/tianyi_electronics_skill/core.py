"""无框架依赖的设备发现、SCPI 读取和基础信号分析。"""

from __future__ import annotations

import csv
import ipaddress
import json
import math
import socket
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable


COMMON_PORTS = (5025, 5555, 4000, 502)


def _scpi_query(host: str, port: int, command: str, timeout: float = 1.5) -> str:
    """通过原始 TCP SCPI 查询；不改变仪器状态。"""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall((command.rstrip() + "\n").encode("ascii", "replace"))
        chunks: list[bytes] = []
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            if b"\n" in data or sum(map(len, chunks)) >= 65536:
                break
    return b"".join(chunks).decode("utf-8", "replace").strip()


def discover_devices(subnet: str, ports: Iterable[int] = COMMON_PORTS, timeout: float = 0.35) -> list[dict]:
    """扫描 CIDR 网段并尝试读取 *IDN?，返回可序列化设备记录。"""
    network = ipaddress.ip_network(subnet, strict=False)
    targets = [(str(ip), int(port)) for ip in network.hosts() for port in ports]

    def probe(target: tuple[str, int]) -> dict | None:
        host, port = target
        try:
            ident = _scpi_query(host, port, "*IDN?", timeout)
            return {"ip": host, "port": port, "protocol": "SCPI/TCP", "idn": ident or None,
                    "reachable": True, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        except (OSError, UnicodeError):
            return None

    found: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(64, max(1, len(targets)))) as pool:
        futures = [pool.submit(probe, target) for target in targets]
        for future in as_completed(futures):
            result = future.result()
            if result:
                found.append(result)
    return sorted(found, key=lambda item: (item["ip"], item["port"]))


def inspect_device(host: str, port: int = 5025, timeout: float = 2.0) -> dict:
    """读取设备身份、错误队列及常见通道状态。"""
    result: dict = {"address": f"{host}:{port}", "transport": "SCPI/TCP", "timestamp": time.time()}
    queries = {"idn": "*IDN?", "error": "SYST:ERR?", "opc": "*OPC?"}
    for key, command in queries.items():
        try:
            result[key] = _scpi_query(host, port, command, timeout)
        except OSError as exc:
            result[key] = None
            result.setdefault("errors", []).append(f"{command}: {exc}")
    channels = {}
    for channel in ("CHAN1", "CHAN2", "CHAN3", "CHAN4"):
        try:
            channels[channel] = {"display": _scpi_query(host, port, f":{channel}:DISP?", timeout)}
        except OSError:
            continue
    result["channels"] = channels
    return result


MEASUREMENT_COMMANDS = {
    # 第一项适配 Keysight/Tektronix 风格；第二项适配 Rigol 的 ITEM 查询风格。
    "frequency_hz": ("MEASure:FREQuency? {channel}", "MEASure:ITEM? FREQuency,{channel}"),
    "vpp_v": ("MEASure:VPP? {channel}", "MEASure:ITEM? VPP,{channel}"),
    "vmax_v": ("MEASure:VMAX? {channel}", "MEASure:ITEM? VMAX,{channel}"),
    "vmin_v": ("MEASure:VMIN? {channel}", "MEASure:ITEM? VMIN,{channel}"),
    "vavg_v": ("MEASure:VAVG? {channel}", "MEASure:ITEM? VAVG,{channel}"),
    "vrms_v": ("MEASure:VRMS? {channel}", "MEASure:ITEM? VRMS,{channel}"),
}


def measure_scope(host: str, port: int = 5025, channel: str = "CHAN1", timeout: float = 2.0) -> dict:
    """读取常见示波器测量值，无法支持的项目保留错误而不中断其它指标。"""
    result: dict = {"address": f"{host}:{port}", "channel": channel, "timestamp": time.time(), "measurements": {}}
    for name, templates in MEASUREMENT_COMMANDS.items():
        failures = []
        for template in templates:
            command = template.format(channel=channel)
            try:
                raw = _scpi_query(host, port, command, timeout)
                result["measurements"][name] = float(raw.split(",")[0])
                break
            except (OSError, ValueError) as exc:
                failures.append(f"{command}: {exc}")
        else:
            result.setdefault("unsupported_or_errors", {})[name] = "; ".join(failures)
    return result


def analyze_samples(samples: Iterable[float], sample_rate_hz: float) -> dict:
    """从等间隔波形估计峰值、峰峰值、RMS、频率和占空比。"""
    values = [float(value) for value in samples]
    if len(values) < 4:
        raise ValueError("至少需要 4 个采样点")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz 必须大于 0")
    peak = max(values, key=abs)
    mean = statistics.fmean(values)
    centered = [value - mean for value in values]
    crossings = [index for index in range(1, len(centered)) if centered[index - 1] <= 0 < centered[index]]
    frequency = None
    if len(crossings) >= 2:
        periods = [b - a for a, b in zip(crossings, crossings[1:])]
        frequency = sample_rate_hz / statistics.fmean(periods)
    high = sum(1 for value in centered if value >= 0) / len(centered)
    return {
        "samples": len(values), "sample_rate_hz": sample_rate_hz,
        "peak_v": peak, "peak_to_peak_v": max(values) - min(values),
        "minimum_v": min(values), "maximum_v": max(values), "mean_v": mean,
        "rms_v": math.sqrt(statistics.fmean(value * value for value in centered)),
        "frequency_hz": frequency, "duty_cycle_percent": high * 100,
    }


def load_waveform(path: str | Path) -> tuple[list[float], float]:
    """读取 CSV（time,value 或单列 value）/JSON（samples 与 sample_rate_hz）。"""
    file_path = Path(path)
    if file_path.suffix.lower() == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        return [float(v) for v in payload["samples"]], float(payload["sample_rate_hz"])
    with file_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    values = []
    for row in rows:
        if not row:
            continue
        try:
            values.append(float(row[-1]))
        except ValueError:
            continue
    return values, 1.0


def demo_waveform(sample_rate_hz: float = 100_000, frequency_hz: float = 1_000, seconds: float = 0.01) -> dict:
    count = max(4, int(sample_rate_hz * seconds))
    samples = [0.8 * math.sin(2 * math.pi * frequency_hz * i / sample_rate_hz) + 0.02 for i in range(count)]
    return {"source": "built-in-demo", **analyze_samples(samples, sample_rate_hz)}
