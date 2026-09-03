# 新增仪器驱动

本页适用于将新仪器接入 WaveBench Core 执行路径。目标是保持「driver 表达设备差异，Service 表达实验动作」的边界，并让 CLI、doctor、run plan、artifact 和测试有明确的接入顺序。

## 接入顺序

1. 确认手册和最小只读操作，例如身份、状态和错误查询。
2. 编写 driver，只封装设备命令和响应解析。
3. 编写 Service，明确读取、写入、输出和恢复的业务动作。
4. 增加 config、CLI parser 和 CLI handler。
5. 接入只读 `doctor` 与 `run verify`。
6. 只有真实用户任务需要时，再增加 run plan step、artifact、模板和报告支持。
7. 为每一层补 fake transport、service、CLI、schema 和失败路径测试。

## 不变量

- driver 不读取 CLI 参数，也不直接写 run artifact。
- 不默认 reset、autoscale、触发采集或切换输出。
- 写入动作必须有明确命令或 run step，并在需要时经 capability、access policy 和安全限制门控。
- 型号私有 SCPI、profile、quirk 和实机 evidence 属于 instrument plugin 仓库。

## 相关页面

- [插件开发](plugin-development.md)
- [测试 WaveBench](testing.md)
- [配置 Reference](../reference/configuration.md)
- [run plan Reference](../reference/run-schema.md)
