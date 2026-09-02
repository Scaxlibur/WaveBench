# 架构与职责

WaveBench 把实验意图、仪器协议和可审计结果分开处理。这样可以在不把厂商命令或型号限制写进普通用户流程的前提下，让一次 run 保持可验证、可回溯。

## 问题与目标

实验台自动化同时面对两类变化：实验目标会变化，仪器型号和协议也会变化。前者应由 CLI、run plan 和通用服务表达；后者应留在 descriptor 和仪器插件中。任何一层都不应替另一层推断安全授权。

## 分层模型

```text
CLI / run plan
        ↓
Service 与 operation contract
        ↓
descriptor、registry 与 capability gate
        ↓
driver / transport
        ↓
artifact 与 report
```

| 层 | 负责内容 | 不负责内容 |
| --- | --- | --- |
| CLI 与 run plan | 接收任务、离线检查和调度 | 直接生成厂商 SCPI。 |
| Service | access、capability、资源租约、preflight、执行顺序和错误语义 | 声明具体型号的支持范围。 |
| descriptor 与 registry | 发现 driver，声明静态能力、类型和 profile | 作为硬件操作授权或运行时状态证明。 |
| driver 与 transport | 执行已经获准的设备动作，隔离协议细节 | 决定用户任务或跨仪器安全策略。 |
| artifact 与 report | 保存结构化结果与派生报告 | 代替实时仪器状态。 |

当前命令和 schema 以 CLI 实现、`--help` 与 `run schema` 为准；执行结果字段以 artifact model 为准。这个页面只解释职责分工。

## Core 与插件边界

Core 维护通用仪器抽象、run plan、artifact、安全与会话模型。`wavebench-instrument-plugins` 维护具体型号、厂商 SCPI、固件差异、型号 profile、quirk 和实机验证状态。Core 文档可以说明「需要 descriptor 声明的 capability」，但不长期复制某个型号的 capability 列表或参数范围。

## 相关页面

- [设备抽象](device-abstraction.md)
- [Capability 模型](capability-model.md)
- [会话与恢复](sessions-and-recovery.md)
- [运行一次实验](../how-to/run-an-experiment.md)
- [插件 Reference](../reference/plugins/index.md)
