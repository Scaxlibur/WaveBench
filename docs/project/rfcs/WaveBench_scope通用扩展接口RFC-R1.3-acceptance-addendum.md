# WaveBench scope 通用扩展接口 RFC：R1.3 Acceptance Addendum A1

> 状态：`Draft`（核心复审后补充，未接受）
> 适用正文：[WaveBench scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)
> 目的：冻结从内部基础设施到公共 capability 注册之间的验收门
> 同步来源：WaveBench Instrument Plugins `a013891`

本文件是总 RFC 第十二节的可单独审阅索引，不是第二套并行合同。字段、Protocol、数值和
失败语义以总 RFC 为唯一事实源；本文件只列出本轮核心复审要求的验收顺序。

## 允许的内部实现范围

核心可以先实现下列私有或 feature-gated 组件，但不得注册新 capability、改变旧 descriptor
行为或启动插件迁移：

- operation context、非嵌套 phase coordinator 和跨 phase 不重置的 binary ledger；
- acquisition、screenshot、transfer 的 typed snapshot / baseline / restore / verify 模型；
- capability-method/descriptor gate、legacy artifact 和 fake/conformance fixture。

## 公共注册前的 P0 门

1. 核心必须实现 `ScopeTraceTransferRecoveryDriver`；`fetch_trace` 在 transfer 状态可能变化时必须
   使用带 `context_id`、epoch、nonce 的 `ScopeTraceTransferBaseline`，并用 descriptor profile
   的固定 restore order/step 上限完成逐字段 restore/verify；`CHDR`、`CORD`、`WFSU` 等字段
   不得只靠文字承诺。
2. 核心必须实现 `ScopeDescriptorExtensions`、`SCOPE_CAPABILITY_METHODS` 和 required Protocol；
   缺 profile 或方法时在零 I/O 阶段拒绝，方法存在但未声明 capability 时不自动暴露。
3. 核心固定并测试以下常量：

   | operation | response / total / query / resync | default timeout |
   | --- | --- | --- |
   | `scope.screenshot_v2` | `262144 / 262144 / 1 / 0` | `5000 ms` |
   | `scope.acquisition_start/single` | binary `—` | `30000 ms` |
   | `scope.fetch_trace` | `8388608 / 67108864 / 256 / 65536` | `60000 ms` |

   profile/connection 只能收紧；超出同步上限、无法证明边界或终止设置恢复失败时统一
   close + `poisoned`。
4. `OperationRequest.deadline`、`before_and_after` 默认 error timing、recovery `disabled`、
   每次 I/O 的剩余 deadline 计算和 artifact 字段已有负向测试。

## 公共注册前的 P1 门

- 旧 capture 嵌入 screenshot 只采用父 operation 字段闭包；没有完整字段闭包则 I/O 前拒绝，
  截图或恢复失败使父 capture 失败，不注册 composite operation。
- screenshot、acquisition、transfer baseline 必须绑定 context、session epoch、opaque nonce，
  按一次性消费状态拒绝重放。
- `identity_delta` 只有在 `ScopeAcquisitionControlProfile.identity_semantics` 为
  `unique_within_session_epoch` 时可用；否则只接受完整 state transition。
- phase coordinator 必须通过现有 normal gate 与 recovery/verification authorization 的
  非嵌套顺序桥接，driver 不接收 session token。
- R1.3 公共 trace 只包含 analog/digital/reference；spectrum、math、fft_phase、frequency
  axis 和新增单位移入后续 RFC。

## 退出条件

满足以下条件后，才能把 RFC 提交为 `Proposed`，并由核心决定是否注册 capability：

1. P0/P1 fake/conformance fixture 全部通过；
2. 至少两个独立仪器族或 backend 证明 transfer restore、binary framing 和失败恢复；
3. Service、CLI、descriptor、registry、artifact schema 和版本门完成核心评审；
4. 未决的 trace extensions、continuation 和 poisoned-session reopen 设计不被当前 capability
   隐式引用。

在此之前，总 RFC 和本 addendum 均保持 `Draft`，插件侧不增加新 capability 或核心迁移。
