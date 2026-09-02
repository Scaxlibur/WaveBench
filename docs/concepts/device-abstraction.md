# 设备抽象

WaveBench 的基础 driver 合同只要求通用身份查询与关闭。不同仪器能力通过可选 Protocol 和 descriptor extension 追加，而不是把所有厂商功能塞进基础接口。

## 从配置到 driver

配置引用 driver 标识。registry 先解析内建或已安装插件的 descriptor，并校验它与目标仪器 kind 一致；factory 随后打开已配置的资源，Service 再按 operation contract 调用所需的可选能力。

这个过程的关键是「先声明，后打开」。缺少 capability 或配置不匹配时，Core 应在实际仪器 I/O 前失败。

## 可选能力的作用

Scope、source、RF source、power 和 DMM 各自拥有不同的可选能力合同。它们可以随版本追加，但不改变不相关仪器的基础协议。driver 负责把类型化请求映射为型号协议，Core 负责通用的执行顺序、安全和 artifact。

## 相关页面

- [架构与职责](architecture.md)
- [插件模型](plugin-model.md)
- [新增仪器驱动](../development/instrument-drivers.md)
- [插件 Reference](../reference/plugins/index.md)
