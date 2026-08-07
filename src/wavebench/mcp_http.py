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
from wavebench.errors import ConfigError, WaveBenchError
from wavebench.logging import CommandLogger
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
                "safety_gate": plan.safety.safety_gate,
                "off_source_channels": list(plan.safety.off_source_channels),
                "off_power_channels": list(plan.safety.off_power_channels),
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
