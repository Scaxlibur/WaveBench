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
- [transport 重放与 session 健康 RFC](rfcs/WaveBench_transport重放与session健康RFC.md)

## contributing：开发和接入

- [插件开发指南](contributing/WaveBench_插件开发指南.md)：接入流程和发布检查
- [新增仪器驱动指南](contributing/WaveBench_新增仪器驱动指南.md)

## 历史资料与外部资料

- 版本路线图、版本整理清单和验证记录统一见根目录 [CHANGELOG](../../CHANGELOG.md)。逐版原始文档可在对应 Git tag 中查阅。
- 厂商编程手册和型号命令确认表由 [WaveBench instrument plugins](https://github.com/Scaxlibur/wavebench-instrument-plugins) 维护，不在本仓库重复保存。

本页只负责目录导航；具体命令、字段和安全边界以对应参考页及程序输出为准。
