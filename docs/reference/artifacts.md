# 运行产物 Reference

本页说明 `run plan` 写入的运行产物入口。字段的 machine source 是 `src/wavebench/services/run_artifacts.py` 和对应的 typed result；不要从旧 Guide 推断新增或可选字段。

## 输出

成功或失败的 run 在写入运行目录后会产生以下文件：

```text
<run-dir>/
  plan.toml            原始 plan 存在时的副本
  run.json             运行级结构化记录
  summary.csv          面向快速查看和表格导入的摘要
  steps/
    00_<kind>.json     单个 step 记录
```

`run report <run-dir>` 只读取已有产物并生成离线报告，不连接仪器，也不修改原始采集数据。

## `run.json`

以下字段始终由 writer 写入：

| 字段 | 含义 |
| --- | --- |
| `status` | 运行级状态。 |
| `experiment` | plan 中的实验名称和标签。 |
| `plan` | plan 文件路径字符串。 |
| `steps` | 每个已记录 step 的结构化记录。 |

`error`、`restore`、`provenance`、`source_operations` 和 `rf_source_operations` 仅在对应条件满足时出现。`source_operations` 与 `rf_source_operations` 只接受带有已知 schema 的类型化 operation artifact。

## step 记录

每个 `steps/<index>_<kind>.json` 记录包含 `index`、`kind`、`status`、`fields` 和 `artifact`。具体 `artifact` 形状取决于 step；采集、频响、DMM、Source V2 和 RF Source 不共享一张人工字段表。

## `summary.csv`

当前 writer 的列顺序为：

```text
index, kind, status, package, metadata, quality_status, quality_warnings,
recovered, expect_status, expect_failures, expect_fft_status, expect_fft_failures
```

`summary.csv` 适合快速查看和表格导入。需要保留完整字段、条件字段或错误 evidence 的自动化工具应优先读取 `run.json` 和对应 step JSON。

## 相关页面

- [执行一次实验](../how-to/run-an-experiment.md)
- [从模板到报告](../tutorials/from-template-to-report.md)
- [run plan 排错](../how-to/troubleshooting.md)
- [run plan Reference](run-schema.md)
