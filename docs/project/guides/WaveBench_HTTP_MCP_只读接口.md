# WaveBench HTTP MCP 只读接口

HTTP MCP 当前是只读 MVP，用于向本机或受控私网客户端提供 WaveBench 的离线信息和 run plan 检查结果。

## 启动

```powershell
python -m wavebench mcp serve --config wavebench.toml --token-env WAVEBENCH_MCP_TOKEN
```

`--host` 默认值为 `127.0.0.1`，`--port` 默认值为 `8765`。显式传入 `--host 0.0.0.0` 时，WaveBench 会拒绝启动。

认证 token 必须通过 `--token` 或 `--token-env` 提供。公开示例只展示环境变量名，不展示 token 值。

## 端点

- `GET /health`：健康检查，不需要 token。
- `POST /mcp`：MCP JSON-RPC 入口，支持 `initialize`、`tools/list`、`tools/call`，需要 Bearer token。
- `GET /tools`：返回当前只读工具列表，需要 Bearer token。
- `POST /call`：调用只读工具，需要 Bearer token，请求体为 JSON 对象。

`/mcp` 和 `/call` 的 JSON 请求体上限为 1 MiB。

`/call` 请求体格式：

```json
{
  "tool": "run.schema",
  "arguments": {}
}
```

`/mcp` 请求体格式：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "run.schema",
    "arguments": {}
  }
}
```

MCP notification 请求没有 `id` 时返回空响应；普通请求返回 JSON-RPC `result` 或 `error`。

## 工具

- `run.schema`：返回 run plan schema 文本和结构化 schema 行。
- `run.check`：参数 `{"plan": "plans/<name>.toml"}`，只解析并检查 `plans/*.toml` 下的 run plan，不连接仪器。
- `capture.inspect`：参数 `{"path": "data/raw/<capture_dir>"}`，读取 `data/raw/` 下的离线采集包摘要。

## 安全边界

- 默认只监听 `127.0.0.1`。
- 拒绝监听 `0.0.0.0`。
- `/mcp`、`/tools` 和 `/call` 强制 Bearer token。
- 当前工具全部只读。
- 不提供 raw SCPI。
- 不提供 power/source output on/off。
- 不提供 run 执行工具。
- `run.check` 只允许项目内 `plans/*.toml`。
- `capture.inspect` 只允许项目内 `data/raw/` 离线采集包。
- `/mcp` 和 `/call` 的 JSON 请求体有 1 MiB 上限。
