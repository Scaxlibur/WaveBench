# WaveBench run plan 使用指南旧入口

本页保留原有链接，作为 run plan 的快速入口。当前页面按读者目标拆分；完整 step、字段、capability 和 artifact 不能再从单一长页手工维护。

## 选择入口

- 想理解从模板到报告的一次完整体验，阅读[从模板到报告](../../tutorials/from-template-to-report.md)。
- 已有 plan，准备进行离线检查、连接预检和真实执行，阅读[执行一次实验](../../how-to/run-an-experiment.md)。
- 需要定位 `kind`、字段或 schema 错误，阅读[run plan 排错](../../how-to/troubleshooting.md)。
- 需要查询当前支持的 step 和字段，运行 `python -m wavebench run schema`，并阅读[run plan Reference](../../reference/run-schema.md)。
- 需要解释 `run.json`、`summary.csv` 或 step 记录，阅读[运行产物 Reference](../../reference/artifacts.md)。

`run check` 不连接仪器；`run verify` 会查询配置的仪器；`run plan` 才会执行可能改变仪器状态的实验。真实实验前先确认接线、输入阻抗、输出状态、安全上限和 restore 范围。

重构前的详细合并页面保留在[历史 run plan 原页](../../archive/run-plan-guide-pre-migration.md)，仅用于追溯和迁移核对，不作为当前 schema 或 capability 的来源。
