# 配置 Reference

WaveBench 从指定路径或默认的 `wavebench.toml` 加载本地实验台配置。该文件缺失时，依赖配置的命令会报错；`wavebench.example.toml` 是示例，不是完整 schema。

## 必需表与可选表

| 类别 | 表 |
| --- | --- |
| 必需 | `[connection]`、`[scope]` |
| 可选 | `[autoscale]`、`[waveform]`、`[output]`、`[quality]`、`[safety_limits]`、`[tui]`、`[source]`、`[rf_source]`、`[power]`、`[dmm]` |

字段、默认值和跨字段约束由 config model 与 parser 定义。修改 plan 或配置前，先核对[示例配置](https://github.com/Scaxlibur/wavebench/blob/master/wavebench.example.toml)、当前 CLI help 和相关 Reference。

## 最小结构

```toml
[connection]
backend = "lan"
resource = "TCPIP::192.0.2.10::INSTR"

[scope]
driver = "rtm2032"
access = "read_only"
```

示例中的 IP 地址是文档保留地址。真实资源、序列号、凭据和本地实验产物不得提交到公开仓库。

## Access policy

`scope`、`source`、`rf_source`、`power` 和 `dmm` 配置可以使用以下 `access` 值：

| 值 | 行为 |
| --- | --- |
| `read_write` | 允许 descriptor 和 operation policy 已授权的读取与写入。 |
| `read_only` | 仅允许 observe 或 stateful read；写入和采集会在 I/O 前被拒绝。 |
| `disabled` | 拒绝仪器操作；离线命令仍可运行。 |

配置中的 `access` 不能替代真实接线、操作系统权限或仪器自身保护。

## 安全限制

`[safety_limits]` 用于在打开 transport 前限制 Source／Power 写入。Source V2 的端口电压下界和上界必须成对出现，并且下界小于上界。限制不应为了通过一次实验而放宽；应先确认实际端接、量程和实验要求。

## 相关页面

- [配置实验台](../getting-started/configure-bench.md)
- [CLI Reference](cli.md)
- [run plan Reference](run-schema.md)
- [仪器插件仓库](https://github.com/Scaxlibur/wavebench-instrument-plugins)
