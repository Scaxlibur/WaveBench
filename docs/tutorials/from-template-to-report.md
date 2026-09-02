# 从模板到报告

本教程通过一个信号源和示波器模板说明 WaveBench 的完整实验节奏：先生成和检查 plan，再进行连接预检，最后执行实验并读取离线报告。前两步不接触仪器；后续步骤必须在已确认的实验台上完成。

## 学习目标

完成本教程后，可以区分 `run check`、`run verify`、`run plan` 和 `run report` 的副作用，并知道每一步应看到什么。

## 前置条件

- 已完成[无硬件快速开始](../getting-started/quickstart.md)。
- 真正执行第 4 步之后的命令前，已准备好受支持的信号源、示波器、接线和安全限制。
- 没有准备真实实验台时，只完成第 1～3 步。

## 1. 选择一个保守模板

```bash
python -m wavebench run template --list
python -m wavebench run template source-scope-sine --output plans/my-sine.toml
```

第一条命令显示当前模板列表。第二条命令创建 `plans/my-sine.toml`；目标文件已存在时命令会拒绝覆盖。该步骤只写本地 TOML，不连接仪器。

## 2. 离线检查 plan

```bash
python -m wavebench run check --plan plans/my-sine.toml
```

成功时命令退出为 `0`，并输出 plan 和安全检查摘要。`run check` 会检查 schema、离线安全上限、plan 内引用和声明的 capability，但不会打开仪器 transport。

## 3. 阅读将要发生的操作

打开 `plans/my-sine.toml`，确认 source、scope、输出状态、质量检查和 restore 条款符合实验目标。完整字段以[run plan Reference](../reference/run-schema.md)中的命令入口为准；不要仅根据模板示例推断所有可用字段。

## 4. 连接前配置并做只读预检

复制并修改配置时，不要覆盖已有实验台配置。资源、输入阻抗和安全限制见[配置实验台](../getting-started/configure-bench.md)和[配置 Reference](../reference/configuration.md)。

```bash
python -m wavebench doctor --config wavebench.toml
python -m wavebench run verify --config wavebench.toml --plan plans/my-sine.toml
```

这两条命令会查询真实设备。成功时会返回已发现资源的身份或预检记录；失败时停止在写入前，应先按[排错指南](../how-to/troubleshooting.md)修正配置、接线或 capability。

## 5. 执行实验

```bash
python -m wavebench run plan --config wavebench.toml --plan plans/my-sine.toml
```

这一步会运行 plan，可能配置仪器、切换输出并采集数据。只在确认接线、输出状态、输入阻抗和安全限值后执行。完成后，命令会报告 run 目录；其中包含 plan 副本、`run.json`、`summary.csv` 和 step 记录。

## 6. 生成离线报告

```bash
python -m wavebench run report data/runs/<run-dir>
```

`run report` 只读取已有运行产物，不连接仪器，也不修改原始采集数据。先查看 `run.json` 的整体状态，再通过报告检查每个 step 的证据。字段与文件职责见[运行产物 Reference](../reference/artifacts.md)。

## 下一步

- 已有明确实验目标时，使用[执行一次实验](../how-to/run-an-experiment.md)。
- 遇到 plan 解析、字段或预检错误时，使用[run plan 排错](../how-to/troubleshooting.md)。
- 需要频率响应、校准或其他专题步骤时，先从[旧 run plan 专题材料](../archive/run-plan-guide-pre-migration.md)追溯，再核对当前 `run schema` 和对应 Reference。
