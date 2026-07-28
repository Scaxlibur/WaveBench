# WaveBench HTTP MCP 只读接口

HTTP MCP 当前是只读 MVP，用于让本机或私网内客户端读取 WaveBench 的离线信息和 run plan 检查结果。

## 启动

```powershell
python -m wavebench mcp serve --config wavebench.toml --token-env WAVEBENCH_MCP_TOKEN
```

`--host` 默认是 `127.0.0.1`，`--port` 默认是 `8765`。如果显式传入 `--host 0.0.0.0`，WaveBench 会拒绝启动。

认证 token 必须通过 `--token` 或 `--token-env` 提供。公开示例只展示环境变量名，不展示 token 值。

## Endpoints

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

## Tools

- `run.schema`：返回 run plan schema 文本和结构化 schema 行。
- `run.check`：参数 `{"plan": "plans/<name>.toml"}`，只解析并检查 `plans/*.toml` 下的 run plan，不连接仪器。
- `capture.inspect`：参数 `{"path": "data/raw/<capture_dir>"}`，读取 `data/raw/` 下的离线采集包摘要。
- `scope.observe`：参数 `{"channels": [1, 2], "fetch_waveform": false}`，只读连接配置中的示波器，返回 IDN、状态快照（若驱动支持）和高阻安全判断。支持 CH1-CH4 多通道观察。显式传 `fetch_waveform=true` 时，会读取一个或多个通道的当前波形摘要；当至少两个通道读取成功时，返回 `relationships`，包含频率比、Vpp/均值关系、公共时间窗、归一化相关性、估计延迟、同频相位和通道间交点。该工具不保存文件，不暴露 raw SCPI，但抓波形可能改动示波器的波形传输源/模式。
  - 可选 `expectations`：例如 `{"1": {"frequency_hz": 1000, "vpp_v": 1.0, "duty_percent": 50}, "2": {"frequency_hz": 50000, "vpp_v": 1.0, "mean_v": 0.5, "symmetry_percent": 30}}`。使用 expectations 必须同时传 `fetch_waveform=true`。
- `scope.advise`：参数同 `scope.observe`，另有 `target_cycles` 和 `target_vertical_divisions`。它基于当前观察和可选 expectations 给出 `scope focus` / `scope display` 建议、每通道推荐时基窗口和垂直档位；只返回建议，绝不应用调整。若传 `fetch_waveform=true`，同样可能改动示波器的波形传输源/模式。
- `doctor.config`：参数 `{"timeout_ms": 1000}`，结构化返回配置中各仪器的只读可达性、IDN 和型号匹配检查结果；不执行网段发现。

## 安全边界

- 默认只监听 `127.0.0.1`。
- 拒绝监听 `0.0.0.0`。
- `/mcp`、`/tools` 和 `/call` 强制 Bearer token。
- 当前工具不提供 raw SCPI，不应用显示/输出/采集建议；`scope.observe` / `scope.advise` 在 `fetch_waveform=true` 时会显式标注可能的波形传输状态影响。
- 不提供 raw SCPI。
- 不提供 power/source output on/off。
- 不提供 run 执行工具。
- `run.check` 只允许项目内 `plans/*.toml`。
- `capture.inspect` 只允许项目内 `data/raw/` 离线采集包。
- `doctor.config` 只检查当前配置中的资源，不扫描网段。
- `/mcp` 和 `/call` 的 JSON 请求体有 1 MiB 上限。
