# 启动只读 MCP 服务

WaveBench HTTP MCP 服务提供本机或受控网络中的离线信息和 run plan 检查。它不提供 raw SCPI、输出控制或 run 执行。

## 启动服务

```bash
python -m wavebench mcp serve \
  --config wavebench.toml \
  --token-env WAVEBENCH_MCP_TOKEN
```

服务默认监听 `127.0.0.1:8765`。认证 token 必须通过 `--token` 或 `--token-env` 提供；不要把 token 值写入配置、命令历史或公开文档。服务拒绝监听 `0.0.0.0` 或 `::`。

## 端点与工具

| 入口 | 认证 | 行为 |
| --- | --- | --- |
| `GET /health` | 不需要 | 服务健康检查。 |
| `GET /tools` | Bearer token | 列出只读工具。 |
| `POST /call`、`POST /mcp` | Bearer token | 调用 MCP 工具或 JSON-RPC 方法。 |

请求体上限为 1 MiB。当前工具仅包含 `run.schema`、`run.check` 和 `capture.inspect`；它们不会连接仪器。`run.check` 只接受项目内的 `plans/*.toml`，`capture.inspect` 只读取项目内的离线采集包。

## Verification

访问 `GET /health` 确认服务已启动。需要调用受保护端点时，使用 Bearer token；认证失败或路径不在允许范围内时，先检查调用方配置，不要放宽监听地址或删除认证。
