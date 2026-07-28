from __future__ import annotations

import json
import os
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from hmac import compare_digest
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from wavebench.config import load_config
from wavebench import __version__
from wavebench.data.packages import load_capture_package
from wavebench.doctor import doctor_records, has_doctor_errors
from wavebench.errors import ConfigError, WaveBenchError
from wavebench.logging import CommandLogger
from wavebench.services.agent_advise import scope_advise_payload
from wavebench.services.agent_observe import scope_observe_payload
from wavebench.services.run_plan import load_run_plan, run_plan_schema_rows
from wavebench.services.run_plan import format_run_plan_schema
from wavebench.services.run_service import RunService


DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8765
DEFAULT_MCP_TOKEN_ENV = "WAVEBENCH_MCP_TOKEN"
MAX_MCP_BODY_BYTES = 1_048_576

_MCP_RUN_PLAN_DIR = Path("plans")
_MCP_CAPTURE_DIR = Path("data/raw")

_SENSITIVE_PATH_EXACT = {
    ".aws",
    ".github",
    ".git-credentials",
    ".netrc",
    ".ssh",
    "credentials",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "known_hosts",
}
_SENSITIVE_PATH_FRAGMENTS = ("password", "passwd", "secret", "token")
_SENSITIVE_PATH_SUFFIXES = (".key", ".pem")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: dict[str, Any]
    handler: Callable[[dict[str, Any], Path], dict[str, Any]]

    def public_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "read_only": True,
            "arguments": self.arguments,
        }


def validate_mcp_host(host: str) -> str:
    normalized = host.strip()
    if not normalized:
        raise ConfigError("MCP host is required / MCP host 不能为空")
    if normalized in {"0.0.0.0", "::", "[::]"}:
        raise ConfigError(
            "MCP host 0.0.0.0 is refused / MCP 拒绝监听 0.0.0.0；请显式使用 127.0.0.1 或私网地址"
        )
    return normalized


def resolve_mcp_token(token: str | None, token_env: str | None = DEFAULT_MCP_TOKEN_ENV) -> str:
    value = token
    if value is None and token_env:
        value = os.environ.get(token_env)
    if value is None or value == "":
        raise ConfigError(
            "MCP token is required; pass --token or set --token-env / "
            "MCP token 必填；请传 --token 或设置 --token-env"
        )
    return value


def _reject_sensitive_path(path: str | Path, *, label: str) -> Path:
    candidate = Path(path)
    lower_parts = [part.lower() for part in candidate.parts]
    lowered = str(candidate).lower()
    if any(part in _SENSITIVE_PATH_EXACT for part in lower_parts):
        raise ConfigError(f"{label} points to a sensitive path / {label} 指向敏感路径")
    if any(fragment in lowered for fragment in _SENSITIVE_PATH_FRAGMENTS):
        raise ConfigError(f"{label} points to a sensitive path / {label} 指向敏感路径")
    if candidate.suffix.lower() in _SENSITIVE_PATH_SUFFIXES:
        raise ConfigError(f"{label} points to a sensitive path / {label} 指向敏感路径")
    return candidate


def _project_root_from_config_path(config_path: str | Path) -> Path:
    candidate = Path(config_path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.parent.resolve(strict=False)


def _resolve_allowed_tool_path(
    path: str | Path,
    *,
    config_path: str | Path,
    allowed_subdir: Path,
    label: str,
    allowed_suffixes: set[str] | None = None,
) -> Path:
    project_root = _project_root_from_config_path(config_path)
    candidate = _reject_sensitive_path(path, label=label)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    try:
        allowed_root = (project_root / allowed_subdir).resolve(strict=False)
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ConfigError(f"{label} path cannot be resolved / {label} 路径无法解析: {exc}") from exc
    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise ConfigError(
            f"{label} must be under {allowed_subdir.as_posix()} / "
            f"{label} 必须位于 {allowed_subdir.as_posix()} 下"
        )
    if allowed_suffixes is not None and resolved.suffix.lower() not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise ConfigError(f"{label} must use suffix {allowed} / {label} 后缀必须为 {allowed}")
    return resolved


def _require_arguments(arguments: dict[str, Any], *names: str) -> None:
    missing = [name for name in names if name not in arguments or arguments[name] in (None, "")]
    if missing:
        raise ConfigError(
            "missing required argument(s) / 缺少必填参数: " + ", ".join(missing)
        )


def _run_schema_tool(arguments: dict[str, Any], config_path: Path) -> dict[str, Any]:
    return {
        "schema_text": format_run_plan_schema(),
        "schema": run_plan_schema_rows(),
    }


def _run_check_tool(arguments: dict[str, Any], config_path: Path) -> dict[str, Any]:
    _require_arguments(arguments, "plan")
    plan_path = _resolve_allowed_tool_path(
        arguments["plan"],
        config_path=config_path,
        allowed_subdir=_MCP_RUN_PLAN_DIR,
        allowed_suffixes={".toml"},
        label="plan",
    )
    safe_config_path = _reject_sensitive_path(config_path, label="config")
    plan = load_run_plan(plan_path)
    config = load_config(safe_config_path)
    RunService(config=config, logger=CommandLogger()).check(plan)
    return {
        "status": "ok",
        "message": "run plan check passed / run plan 检查通过",
        "plan": {
            "path": str(plan.path),
            "name": plan.name,
            "label": plan.label,
            "steps": [
                {"index": step.index, "kind": step.kind, "fields": step.fields}
                for step in plan.steps
            ],
            "safety": {
                "scope_guard_channel": plan.safety.scope_guard_channel,
                "require_scope_coupling_not": list(plan.safety.require_scope_coupling_not),
                "allow_50ohm": plan.safety.allow_50ohm,
            },
            "restore": {
                "source_state": plan.restore.source_state,
                "source_channels": list(plan.restore.source_channels),
            },
        },
    }


def _capture_inspect_tool(arguments: dict[str, Any], config_path: Path) -> dict[str, Any]:
    _require_arguments(arguments, "path")
    package_path = _resolve_allowed_tool_path(
        arguments["path"],
        config_path=config_path,
        allowed_subdir=_MCP_CAPTURE_DIR,
        label="path",
    )
    package = load_capture_package(package_path)
    return {
        "path": str(package.path),
        "metadata": str(package.metadata_path),
        "operation": package.operation,
        "instrument": package.instrument,
        "channels": [
            {
                "channel": channel.channel,
                "summary": channel.summary,
                "files": channel.files,
            }
            for channel in package.channels
        ],
    }


def _optional_bool(arguments: dict[str, Any], name: str, default: bool) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean / {name} 必须是布尔值")
    return value


def _scope_observe_tool(arguments: dict[str, Any], config_path: Path) -> dict[str, Any]:
    channel = arguments.get("channel")
    if channel is not None and (isinstance(channel, bool) or not isinstance(channel, int)):
        raise ConfigError("channel must be an integer / channel 必须是整数")
    raw_channels = arguments.get("channels")
    channels = None
    if raw_channels is not None:
        if not isinstance(raw_channels, list):
            raise ConfigError("channels must be an array / channels 必须是数组")
        channels = tuple(raw_channels)
        if any(isinstance(item, bool) or not isinstance(item, int) for item in channels):
            raise ConfigError("channels must contain integers / channels 必须包含整数")
    expectations = _scope_expectations_argument(arguments.get("expectations"))
    return scope_observe_payload(
        config_path=_reject_sensitive_path(config_path, label="config"),
        channel=channel,
        channels=channels,
        fetch_waveform=_optional_bool(arguments, "fetch_waveform", False),
        allow_50ohm=_optional_bool(arguments, "allow_50ohm", False),
        expectations=expectations,
    )


def _scope_advise_tool(arguments: dict[str, Any], config_path: Path) -> dict[str, Any]:
    channel, channels = _scope_channel_arguments(arguments)
    expectations = _scope_expectations_argument(arguments.get("expectations"))
    return scope_advise_payload(
        config_path=_reject_sensitive_path(config_path, label="config"),
        channel=channel,
        channels=channels,
        fetch_waveform=_optional_bool(arguments, "fetch_waveform", False),
        allow_50ohm=_optional_bool(arguments, "allow_50ohm", False),
        expectations=expectations,
        target_cycles=_optional_positive_number(arguments, "target_cycles", 10.0),
        target_vertical_divisions=_optional_positive_number(
            arguments,
            "target_vertical_divisions",
            5.0,
        ),
    )


def _scope_channel_arguments(arguments: dict[str, Any]) -> tuple[int | None, tuple[int, ...] | None]:
    channel = arguments.get("channel")
    if channel is not None and (isinstance(channel, bool) or not isinstance(channel, int)):
        raise ConfigError("channel must be an integer / channel 必须是整数")
    raw_channels = arguments.get("channels")
    channels = None
    if raw_channels is not None:
        if not isinstance(raw_channels, list):
            raise ConfigError("channels must be an array / channels 必须是数组")
        channels = tuple(raw_channels)
        if any(isinstance(item, bool) or not isinstance(item, int) for item in channels):
            raise ConfigError("channels must contain integers / channels 必须包含整数")
    return channel, channels


def _optional_positive_number(arguments: dict[str, Any], name: str, default: float) -> float:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"{name} must be a positive number / {name} 必须是正数")
    return float(value)


def _scope_expectations_argument(raw: Any) -> dict[int, dict[str, Any]] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("expectations must be an object / expectations 必须是对象")
    parsed: dict[int, dict[str, Any]] = {}
    for key, value in raw.items():
        try:
            channel = int(key)
        except (TypeError, ValueError) as exc:
            raise ConfigError("expectations keys must be channel numbers / expectations 键必须是通道号") from exc
        if channel < 1:
            raise ConfigError("expectations channel must be >= 1 / expectations 通道必须 >= 1")
        if not isinstance(value, dict):
            raise ConfigError("expectations entries must be objects / expectations 条目必须是对象")
        parsed[channel] = dict(value)
    return parsed


def _doctor_config_tool(arguments: dict[str, Any], config_path: Path) -> dict[str, Any]:
    timeout_ms = arguments.get("timeout_ms")
    if timeout_ms is not None and (isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0):
        raise ConfigError("timeout_ms must be a positive integer / timeout_ms 必须是正整数")
    records = doctor_records(
        load_config(_reject_sensitive_path(config_path, label="config")),
        timeout_ms=timeout_ms,
        include_visa=False,
    )
    return {
        "status": "error" if has_doctor_errors(records) else "ok",
        "read_only": True,
        "mutates_instrument": False,
        "raw_scpi": False,
        "records": [
            {
                "severity": record.severity,
                "target": record.target,
                "driver": record.driver,
                "resource": record.resource,
                "idn": record.idn,
                "message": record.message,
                "suggestion": record.suggestion,
            }
            for record in records
        ],
    }


READ_ONLY_TOOLS: dict[str, ToolSpec] = {
    "run.schema": ToolSpec(
        name="run.schema",
        description="Return run plan schema / 返回 run plan schema",
        arguments={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_run_schema_tool,
    ),
    "run.check": ToolSpec(
        name="run.check",
        description=(
            "Parse and validate a run plan without connecting to instruments / "
            "只解析并检查 run plan，不连接仪器"
        ),
        arguments={
            "type": "object",
            "required": ["plan"],
            "properties": {"plan": {"type": "string"}},
            "additionalProperties": False,
        },
        handler=_run_check_tool,
    ),
    "capture.inspect": ToolSpec(
        name="capture.inspect",
        description="Inspect an offline capture package / 读取离线采集包摘要",
        arguments={
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
        handler=_capture_inspect_tool,
    ),
    "scope.observe": ToolSpec(
        name="scope.observe",
        description=(
            "Read configured scope identity, state, coupling safety, and waveform summary without "
            "changing instrument state / 只读观察配置中的示波器身份、状态、高阻安全与波形摘要"
        ),
        arguments={
            "type": "object",
            "properties": {
                "channel": {"type": "integer", "minimum": 1},
                "channels": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "minItems": 1,
                    "uniqueItems": True,
                },
                "fetch_waveform": {"type": "boolean", "default": False},
                "allow_50ohm": {"type": "boolean", "default": False},
                "expectations": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "shape": {"type": "string"},
                            "frequency_hz": {"type": "number", "exclusiveMinimum": 0},
                            "frequency_tolerance_ratio": {"type": "number", "minimum": 0},
                            "vpp_v": {"type": "number", "exclusiveMinimum": 0},
                            "vpp_tolerance_ratio": {"type": "number", "minimum": 0},
                            "mean_v": {"type": "number"},
                            "offset_v": {"type": "number"},
                            "mean_tolerance_v": {"type": "number", "minimum": 0},
                            "duty_cycle": {"type": "number", "minimum": 0, "maximum": 1},
                            "duty_percent": {"type": "number", "minimum": 0, "maximum": 100},
                            "duty_tolerance": {"type": "number", "minimum": 0},
                            "symmetry_percent": {"type": "number", "minimum": 0, "maximum": 100},
                            "symmetry_tolerance_percent": {"type": "number", "minimum": 0},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
        handler=_scope_observe_tool,
    ),
    "doctor.config": ToolSpec(
        name="doctor.config",
        description=(
            "Run configured-instrument read-only doctor checks and return structured records / "
            "对已配置仪器执行只读 doctor 检查并返回结构化结果"
        ),
        arguments={
            "type": "object",
            "properties": {
                "timeout_ms": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
        handler=_doctor_config_tool,
    ),
    "scope.advise": ToolSpec(
        name="scope.advise",
        description=(
            "Observe the configured scope and recommend display/acquisition settings without applying them / "
            "观察配置中的示波器并建议显示/采集参数，但不应用建议"
        ),
        arguments={
            "type": "object",
            "properties": {
                "channel": {"type": "integer", "minimum": 1},
                "channels": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "minItems": 1,
                    "uniqueItems": True,
                },
                "fetch_waveform": {"type": "boolean", "default": False},
                "allow_50ohm": {"type": "boolean", "default": False},
                "target_cycles": {"type": "number", "exclusiveMinimum": 0, "default": 10},
                "target_vertical_divisions": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "default": 5,
                },
                "expectations": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "shape": {"type": "string"},
                            "frequency_hz": {"type": "number", "exclusiveMinimum": 0},
                            "frequency_tolerance_ratio": {"type": "number", "minimum": 0},
                            "vpp_v": {"type": "number", "exclusiveMinimum": 0},
                            "vpp_tolerance_ratio": {"type": "number", "minimum": 0},
                            "mean_v": {"type": "number"},
                            "offset_v": {"type": "number"},
                            "mean_tolerance_v": {"type": "number", "minimum": 0},
                            "duty_cycle": {"type": "number", "minimum": 0, "maximum": 1},
                            "duty_percent": {"type": "number", "minimum": 0, "maximum": 100},
                            "duty_tolerance": {"type": "number", "minimum": 0},
                            "symmetry_percent": {"type": "number", "minimum": 0, "maximum": 100},
                            "symmetry_tolerance_percent": {"type": "number", "minimum": 0},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
        handler=_scope_advise_tool,
    ),
}


class McpHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        token: str,
        config_path: str | Path,
    ) -> None:
        self.wavebench_token = token
        self.wavebench_config_path = Path(config_path)
        super().__init__(server_address, McpHttpHandler)


class McpHttpHandler(BaseHTTPRequestHandler):
    server: McpHttpServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._write_json(
                200,
                {
                    "status": "ok",
                    "service": "wavebench-mcp-http",
                    "read_only": True,
                },
            )
            return
        if path == "/tools":
            if not self._require_auth():
                return
            self._write_json(
                200,
                {"tools": [tool.public_payload() for tool in READ_ONLY_TOOLS.values()]},
            )
            return
        self._write_error(404, "not found / 未找到")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/call":
            self._handle_legacy_call()
            return
        if path == "/mcp":
            self._handle_mcp_jsonrpc()
            return
        self._write_error(404, "not found / 未找到")

    def _handle_legacy_call(self) -> None:
        if not self._require_auth():
            return
        try:
            payload = self._read_json_object()
            tool_name = str(payload.get("tool", "")).strip()
            if tool_name not in READ_ONLY_TOOLS:
                self._write_error(404, f"unknown tool / 未知工具: {tool_name}")
                return
            arguments = payload.get("arguments", {})
            result = self._call_tool(tool_name, arguments)
        except WaveBenchError as exc:
            status = exc.exit_code if exc.exit_code in {400, 401, 403, 404} else 400
            self._write_error(status, str(exc), type(exc).__name__)
            return
        except Exception as exc:
            self._write_error(500, str(exc), type(exc).__name__)
            return
        self._write_json(200, {"result": result})

    def _handle_mcp_jsonrpc(self) -> None:
        if not self._require_auth():
            return
        request_id: Any = None
        try:
            request = self._read_json_object()
            request_id = request.get("id")
            method = str(request.get("method", "")).strip()
            params = request.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise ConfigError("params must be an object / params 必须是对象")
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "wavebench", "version": __version__},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.arguments,
                        }
                        for tool in READ_ONLY_TOOLS.values()
                    ]
                }
            elif method == "tools/call":
                tool_name = str(params.get("name", "")).strip()
                arguments = params.get("arguments", {})
                tool_result = self._call_tool(tool_name, arguments)
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(tool_result, ensure_ascii=False, sort_keys=True),
                        }
                    ],
                    "structuredContent": tool_result,
                }
            elif method == "notifications/initialized":
                if request_id is None:
                    self._write_empty(204)
                    return
                result = {}
            else:
                self._write_jsonrpc_error(request_id, -32601, f"method not found / 未知方法: {method}")
                return
        except WaveBenchError as exc:
            self._write_jsonrpc_error(request_id, -32602, str(exc), type(exc).__name__)
            return
        except Exception as exc:
            self._write_jsonrpc_error(request_id, -32603, str(exc), type(exc).__name__)
            return
        if request_id is None:
            self._write_empty(204)
            return
        self._write_json(200, {"jsonrpc": "2.0", "id": request_id, "result": result})

    def _call_tool(self, tool_name: str, arguments: Any) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ConfigError("arguments must be an object / arguments 必须是对象")
        tool = READ_ONLY_TOOLS.get(tool_name)
        if tool is None:
            raise ConfigError(f"unknown tool / 未知工具: {tool_name}")
        return tool.handler(arguments, self.server.wavebench_config_path)

    def _require_auth(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        supplied = header[len(prefix):] if header.startswith(prefix) else ""
        if not supplied or not compare_digest(supplied, self.server.wavebench_token):
            self._write_error(401, "authentication required / 需要认证")
            return False
        return True

    def _read_json_object(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError as exc:
            raise ConfigError("invalid Content-Length / Content-Length 无效") from exc
        if length < 0:
            raise ConfigError("invalid Content-Length / Content-Length 无效")
        if length > MAX_MCP_BODY_BYTES:
            raise ConfigError(
                f"JSON body too large; max {MAX_MCP_BODY_BYTES} bytes / "
                f"JSON 请求体过大；最大 {MAX_MCP_BODY_BYTES} 字节"
            )
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSON body / JSON 请求体无效: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigError("JSON body must be an object / JSON 请求体必须是对象")
        return payload

    def _write_error(self, status: int, message: str, error_type: str = "Error") -> None:
        self._write_json(status, {"error": {"type": error_type, "message": message}})

    def _write_jsonrpc_error(
        self,
        request_id: Any,
        code: int,
        message: str,
        error_type: str = "Error",
    ) -> None:
        self._write_json(
            200,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": code,
                    "message": message,
                    "data": {"type": error_type},
                },
            },
        )

    def _write_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_mcp_http_server(
    *,
    host: str = DEFAULT_MCP_HOST,
    port: int = DEFAULT_MCP_PORT,
    token: str,
    config_path: str | Path = "wavebench.toml",
) -> McpHttpServer:
    safe_host = validate_mcp_host(host)
    if port < 0 or port > 65535:
        raise ConfigError("MCP port must be 0..65535 / MCP port 必须在 0..65535 之间")
    safe_config_path = _reject_sensitive_path(config_path, label="config")
    return McpHttpServer((safe_host, port), token=token, config_path=safe_config_path)


def serve_mcp_http(
    *,
    host: str = DEFAULT_MCP_HOST,
    port: int = DEFAULT_MCP_PORT,
    token: str,
    config_path: str | Path = "wavebench.toml",
) -> None:
    server = make_mcp_http_server(host=host, port=port, token=token, config_path=config_path)
    actual_host, actual_port = server.server_address[:2]
    print(
        f"MCP HTTP listening / MCP HTTP 正在监听: http://{actual_host}:{actual_port} "
        "(read-only / 只读)"
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
