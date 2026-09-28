# 插件模型

WaveBench 支持 metadata、可执行插件和声明式 SCPI 三条插件路径。它们的信任边界与执行能力不同，不应视为同一类扩展机制。

## 三条路径

| 路径 | 用途 | 边界 |
| --- | --- | --- |
| V1 metadata | 展示已有仪器 metadata | 不进入真实仪器执行。 |
| V2 executable plugin | 提供 descriptor 和 driver | 只在配置选中该 driver 时进入执行；第三方代码属于可信本地代码。 |
| 声明式 SCPI TOML | 检查和描述只读 IDN probe | 不提供任意 SCPI 或写入。 |

## 责任分配

Core 负责插件发现、resource、transport、Service、安全、run plan、artifact 和会话语义。插件负责厂商协议、解析、descriptor、型号限制和私有测试。型号级 SCPI、quirk、profile 与实机 evidence 应留在 `wavebench-instrument-plugins`，不要复制到 Core 的通用 Guide。

## 相关页面

- [插件 Reference](../reference/plugins/index.md)
- [管理仪器插件](../how-to/manage-plugins.md)
- [插件开发](../development/plugin-development.md)
- [新增仪器驱动](../development/instrument-drivers.md)
