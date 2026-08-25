# WaveBench scope 通用扩展接口 RFC：R1.3 Acceptance Addendum A1

> 状态：`Accepted`（核心 `0.8.23` 开发线已通过离线 A1 门）
> 适用正文：[WaveBench scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)
> 目的：记录从内部基础设施到公共 capability 注册的验收门和完成证据
> 同步来源：WaveBench Instrument Plugins `a013891`

本文件是总 RFC 第十二节的可单独审阅索引，不是第二套并行合同。字段、Protocol、数值和
失败语义以总 RFC 为唯一事实源；本文件记录核心 `0.8.23` 开发线完成公共注册时采用的验收顺序。

## 验收结果

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| P0/P1 核心 fixture | 通过 | `tests/test_scope_phase_coordinator.py`、`tests/test_scope_binary_contract.py`、`tests/test_scope_extension_service.py` |
| 两类 backend wrapper | 通过 | PyVISA 与 RsInstrument 的 VISA `INSTR` definite-block/message EOM；SocketIO/serial 零发送拒绝 |
| transfer、截图和采集 fixture | 通过 | 核心 fake fixture 与 SDS800X HD conformance fixture |
| 公共接口 | 通过 | `ScopeDescriptorExtensions`、公共 capability/operation registry、`ScopeService`、CLI、artifact schema、`0.8.23` 版本门 |
| 旧插件兼容 | 通过 | DS1000Z、RTM2000、SDS800X HD 离线插件测试；未声明新 capability 的 descriptor 行为不变 |
| SDS3000 迁移审计 | 插件侧待同步 | 运行相关测试通过；手工 capability 矩阵仍按旧核心 19 项记录，需更新为 26 项后再决定 opt-in 范围 |
| 实机覆盖 | 不属于离线 A1 结论 | 每个准备 opt-in 的插件仍需单独执行 resource/backend 和仪器状态恢复验收 |

离线 A1 通过只允许核心发布合同和插件开始迁移，不表示某个型号已经通过新 capability 的实机
验收。

## 历史实施边界

公共注册前，核心只允许实现下列私有或 feature-gated 组件：

- operation context、非嵌套 phase coordinator 和跨 phase 不重置的 binary ledger；
- acquisition、screenshot、transfer 的 typed snapshot / baseline / restore / verify 模型；
- capability-method/descriptor gate、legacy artifact 和 fake/conformance fixture。

## P0 门

1. 核心必须实现 `ScopeTraceTransferRecoveryDriver`；`fetch_trace` 在 transfer 状态可能变化时必须
   使用带 `context_id`、epoch、nonce 的 `ScopeTraceTransferBaseline`，并用 descriptor profile
   的固定 restore order/step 上限完成逐字段 restore/verify；`CHDR`、`CORD`、`WFSU` 等字段
   不得只靠文字承诺。
2. 核心必须实现 `ScopeDescriptorExtensions`、`SCOPE_CAPABILITY_METHODS` 和 required Protocol；
   缺 profile 或方法时在零 I/O 阶段拒绝，方法存在但未声明 capability 时不自动暴露。
3. 核心固定并测试以下常量：

   | operation | response / total / query / resync | default timeout |
   | --- | --- | --- |
   | `scope.screenshot_v2` | `8388608 / 8388608 / 1 / 0` | `5000 ms` |
   | `scope.acquisition_start/single` | binary `—` | `30000 ms` |
   | `scope.fetch_trace` | `8388608 / 67108864 / 256 / 65536` | `60000 ms` |

   profile/connection 只能收紧；超出同步上限、无法证明边界或终止设置恢复失败时统一
   close + `poisoned`。
4. `OperationRequest.deadline`、`before_and_after` 默认 error timing、recovery `disabled`、
   每次 I/O 的剩余 deadline 计算和 artifact 字段已有负向测试。

## P1 门

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

核心按以下条件决定是否注册 capability：

1. P0/P1 fake/conformance fixture 全部通过；
2. 至少两个独立仪器族或 backend 证明 transfer restore、binary framing 和失败恢复；
3. Service、CLI、descriptor、registry、artifact schema 和版本门完成核心评审；
4. 未决的 trace extensions、continuation 和 poisoned-session reopen 设计不被当前 capability
   隐式引用。

上述条件已在核心 `0.8.23` 开发线的离线验收中满足。插件现在可以准备迁移，但只有在插件自身
conformance、包检查和实机验收通过后，才能在正式 descriptor 中声明对应 capability。
