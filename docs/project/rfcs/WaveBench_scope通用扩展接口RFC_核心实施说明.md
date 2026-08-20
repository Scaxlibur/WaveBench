# WaveBench scope 通用扩展接口 RFC：核心实施说明

> 状态：内部实现中，公共 capability 未注册
> 对应规范：[scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)
> 验收门：[R1.3 Acceptance Addendum A1](WaveBench_scope通用扩展接口RFC-R1.3-acceptance-addendum.md)
> 实施分支：`Scaxlibur/feat/scope-generic-extensions-r1-3`

本文记录核心实现对 R1.3 Draft 歧义的裁决、当前门禁和剩余验收项。它不把 RFC 状态改为
`Accepted`，也不授权插件声明新增 capability。

## 核心裁决

### Baseline nonce 生命周期

核心采用以下一次性状态机：

```text
fresh
  -> passed_to_main
  -> restore_attempted
  -> verify_attempted
  -> consumed
```

restore 和 fresh verify 各有一个独立的一次性 slot。restore 一旦开始，即使 driver 抛出异常，
也不能再次执行；只要 session 尚未 `poisoned`，允许执行一次诊断性 fresh verify。restore 已失败时，
诊断性 verify 即使读回匹配，也不能使 session 回到 `healthy`。

context、operation、session epoch、nonce 或 phase 任一不匹配时，在 instrument I/O 前拒绝。
artifact 只记录 nonce 摘要，不记录原值。

### Trace 恢复顺序

只要 `fetch_trace` 可能修改任一 transfer field，成功和失败路径都必须执行 restore 与 fresh
verify：

```text
preflight snapshot
  -> main transfer
  -> error_after（按策略）
  -> success_restore / failure_cleanup
  -> cleanup_verification
  -> terminal
```

`scope.query_response_header`、`scope.waveform_byte_order` 和
`scope.waveform_transfer_window` 与其他 transfer field 使用同一逐字段验证规则。缺少任一字段的
fresh readback 时，结果不能返回成功，session 保持 fail-closed。

### OperationSpec 机器字段

核心为候选 operation 增加显式字段：

- `operation_timeout_ms`；
- `postcondition_fields`；
- `cleanup_verification_fields`；
- response、operation-total、query-count 和 resynchronization 四类 binary limit；
- `error_check_minimum`；
- `embedded_screenshot_contract`。

新 scope operation 不再依赖解析 `timeout_source` 字符串取得超时数值。旧 operation 继续使用
`connection.timeout_ms`，保持兼容。

嵌入截图合同分别记录仪器状态字段和 `output.screenshot` artifact 字段。候选父 capture spec
具备完整 changed、verification 和 cleanup 字段，但尚未替换公开 capture spec。

## 默认关闭门禁

当前实现遵守以下限制：

- 新模型位于实验模块，不从 `wavebench.instruments` 顶层导出；
- 新 capability 方法表与公开 `CAPABILITY_METHODS` 分离；
- 新 operation spec 与公开 `OPERATION_REGISTRY` 分离；
- 实验 Service 和 operation context 构造时必须显式传入内部 enable gate；
- PyVISA、RsInstrument 和 serial backend 在通过 framing conformance 前，对新
  `query_binary()` 入口执行零发送拒绝；旧 `query_bin_block()` 行为不变；
- SDS3000 和其他插件不得据此提高核心版本下限或开始 capability 迁移。

## 当前已实现范围

- 单 operation context、绝对 monotonic deadline、cleanup 时间预留和非嵌套 phase 授权；
- response/total/query/resync binary ledger、严格 definite-block parser 和结构化字节证据；
- screenshot、acquisition、trace transfer 的 typed snapshot、baseline、restore 和 fresh verify；
- descriptor extension 与候选 capability-method 完整性检查；
- screenshot、acquisition control、analog/digital/reference trace 的内部 Service 编排；
- `scope.error_drain_v1` 的三态策略、`max_records+1` 终止证据、query 次数对账和脱敏 artifact；
- 旧 `scope.errors` 的 `legacy_unstructured` artifact 结构；
- main 进入后撤销 stale `verified_fields`，恢复验证通过后再重建字段证据。

## 公共注册前仍需完成

以下项目仍受 A1 退出条件约束：

1. 至少两个独立仪器族或 backend 的 transfer restore、截图恢复、acquisition proof 和 binary
   framing 证据；
2. PyVISA 或 RsInstrument 的 definite-block / message boundary conformance，以及 termination
   恢复失败测试；
3. 旧 capture 的运行时父 operation 字段闭包接入和 `fail_parent` artifact 验收；
4. CLI、稳定 Service 方法、公开 artifact schema 和版本门评审；
5. 新旧核心与插件四组合测试；
6. RFC 中明确排除的 spectrum、math、frequency axis、continuation token 和 poisoned-session
   reopen 设计继续保持未引用状态。

上述项目完成前，公开 registry、插件迁移和 RFC 状态均不改变。
