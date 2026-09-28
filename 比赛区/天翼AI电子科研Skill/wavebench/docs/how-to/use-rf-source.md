# 使用 RF 信号源

本页适用于已经确认需要使用 `rf_source` 的实验操作者。目标是在不把普通函数发生器模型套用到 RF 端口的前提下，先完成离线确认，再进入已声明的只读或写入操作。

> [!WARNING]
> RF 写入和输出操作可能改变端口能量。`run check` 的通过结果不构成接线、端接或输出安全证明。

## 必要条件

- 已确认任务确实需要 `rf_source`，而不是使用 channel、Vpp 和 offset 的普通 `source`。
- 配置中的资源、access、安全限制和端接声明已按当前实验台核对。
- 已安装并选中相应仪器插件；型号支持和 profile 以该插件的 descriptor 为准。

## 步骤

### 1. 先查询可用操作

```bash
python -m wavebench rf-source --help
python -m wavebench run schema
```

两个命令都不会连接仪器。`rf-source --help` 显示当前安装版本的操作入口；`run schema` 显示可写入 plan 的 `rf_source.*` step。命令出现在 help 或 schema 中，不表示当前配置的仪器已经获准执行。

### 2. 采用最小的只读检查

在确认配置后，先检查身份和状态：

```bash
python -m wavebench rf-source idn --config wavebench.toml
python -m wavebench rf-source status --config wavebench.toml
```

这些命令会连接仪器。若 descriptor 未声明所需 capability、资源不匹配或 access policy 不允许，停止并修正配置或插件，不要改用原始 SCPI 绕过。

### 3. 写入前重新检查边界

写入前，确认本次 operation 的 capability、`read_write` access、端口安全限制、实际端接和仪器当前状态。对于 plan，先运行：

```bash
python -m wavebench run check --config wavebench.toml --plan plans/<plan>.toml
python -m wavebench run verify --config wavebench.toml --plan plans/<plan>.toml
```

`run verify` 会连接仪器，但不代替人工复核接线和额定值。只有目标 capability、preflight 和现场条件都满足时，才执行显式的写入或输出步骤。

## Verification

- 离线 schema 与 plan 字段一致。
- 只读身份与状态检查匹配预期仪器。
- 写入前的 capability、access、端接和安全条件均可证明。
- 运行后检查 artifact，并在必要时用新的只读会话复核状态。

## 常见失败

- `rf_source` capability 未声明：查看安装插件的 descriptor 和插件仓库，而不是从 Core help 推断型号支持。
- `read_only` 拒绝写入：这是 access policy 的预期行为；只在已完成现场确认时才调整配置。
- 状态或 readback 不确定：停止后续写入，保留 artifact，并按操作合同规定的恢复边界处理。

## 相关页面

- [配置 Reference](../reference/configuration.md)
- [run plan Reference](../reference/run-schema.md)
- [安全模型](../concepts/safety-model.md)
- [Capability 模型](../concepts/capability-model.md)
- [仪器插件仓库](https://github.com/Scaxlibur/wavebench-instrument-plugins)
