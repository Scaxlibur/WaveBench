# run plan Reference

本页说明如何查询 WaveBench 当前支持的 run plan 结构。完整的 step、必填字段、可选字段和简要行为由离线命令生成，不在 Guide 中复制维护。

## Synopsis

```bash
python -m wavebench run schema
python -m wavebench run template --list
python -m wavebench run template <name> --print
```

三个命令都不连接仪器。`run schema` 输出顶层 TOML 表以及按 step kind 排序的字段清单；`run template --list` 列出当前模板；`--print` 将一个模板的 TOML 输出到标准输出。

## 使用顺序

1. 先运行 `run schema`，确认安装版本接受的 `kind` 和字段。
2. 再用 `run template --list` 选择接近实验目标的保守模板。
3. 使用 `run check` 检查实际 plan；它不能替代连接预检或实验台安全确认。

## 事实来源与边界

当前 schema 的 canonical source 是 `src/wavebench/services/run_plan.py` 中的 step schema 以及 `python -m wavebench run schema` 的输出。模板名称与默认内容来自 template registry。页面中的计划片段只能说明一个任务，不能作为完整字段表或型号 capability 的来源。

实际执行步骤、连接预检和副作用见[执行一次实验](../how-to/run-an-experiment.md)。字段错误和 schema 变更的排查见[run plan 排错](../how-to/troubleshooting.md)。
