# CLI Reference

本页说明 WaveBench CLI 的入口、输出和副作用边界。完整参数以当前安装版本的 `python -m wavebench --help` 及各子命令 `--help` 为准。

## Synopsis

```bash
python -m wavebench --help
python -m wavebench run --help
python -m wavebench --json <domain> <command> ...
```

一级命令域包括 `scope`、`source`、`rf-source`、`power`、`dmm`、`sweep`、`run`、`capture`、`mcp`、`tui`、`net`、`doctor`、`plugin`、`capability` 和 `lock`。`run` 的当前子命令由 `python -m wavebench run --help` 输出。

## 副作用

| 类别 | 命令族 | 行为 |
| --- | --- | --- |
| 离线、只读本地文件 | `run schema`、`run check`、`run intent`、`run compare`、`run resume`、`capture inspect`、`capability explain`、`lock status` | 不连接仪器。 |
| 离线且可能写本地文件 | `run template --output`、`run report`、`run report-index` | 不连接仪器，但会创建模板、报告或索引文件。 |
| 连接读取或预检 | `doctor`、状态／身份查询、`run verify` | 会访问配置的仪器，不应改变实验设置。 |
| 可能改变状态或触发采集 | 输出和 setter、`scope auto`／`scope capture`、非 fake TUI、`run plan` | 可能写入仪器、触发采集或切换输出。 |

`scope fetch` 读取已有波形，但仍是仪器 I/O；不要把它当作离线命令。每次硬件操作前确认接线、输入阻抗、输出状态和安全限制。WaveBench 不会自动执行 `*RST`，也不会因设置电压、幅度或频率而自动开启输出。

## JSON 输出与退出码

将 `--json` 放在命令行任意位置可请求机器可读输出。成功结果使用 `wavebench.cli.result.v1`，包含 `status`、`exit_code` 和 `result`；错误使用 `wavebench.error.v1`。普通成功输出写入标准输出，普通错误写入标准错误。

错误分类和稳定退出码见[错误 Reference](errors.md)。`run plan` 即使保留了运行产物，只要存在失败 step 也会返回非零状态。

## 相关页面

- [无硬件快速开始](../getting-started/quickstart.md)
- [配置实验台](../getting-started/configure-bench.md)
- [执行一次实验](../how-to/run-an-experiment.md)
- [run plan Reference](run-schema.md)
- [插件 Reference](plugins/index.md)
