# WaveBench 文档

本页按目标组织 WaveBench 文档。命令、schema、配置字段、capability 和型号支持状态以当前程序、descriptor 与插件仓库为准；历史 RFC 和已发布变更另有入口。

> [!WARNING]
> `doctor`、`run verify` 和写入类命令会接触真实仪器。先完成离线检查，再确认接线、输入阻抗、输出状态和安全限值。

## 第一次使用 WaveBench

- [无硬件快速开始](getting-started/quickstart.md)：安装后打印示例 plan，并完成一次不连接仪器的检查。
- [安装](getting-started/installation.md)：创建虚拟环境并验证 CLI 入口。
- [项目首页](../README.md)：了解适用范围、核心能力和安全摘要。

## 连接实验仪器

- [配置实验台](getting-started/configure-bench.md)：从示例配置到 `doctor` 和 `run verify`。
- [配置 Reference](reference/configuration.md)：当前配置表、访问策略和安全边界。
- [执行一次实验](how-to/run-an-experiment.md)：`run check`、`doctor`、`run verify` 与 `run plan` 的顺序。
- [示例计划目录](../plans/README.md)：每个示例的硬件副作用和适用范围。

## 自动化一次测量

- [从模板到报告](tutorials/from-template-to-report.md)：用一个模板理解完整实验流程。
- [执行一次实验](how-to/run-an-experiment.md)：已明确目标时的最短可靠步骤。
- [run plan Reference](reference/run-schema.md)：查询当前 step、字段和离线 schema。
- [使用 TUI](how-to/use-tui.md)：实验性的人工查看和受限控制。
- [启动只读 MCP 服务](how-to/serve-mcp.md)：本机自动化读取入口。

## 分析与报告

- [运行产物 Reference](reference/artifacts.md)：`run.json`、`summary.csv` 和 step 记录的稳定入口。
- [频率响应与校准](how-to/frequency-response-and-calibration.md)：从模板、离线检查到报告的专题操作入口。

## 查询精确参数

- [CLI Reference](reference/cli.md)
- [配置 Reference](reference/configuration.md)
- [run plan Reference](reference/run-schema.md)
- [错误 Reference](reference/errors.md)

## 理解模型与安全边界

- [项目边界](project/design/WaveBench_项目边界.md)
- [设备抽象层](project/design/WaveBench_设备抽象层.md)
- [多仪器协同流程设计](project/design/WaveBench_多仪器协同流程设计.md)

## 扩展 WaveBench

- [管理仪器插件](how-to/manage-plugins.md)
- [插件 Reference](reference/plugins/index.md)
- [插件开发](development/plugin-development.md)
- [新增仪器驱动](development/instrument-drivers.md)
- [贡献 WaveBench](development/contributing.md)
- [测试 WaveBench](development/testing.md)
- [文档工作流](development/documentation.md)
- [仪器插件仓库](https://github.com/Scaxlibur/wavebench-instrument-plugins)：具体型号、SCPI、profile、quirk 和实机 evidence。

## RFC、历史与发布

- [RFC 索引](project/rfcs/README.md)：提案和设计裁决，不能用作当前能力承诺。
- [更新日志](../CHANGELOG.md)：已发布版本的变更。
- [sweep 状态保存与恢复提案](project/design/WaveBench_sweep状态恢复设计.md)：历史设计，不作为当前行为来源。
- [历史 run plan 原页](archive/run-plan-guide-pre-migration.md)：迁移保留记录，不作为当前 schema 或 capability 的来源。
