"""可插拔协议识别与解析。

默认适配器不假定某个厂商。厂商自定义协议可通过 JSON manifest 注册，避免修改核心代码，
适合 Arm Linux 上以文件、串口、CAN 或 TCP 采集后交给 Agent 解析。
"""

from __future__ import annotations

import base64
import json
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class NormalizedFrame:
    protocol: str
    source: str
    timestamp: float
    samples: list[float] = field(default_factory=list)
    sample_rate_hz: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_size: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "source": self.source,
            "timestamp": self.timestamp,
            "samples": self.samples,
            "sample_rate_hz": self.sample_rate_hz,
            "metadata": self.metadata,
            "raw_size": self.raw_size,
        }


class ProtocolAdapter:
    name = "unknown"

    def match(self, payload: bytes | dict[str, Any], metadata: dict[str, Any] | None = None) -> float:
        return 0.0

    def decode(self, payload: bytes | dict[str, Any], source: str = "unknown", metadata: dict[str, Any] | None = None) -> NormalizedFrame:
        raise NotImplementedError


class JSONWaveformAdapter(ProtocolAdapter):
    name = "json-waveform"

    def match(self, payload: bytes | dict[str, Any], metadata: dict[str, Any] | None = None) -> float:
        if isinstance(payload, dict) and "samples" in payload:
            return 0.99
        if isinstance(payload, bytes):
            try:
                value = json.loads(payload.decode("utf-8"))
                return 0.99 if isinstance(value, dict) and "samples" in value else 0.0
            except (UnicodeDecodeError, json.JSONDecodeError):
                return 0.0
        return 0.0

    def decode(self, payload: bytes | dict[str, Any], source: str = "json", metadata: dict[str, Any] | None = None) -> NormalizedFrame:
        value = payload if isinstance(payload, dict) else json.loads(payload.decode("utf-8"))
        samples = [float(item) for item in value["samples"]]
        rate = value.get("sample_rate_hz") or value.get("sample_rate")
        return NormalizedFrame(self.name, source, time.time(), samples, float(rate) if rate else None,
                               {**(metadata or {}), **value.get("metadata", {})}, len(samples))


class SCPITextAdapter(ProtocolAdapter):
    name = "scpi-text"

    def match(self, payload: bytes | dict[str, Any], metadata: dict[str, Any] | None = None) -> float:
        if not isinstance(payload, bytes):
            return 0.0
        text = payload.decode("utf-8", "ignore").strip()
        if "*IDN?" in text or "MEAS" in text.upper():
            return 0.92
        try:
            values = [float(item.strip()) for item in text.replace(";", ",").split(",") if item.strip()]
            return 0.65 if len(values) >= 2 else 0.0
        except ValueError:
            return 0.0

    def decode(self, payload: bytes | dict[str, Any], source: str = "scpi", metadata: dict[str, Any] | None = None) -> NormalizedFrame:
        if not isinstance(payload, bytes):
            raise TypeError("SCPI 适配器需要 bytes")
        text = payload.decode("utf-8", "replace").strip()
        values = []
        for item in text.replace(";", ",").split(","):
            try:
                values.append(float(item.strip()))
            except ValueError:
                continue
        return NormalizedFrame(self.name, source, time.time(), values, None,
                               {**(metadata or {}), "text": text}, len(payload))


class CANFrameAdapter(ProtocolAdapter):
    """解析常见 CAN JSON/文本记录；真正的 SocketCAN 读取由采集端完成。"""

    name = "can"

    def match(self, payload: bytes | dict[str, Any], metadata: dict[str, Any] | None = None) -> float:
        if isinstance(payload, dict) and ("can_id" in payload or "arbitration_id" in payload):
            return 1.0
        if metadata and str(metadata.get("transport", "")).lower() in {"can", "socketcan", "canfd"}:
            return 0.9
        return 0.0

    def decode(self, payload: bytes | dict[str, Any], source: str = "can", metadata: dict[str, Any] | None = None) -> NormalizedFrame:
        if isinstance(payload, bytes):
            try:
                payload = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("CAN 原始帧请先转换为 JSON: {can_id, data}") from exc
        can_id = payload.get("can_id", payload.get("arbitration_id"))
        raw_data = payload.get("data", [])
        if isinstance(raw_data, str):
            raw_data = bytes.fromhex(raw_data.replace("0x", "")).hex()
            byte_values = [int(raw_data[index:index + 2], 16) for index in range(0, len(raw_data), 2)]
        else:
            byte_values = [int(value) & 0xFF for value in raw_data]
        samples = [float(value) for value in payload.get("samples", byte_values)]
        return NormalizedFrame(self.name, source, time.time(), samples,
                               float(payload["sample_rate_hz"]) if payload.get("sample_rate_hz") else None,
                               {**(metadata or {}), "can_id": can_id, "data_hex": bytes(byte_values).hex()}, len(byte_values))


class DeclarativeBinaryAdapter(ProtocolAdapter):
    """由 manifest 描述的固定头 + 定长数值数组协议。"""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.name = str(config["name"])
        match = config.get("match", {})
        self.magic = bytes.fromhex(str(match.get("magic_hex", ""))) if match.get("magic_hex") else b""
        self.prefix = str(match.get("prefix", "")).encode()
        payload = config.get("payload", {})
        self.sample_type = str(payload.get("sample_type", "f32"))
        self.endian = ">" if str(payload.get("endian", "big")).lower() == "big" else "<"
        self.scale = float(payload.get("scale", 1.0))
        self.offset = float(payload.get("offset", 0.0))
        self.sample_rate_hz = payload.get("sample_rate_hz")

    def match(self, payload: bytes | dict[str, Any], metadata: dict[str, Any] | None = None) -> float:
        if not isinstance(payload, bytes):
            return 0.0
        if self.magic and payload.startswith(self.magic):
            return 0.97
        if self.prefix and payload.startswith(self.prefix):
            return 0.94
        return 0.0

    def decode(self, payload: bytes | dict[str, Any], source: str = "binary", metadata: dict[str, Any] | None = None) -> NormalizedFrame:
        if not isinstance(payload, bytes):
            raise TypeError("二进制适配器需要 bytes")
        header_len = int(self.config.get("payload", {}).get("header_bytes", len(self.magic)))
        formats = {"f32": "f", "f64": "d", "i16": "h", "u16": "H", "i32": "i", "u32": "I"}
        if self.sample_type not in formats:
            raise ValueError(f"不支持的 sample_type: {self.sample_type}")
        item_size = struct.calcsize(formats[self.sample_type])
        body = payload[header_len:]
        usable = len(body) - (len(body) % item_size)
        values = [self.offset + self.scale * value for value in struct.unpack(
            f"{self.endian}{usable // item_size}{formats[self.sample_type]}", body[:usable])]
        return NormalizedFrame(self.name, source, time.time(), values,
                               float(self.sample_rate_hz) if self.sample_rate_hz else None,
                               {**(metadata or {}), "header_bytes": header_len}, len(payload))


class ProtocolRegistry:
    def __init__(self, adapters: Iterable[ProtocolAdapter] | None = None):
        self.adapters = list(adapters or [JSONWaveformAdapter(), SCPITextAdapter(), CANFrameAdapter()])

    def load_manifests(self, directory: str | Path) -> int:
        count = 0
        for path in sorted(Path(directory).glob("*.json")):
            config = json.loads(path.read_text(encoding="utf-8"))
            if config.get("kind") == "binary-waveform" and config.get("name"):
                self.adapters.append(DeclarativeBinaryAdapter(config))
                count += 1
        return count

    def identify(self, payload: bytes | dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        scores = [{"protocol": adapter.name, "confidence": adapter.match(payload, metadata)} for adapter in self.adapters]
        scores.sort(key=lambda item: item["confidence"], reverse=True)
        selected = scores[0] if scores and scores[0]["confidence"] > 0 else None
        return {"selected": selected, "candidates": scores}

    def decode(self, payload: bytes | dict[str, Any], source: str = "unknown", metadata: dict[str, Any] | None = None) -> NormalizedFrame:
        ranked = sorted(self.adapters, key=lambda item: item.match(payload, metadata), reverse=True)
        if not ranked or ranked[0].match(payload, metadata) <= 0:
            raise ValueError("没有匹配的协议适配器，请提供 protocol manifest")
        return ranked[0].decode(payload, source, metadata)


def read_payload(path: str | Path) -> bytes | dict[str, Any]:
    file_path = Path(path)
    raw = file_path.read_bytes()
    if file_path.suffix.lower() == ".json":
        return json.loads(raw.decode("utf-8"))
    if file_path.suffix.lower() in {".b64", ".base64"}:
        return base64.b64decode(raw)
    return raw


def capture_socketcan(channel: str = "can0", count: int = 1, timeout: float = 1.0) -> list[dict[str, Any]]:
    """在 Arm Linux/SocketCAN 上读取若干帧，python-can 为可选依赖。"""
    try:
        import can  # type: ignore
    except ImportError as exc:
        raise RuntimeError("SocketCAN 需要安装可选依赖: pip install python-can") from exc
    bus = can.Bus(interface="socketcan", channel=channel)
    frames: list[dict[str, Any]] = []
    try:
        while len(frames) < count:
            message = bus.recv(timeout=timeout)
            if message is None:
                break
            frames.append({
                "can_id": int(message.arbitration_id),
                "data": list(message.data),
                "is_extended_id": bool(message.is_extended_id),
                "timestamp": float(message.timestamp),
            })
    finally:
        bus.shutdown()
    return frames
