# WaveBench scope 通用扩展接口 RFC：核心实施说明

> 状态：核心 `0.8.23` 开发线已实现并注册公共合同
> 对应规范：[scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)
> 验收记录：[R1.3 Acceptance Addendum A1](WaveBench_scope通用扩展接口RFC-R1.3-acceptance-addendum.md)
> 实施分支：`Scaxlibur/feat/scope-generic-extensions-r1-3`

本文记录 R1.3 在核心中的实际边界。公共合同已经进入开发线，但这不表示任何现有插件自动获得
新能力，也不构成未执行实机验收的型号覆盖声明。

## 核心已实现的公共合同

- `ScopeDescriptorExtensions`、截图、采集控制、trace、错误策略和恢复模型已从
  `wavebench.instruments` 导出；
- 新 capability 已进入公共 `CAPABILITY_METHODS`；
- 新 operation 已进入公共 `OPERATION_REGISTRY`；
- `ScopeService` 提供截图、采集控制、trace metadata 和 trace fetch 方法；
- `ScopeExtensionService` 是可直接复用的稳定编排入口，不要求实验 enable 参数；
- operation artifact 使用 `wavebench.scope.operation.v1`，Service 结果使用
  `wavebench.scope.result.v1`；
- 使用任一 R1.3 capability 的插件必须声明 `wavebench_min_version >= 0.8.23`；
- `InstrumentDescriptor.scope_extensions` 位于 dataclass 字段末尾，旧位置参数顺序保持不变。

公共 operation 包括：

```text
scope.screenshot_profile
scope.screenshot_v2
scope.acquisition_run_state
scope.acquisition_start
scope.acquisition_single
scope.acquisition_stop
scope.trace_metadata
scope.fetch_trace
```

`scope.error_drain_v1` 是受管 error phase capability，不提供独立 operation。

## Backend 二进制传输

PyVISA 和 RsInstrument backend 已实现一次发送、有限读取的 `query_binary()`：

- definite block 按实际 `#N` 头读取，不从 payload 长度反推头部；
- response、operation total、query count 和 resynchronization 使用同一 operation ledger；
- 声明长度超限时，只在剩余 resynchronization 额度内读到边界；
- 非法头、截断、超时、额外尾部和设置恢复失败使用结构化 `TransportIOError`；
- read timeout 和 read termination 在成功与失败路径均恢复；恢复失败视为失步；
- definite block 和 `MESSAGE` 都只对能够报告 EOM 的具体 VISA `INSTR` resource 开放；
  SocketIO 和 serial 在发送前拒绝；
- 最终读取最多增加 1 个有界探测字节，避免合法 EOM 与 VISA `MAX_CNT` 状态重合时误判；
- RsInstrument 通过底层 VISA session 写入 binary query，避免 `write_str()` 自动插入状态查询；
- 失步、超出同步额度或 backend 合同违反会关闭 transport，并把 session 标记为 `poisoned`；
- 旧 `query_bin_block()` 保持原行为，不改变现有 driver 的采集入口。

## Baseline 与恢复

baseline nonce 使用以下一次性状态机：

```text
fresh
  -> passed_to_main
  -> restore_attempted
  -> verify_attempted
  -> consumed
```

context、operation、session epoch、nonce 或 phase 不匹配时，在仪器 I/O 前拒绝。artifact 只记录
nonce 摘要。restore 失败后允许一次诊断性 fresh verify，但不能据此把 session 恢复为 `healthy`。

`fetch_trace` 对以下 transfer 字段执行逐项 snapshot、restore 和 fresh verify：

```text
scope.run_state
scope.waveform_source
scope.waveform_mode
scope.query_response_header
scope.waveform_format
scope.waveform_byte_order
scope.waveform_points
scope.waveform_transfer_window
```

这组字段覆盖 `CHDR`、`CORD`、`WFSU` 等厂商状态映射。缺少任一 fresh readback 时，operation
不能返回成功。

## CLI

公共命令为：

```text
wavebench scope screenshot profile
wavebench scope screenshot capture
wavebench scope acquisition status
wavebench scope acquisition start
wavebench scope acquisition single
wavebench scope acquisition stop
wavebench scope trace metadata
wavebench scope trace fetch
```

截图和 trace fetch 要求显式指定新文件路径，不覆盖已有文件。二进制 payload 写入 PNG 或 NPY；
JSON artifact 只保存媒体类型、尺寸、点数、dtype、字节数、SHA-256 摘要、phase、恢复和错误检查
证据，不复制原始 payload。

## 旧 capture 兼容边界

声明旧 `scope.screenshot` 的插件继续使用原 capture 截图行为，既有 RTM2000 和 DS1000Z 路径
不变。插件同时声明旧能力和 `scope.screenshot_v2` 时，旧 capture 仍只走 legacy 路径；只有
v2、没有旧能力时，核心会在任何仪器 I/O 前拒绝嵌入请求，并要求改用独立的
`scope screenshot capture` 命令。

该分流避免把旧 capture 的「截图失败可保留 waveform」语义伪装成 R1.3 的 `fail_parent`。
以后若需要在新插件中重新开放嵌入截图，必须实现父 operation 字段闭包和同一 context 的
snapshot、capture、restore、verify；不得调用独立的子 operation。

## 插件采用条件

插件只有同时满足以下条件，才能声明新 capability：

1. wheel 依赖和 descriptor 均要求 WaveBench `0.8.23` 或更高的 `0.8.x` 版本；
2. descriptor 提供对应的 `scope_extensions` 静态 profile；
3. capability 所需方法全部实现，且额外方法不会产生隐式能力；
4. transfer 状态、截图状态和采集状态均有 fresh readback 证明；
5. binary framing 与 resource/backend 的实际 EOM 能力一致；
6. 插件自己的 conformance、包检查和实机验收分别通过。

现有插件没有声明新 capability 时，不需要提高最低核心版本。

## 当前验证与剩余工作

核心离线验证覆盖 definite block、`MESSAGE` EOM、超限 resynchronization、timeout、termination
恢复失败、nonce 重放、phase 越界、截图恢复、采集证明、trace transfer 恢复、error policy、
CLI artifact、版本门和新旧 capability 组合。

仍需在插件仓库完成：

- SDS3000 capability 审计清单从旧核心的 19 项更新为当前 26 项，再决定具体 opt-in 范围；
- 任何准备声明新 capability 的插件补齐 descriptor profile、driver 方法和发行版核心下限；
- 对实际 resource/backend 执行独立硬件验收，特别是 `MESSAGE` EOM、长 payload、设置恢复和
  失败后的下一次 query；
- `spectrum`、`math`、frequency axis、continuation token 和 poisoned-session reopen 继续由后续
  RFC 处理。

本次核心实施没有连接真实仪器，没有改动 `wavebench.toml`，也没有安装或升级依赖。
