# 错误 Reference

WaveBench 的结构化错误使用 `wavebench.error.v1`。稳定字段为 `schema`、`code`、`type`、`message` 和 `exit_code`；`operation`、`details` 和 `cause` 只在适用时出现。

## 退出码

| 退出码 | 类别 | 含义 |
| --- | --- | --- |
| `1` | 未分类错误 | 未被 WaveBench 专用错误处理捕获的失败。 |
| `2` | `ConfigError` | 配置、参数、schema 或 run 失败。 |
| `3` | `ConnectionError` | 资源连接或关闭失败。 |
| `4` | `InstrumentError` | 仪器返回的状态、能力或操作错误。 |
| `5` | `OperationTimeout` | 操作在配置的时间范围内未完成。 |
| `6` | `DataError` | 解析、测量或数据格式错误。 |
| `7` | `ResourceBusyError` | 本地资源租约已被占用。 |
| `130` | 中断 | 进程收到中断。 |

`TransportIOError` 和 `SessionHealthError` 属于仪器错误类别。它们只保留结构化的发送／响应进度与 session 状态，不应把原始命令、完整响应、资源串或凭据复制进 error envelope。

## `run plan` 失败

run 已开始后发生失败时，CLI 会保留可写入的运行产物并返回退出码 `2`。优先检查 `run.json`、`summary.csv` 和对应 step 记录；不要仅凭终端最后一行判断是否已经恢复或安全关断。

## 相关页面

- [run plan 排错](../how-to/troubleshooting.md)
- [运行产物 Reference](artifacts.md)
