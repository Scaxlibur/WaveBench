# 项目文档分类

`docs/project/` 按文档用途分组。当前重点是让入口、参考资料和设计说明各自承担单一职责；文件名暂时保留原样，ASCII 文件名迁移另行处理。

## guides：使用指南

- [CLI 形态](guides/WaveBench_CLI形态.md)
- [run plan 使用指南](guides/WaveBench_run_plan_使用指南.md)
- [可安装仪器插件用户指南](guides/WaveBench_可安装仪器插件.md)
- [TUI 终端控制面板](guides/WaveBench_TUI终端控制面板.md)
- [HTTP MCP 只读接口](guides/WaveBench_HTTP_MCP_只读接口.md)

## reference：接口和数据格式

- [配置文件格式](reference/WaveBench_配置文件格式.md)
- [数据输出格式](reference/WaveBench_数据输出格式.md)
- [错误处理和日志策略](reference/WaveBench_错误处理和日志策略.md)
- [可执行仪器插件 API 约定](reference/plugins/WaveBench_可执行仪器插件API.md)
- [其他插件参考](reference/plugins/)

## design：设计说明

- [项目边界](design/WaveBench_项目边界.md)
- [设备抽象层](design/WaveBench_设备抽象层.md)
- [多仪器流程设计](design/WaveBench_多仪器协同流程设计.md)
- [sweep 状态保存与恢复](design/WaveBench_sweep状态恢复设计.md)

## rfcs：接口提案与决策

- [RFC 索引](rfcs/README.md)
- [scope 可移植性 RFC-0001～RFC-0008 组合说明](rfcs/WaveBench_scope可移植性RFC组合说明.md)：
  将八份外部插件提案转换为核心裁决；RFC-0001 和 RFC-0003 的早期入口由现有更严格合同取代，
  RFC-0008 在 R1.3 基础上增加标准 waveform bounded opt-in，RFC-0002、RFC-0004 和 RFC-0005 已完成
  核心只读状态 V2；snapshot V2 仍未发布，主包和插件均未 opt-in，也不修改 legacy API、CLI 或 artifact；
  RFC-0006～RFC-0007 仍为追加式 V2 草案，当前不新增插件 capability 或硬件工作。
- [Source V2 能力、状态与复合输出安全 RFC](rfcs/WaveBench_source能力状态与复合输出安全RFC.md)：
  `Accepted R6`；核心 `0.8.24` 开发线已实现 P0、M1–M4、M4.5、C1、M5-A 至 M5-D 与 C2，
  M6-A、M6-B 与 M6-C 已完成核心离线合同；真实插件验收与发布审计仍按里程碑推进。
- [transport 重放与 session 健康 RFC](rfcs/WaveBench_transport重放与session健康RFC.md)
- [scope 通用扩展接口 RFC](rfcs/WaveBench_scope通用扩展接口RFC.md)：`Accepted R1.3`，定义 operation context、binary budget、截图、采集控制、trace、错误策略及恢复验证合同。
- [标准波形有界二进制传输 RFC](rfcs/WaveBench_标准波形有界二进制传输RFC.md)：`Implemented R1（未发布）`，标准 waveform/capture 已具备对 R1.3 bounded binary context 的 descriptor opt-in 接入、恢复边界和新旧核心／插件兼容矩阵；外部插件仍需单独验收。

## contributing：开发和接入

- [插件开发指南](contributing/WaveBench_插件开发指南.md)：接入流程和发布检查
- [新增仪器驱动指南](contributing/WaveBench_新增仪器驱动指南.md)

## 历史资料与外部资料

- 版本路线图、版本整理清单和验证记录统一见根目录 [CHANGELOG](../../CHANGELOG.md)。逐版原始文档可在对应 Git tag 中查阅。
- 厂商编程手册和型号命令确认表由 [WaveBench instrument plugins](https://github.com/Scaxlibur/wavebench-instrument-plugins) 维护，不在本仓库重复保存。

本页只负责目录导航；具体命令、字段和安全边界以对应参考页及程序输出为准。
