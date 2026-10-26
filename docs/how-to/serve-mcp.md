# 启动只读 MCP 服务

WaveBench HTTP MCP 服务提供离线信息、run plan 检查和显式仪器查询。它不提供 raw SCPI、输出控制或 run 执行。

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

请求体上限为 1 MiB。以下工具表对应 Core `0.8.28`；安装版本的实际列表以 `GET /tools` 返回为准。仪器查询需要已确认的资源、接线和访问权限：

| 工具 | 行为 |
| --- | --- |
| `run.schema` | 返回 run plan schema。 |
| `run.check` | 只接受项目内的 `plans/*.toml`，离线解析并校验 run plan。 |
| `capture.inspect` | 只读取项目内的离线采集包摘要。 |
| `doctor.config` | 查询配置中的仪器，返回结构化检查记录。 |
| `scope.observe` | 查询示波器身份、可用状态和输入耦合，不读取波形。 |
| `scope.advise` | 基于查询结果和显式期望频率生成显示／时基建议，不执行建议。 |

缺少测量值、有效档位或期望频率时，建议保留 `advice_unavailable`；查询失败不能解释为无需调整。MCP 不提供 `scope observe --fetch-waveform` 的波形报告路径。执行建议须另行经过对应 CLI 的访问、能力和安全检查。开发与回归合同见[示波器观察实现与验收](../development/scope-observation.md)。

## Verification

访问 `GET /health` 确认服务已启动。需要调用受保护端点时，使用 Bearer token；认证失败或路径不在允许范围内时，先检查调用方配置，不要放宽监听地址或删除认证。
