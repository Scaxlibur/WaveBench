# Capability 模型

Capability 用于描述某个 driver 已声明的通用操作边界。它让 Core 能在建立仪器会话前拒绝未支持的请求，也让插件可以逐项扩展实现，而不把「代码中有命令」误写成「所有仪器都可用」。

## Capability 不是什么

Capability 不是现场授权，不表示资源可达、当前状态安全，也不表示某个型号的全部功能已经验证。实际执行仍受 access policy、配置、安全 preflight、会话健康和 postcondition readback 约束。

## 声明与运行时检查

descriptor 由 registry 解析并校验，声明仪器 kind、driver 标识和支持的 operation/profile。Service 在执行前先检查 operation 合同、access 和 descriptor capability，再读取与操作有关的当前状态。这个顺序避免因为缺少 capability 而先打开不应访问的 transport。

## 为什么型号事实不放在 Core 文档

同一个 capability 在不同型号、固件或接线下可以有不同 profile、限制和证据状态。这些事实属于仪器插件仓库。Core 文档只说明 capability 的通用含义和检查方式；查找某型号是否支持某项操作时，应以安装插件的 descriptor 和插件仓库为准。

## 相关页面

- [架构与职责](architecture.md)
- [插件模型](plugin-model.md)
- [管理仪器插件](../how-to/manage-plugins.md)
- [插件 Reference](../reference/plugins/index.md)
