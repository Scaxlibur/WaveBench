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

`run report <run-dir>` 只读取已有产物并生成离线报告，不连接仪器，也不修改原始采集数据；它会在运行目录或显式输出位置写入派生的 HTML，使用 `--pdf` 时还会写入 PDF。

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

## 多个 run 的离线索引

`run report-index` 读取一个或多个已有 run 目录，并在 `--output` 目录写入 `manifest.json`、`manifest.csv` 和 `index.html`。它不连接仪器；输出中的生成时间不应被当作实验时间或原始测量证据。

运行产物与 scope capture package 是不同层次的对象：run 目录记录 plan 和 step 关系，capture package 保存单次波形及其 metadata。需要分析具体采集字段时，以对应的 typed result、package loader 和 `metadata.json` 为准。

## 相关页面

- [执行一次实验](../how-to/run-an-experiment.md)
- [从模板到报告](../tutorials/from-template-to-report.md)
- [run plan 排错](../how-to/troubleshooting.md)
- [run plan Reference](run-schema.md)
