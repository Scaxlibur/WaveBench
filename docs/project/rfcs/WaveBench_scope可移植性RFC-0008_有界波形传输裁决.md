# RFC-0008：标准波形有界二进制传输裁决

> 状态：`Implemented R1（未发布）`
> 规范正文：[标准波形有界二进制传输 RFC](WaveBench_标准波形有界二进制传输RFC.md)
> 外部验收：[MSO8104 受控实机验收记录](https://github.com/Scaxlibur/wavebench-instrument-plugins/blob/5a760c954f75dc69909bfde04cb5cd7837364ab3/packages/wavebench-rigol-mso8000/doc/MSO8104_HARDWARE_ACCEPTANCE.md)
> 核心基线：WaveBench `0.8.24` 开发线
> 系列总览：[scope 可移植性 RFC 组合说明](WaveBench_scope可移植性RFC组合说明.md)

## 摘要

原插件提案要求 waveform definite block 能声明 payload 后的 transport trailing，并限制单次
响应大小。核心接受问题，但不采用向 legacy `query_bin_block()` 增加
`expect_termination` 和 `max_bytes` 的方案。

核心 P0～P3 已使用 descriptor-owned `ScopeWaveformBinaryProfile`、
`query_binary()`、四维 budget、独立 waveform baseline 和核心恢复编排实现。本文只记录
编号映射与裁决摘要；字段、常量和失败语义以规范正文为准。

## 证据分层

早期 legacy 超时只能证明：

- 当时的读取方式不能证明响应边界；
- 增加 timeout 或重试不能修复同步证明；
- 不能由一次超时判断 payload 后究竟是空 trailing、`LF`、其他字节或读取设置不匹配。

后续受控 bounded 读取才为一个明确的型号、固件和 LAN/PyVISA 组合提供
`DEFINITE_BLOCK + LF` 证据。该结果必须写入 descriptor 的精确
`transport_trailing_hex`，不能外推到其他型号、固件、resource/backend、MAX、DMAX 或 capture。

核心离线 fixture 同时覆盖空、`LF`、`CRLF` 和错误 trailing，但 fixture 覆盖不表示具体
仪器支持这些变体。

## 核心裁决

### 不修改 legacy 入口

~~~python
def query_bin_block(
    self,
    command: str,
    *,
    replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
) -> bytes: ...
~~~

该方法保持原签名。active bounded phase 中调用它时，在发送前以
`binary_legacy_entry_unsupported` 拒绝。bounded 失败后不得回退 legacy。

### Descriptor opt-in

`ScopeDescriptorExtensions.waveform_binary_profile` 为 `None` 时继续使用旧 driver 方法。
profile 非空时：

- 只允许声明 descriptor 已有的标准 waveform capability；
- fetch、单通道 capture 和多通道 capture 分别声明，不能互推；
- 首版 framing 固定为 `DEFINITE_BLOCK`；
- trailing 使用最长 16 bytes 的精确十六进制；
- response、operation total、query count 和 resynchronization 均为有限预算；
- profile、OperationSpec 和 connection limit 逐项取最小值；
- descriptor 不能扩大核心硬上限；
- capability、profile、bounded Protocol 和恢复方法必须一一对应。

核心硬上限为：

~~~text
8 MiB / 64 MiB / 256 queries / 64 KiB resynchronization
~~~

这些上限只约束 opt-in 路径，不改变 legacy DS1000Z/DS1104/RTM2032。

### 核心编排

标准 Service 的 bounded 路径使用：

~~~text
identity + snapshot
  -> error-before
  -> main
  -> error-after
  -> restore
  -> fresh verify
~~~

多通道 capture 使用一次 acquisition、一个 baseline、一个 deadline 和一个 ledger。已完成
通道的 callback/partial artifact 时序保持原样，每个请求通道最多产生一次 waveform callback，
且 callback 内容必须与最终结果一致。

### Backend 和 factory

首版只接受核心已验证的 PyVISA/RsInstrument VISA `INSTR` bounded backend。Serial、
SocketIO、第三方 duck transport 或只实现公开 `query_binary()` 的对象，都不能仅凭方法存在
获得能力。

profile 非空时 construction barrier 在 factory 完成后验证：

- bounded Protocol；
- profile/capability 对应；
- backend/resource；
- 核心版本。

验证前所有仪器 I/O 以 `factory_construction_pending` 拒绝；失败时关闭 transport。

## 错误检查

`ScopeConfig.check_errors=true` 固定映射为
`ErrorCheckSpec(policy="required")`，要求 `scope.error_drain_v1`。`false` 固定为 disabled。
路径选择发生在 legacy `scope.errors` gate 前，旧 `scope.errors` 不能冒充类型化 drain。

## 失败语义

- binary query 最多发送一次，不重放、不续读、不从中间 chunk 继续；
- 响应边界已经证明后的 payload/preamble/scaling 失败仍执行 restore + fresh verify；
- 同步无法证明或已失步时 session 进入 `poisoned`；
- poisoned 后禁止 STOP、restore、verify、截图、IDN 和探测 query；
- transport 主异常保持 primary cause；
- restore/verify 失败时不返回 waveform 成功值；
- 不自动重连。

## 兼容性

1. capability 名称仍为 `scope.fetch_waveform`、`scope.capture_waveform` 和
   `scope.capture_waveforms`，不新增 `_bounded` 名称。
2. 旧 `ScopeDriver` 方法签名不变。
3. bounded Protocol 只对 profile opt-in driver 生效。
4. `WaveformData`、`CaptureResult`、`MultiCaptureResult`、CLI、run plan 和旧 artifact
   成功形状不变。
5. 旧 descriptor 不需要 `query_binary()` 或新 baseline 方法。
6. 新 descriptor 在旧核心中由 wheel 依赖或 descriptor load 阶段拒绝，并保持零仪器 I/O。
7. 外部插件只有在首个正式包含完整合同的核心版本发布后，才能同步提高 wheel/descriptor
   下限。

开发线当前使用 `0.8.24` 作为 profile 静态下限。插件发布时仍必须核对实际发行物；如果
`0.8.24` 不能唯一表示包含该合同的正式版本，应改用首个可区分的更高版本。

## 核心完成与插件边界

核心 P0～P3 已完成：

- profile、模型与 capability-aware validator；
- construction barrier 和可信 backend gate；
- bounded executor、typed error policy 和 core-owned recovery；
- 空/`LF`/`CRLF`、预算、分块、多通道、no-replay、poison 和 compatibility 回归。

仍属于 P4：

- 插件 descriptor/driver opt-in；
- 每个型号、固件和 resource/backend 的 trailing；
- X/Y 换算和已知信号测量；
- transfer state 的 fresh readback；
- CH2、MAX、DMAX、分块、多通道和 capture；
- 外部 source 前后独立 OFF 证据。

本核心分支的离线实现不能替代这些插件与实机证据。

## 已否决方案

- 给 `query_bin_block()` 增加 `expect_termination`；
- 由 driver 向 `query_binary()` 临时传 trailing；
- 全局关闭 PyVISA termination 等待；
- 插件直接访问 backend session；
- 增大 timeout 或失败后重试；
- bounded 失败后回退 legacy；
- 以短记录 fetch 成功推导 MAX/DMAX/capture；
- 在 poisoned session 上继续恢复。
