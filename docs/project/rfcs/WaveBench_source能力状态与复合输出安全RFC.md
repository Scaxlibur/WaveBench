# WaveBench Source V2 能力、状态与复合输出安全 RFC

> 状态：`Accepted`
> 修订：`R5`
> 核心基线：WaveBench `0.8.23`，`master@6cd2eb5`
> 首个支持版本：WaveBench `0.8.24`
> 实施状态：P0、M1–M4、M4.5 与 C1 已进入核心 `0.8.24` 开发线；只注册
> `source.snapshot_v2`，未注册写 capability

> [!IMPORTANT]
> `Accepted R5` 在 R4 的 operation context、受影响字段闭包、phase、nonce、cleanup reserve
> 和离线故障注入之外，增加 V1 写路由审计、可选 operation artifact 根键，以及受管插件的
> Source V2 wheel/descriptor 版本交叉门。它仍不注册写 capability，也不改变现有 V1 写合同、
> 插件版本下限或 V1 行为。

## 摘要

WaveBench 当前的 Source 基础接口可以表示固定波形、频率、Vpp 和输出开关，但无法无损表示
Harmonic、Modulation、Sweep、Burst、Pulse、Noise、DC、ARB、Counter、Combine 和 Coupling 等功能。
当前多个 profile 要求所有字段同时存在，`SourceStatus` 则使用裸 `Optional`。设备不支持、
当前模式不适用、查询未执行、查询失败与支持情况未知，因此可能被压缩成同一个 `None`
或伪造的默认值。

本 RFC 提议在不破坏 V1 的前提下，增加 Source 领域的 V2 合同：

- 用类型化 descriptor extension 表示功能、通道、读写方向、模式约束和查询特性；
- 用 `Observed[T]` 区分有值、不支持、不适用、未查询、暂不可获取和未知；
- 将通道、系统和跨通道状态拆分为按模式激活的 facet；
- 使用 anchor 复读和 session epoch 表示查询期间的一致性；
- 使用变长、稀疏且带完整性标记的谐波分量取代固定 H2–H16 假设；
- 对所有可开始或改变端口信号的操作使用统一的复合输出预算门；
- 复用已有 `InstrumentSessionState`、`SessionTransactionCoordinator` 和
  `GuardedAuditedTransport`，由核心组织写入、恢复与独立验证；
- 将软件测试、协议查询、仪器回读、波形测量和真实触发接线分层记录。

本提案源于 `wavebench-instrument-plugins` 仓库的
[Source V2 草案（修订 `224707d`）](https://github.com/Scaxlibur/wavebench-instrument-plugins/blob/224707d78e24720100129a32a935219798dcd19c/packages/wavebench-siglent-sdg2000x/doc/RFC_SOURCE_V2_CAPABILITY_STATE_SAFETY.md)，
但规范归核心仓库所有。型号命令、私有响应形态和实机验收证据仍由仪器插件仓库维护。

## 修订记录

| 修订 | 状态 | 主要变化 |
| --- | --- | --- |
| R0 | Draft | 建立问题边界、安全不变量、候选模型、预算和事务方向 |
| R1 | Draft | 冻结维护者决议、公共类型名称、两层查询计划、Source V1 生命周期、安全配置、ARB storage mutation、事务阶段、双 artifact schema 和兼容边界 |
| R2 | Accepted | 冻结 snapshot-only 公共类型、reason code、serializer、`__all__`、OperationSpec、Service／CLI JSON 和版本门；测试条件移至 M1–M2 退出门，写能力继续保留 |
| R3 | Accepted | 冻结 M3 的绝对端口电压配置、端接证据、预算 blocker、端口/共享功率预算模型和纯计算器；不注册任何 V2 写 capability |
| R4 | Accepted | 冻结 M4 的 Source operation contract、affected closure、固定 phase、core-owned baseline 与离线事务协调者；不注册任何 V2 写 capability |
| R5 | Accepted | 冻结 M4.5 的 V1 写路由清单和 additive artifact 边界，并实现 C1 的受管 wheel/descriptor PEP 440 交叉门与 V1/V2 兼容 fixture；不注册任何 V2 写 capability |

## Accepted R5 范围

下文的「必须」和「不得」是核心 `0.8.24` 的 snapshot-only、预算、事务和兼容公共合同。P0、M1–M4、
M4.5 与 C1 可以按本文实施；M5–M7 仍需对应写 capability 的后续 Accepted 修订。
以下内容不属于 R5 的实施授权：

- 不注册任何 V2 写 capability 或写 `OperationSpec`；
- 不增加 Source V2 run plan 写入口；
- 不改变现有 V1 `source.output` 或高级 Source 操作；
- 不执行仪器 I/O 或实机验收。

## 规范关系

- [transport 重放与 session 健康 RFC](WaveBench_transport重放与session健康RFC.md) 是连接代次、
  重放策略、`healthy`/`uncertain`/`poisoned`/`closed`、恢复授权和验证证据的权威合同。
  本 RFC 不覆盖或放宽其中任何状态转移。
- [scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md) 提供 operation context、
  phase authorization、core-owned baseline、nonce 和 typed descriptor extension 的设计先例，
  但 scope 专用 binary、acquisition 和 trace 语义不会直接复制到 Source。
- 仪器插件仓库中的 Source V2 草案、SDG2000X 验收记录和 DG4000 实现是问题与设备证据，
  不是核心公共合同。发生歧义时，以本文件及已接受的核心 RFC 为准。

## 背景与当前事实

### V1 状态无法表示可用性

`SourceStatus` 是平面 dataclass。`frequency_hz`、`amplitude`、`offset_v`、`phase_deg` 和
`square_duty_cycle_percent` 使用 `None` 表示缺失，但没有字段说明缺失原因。

当前 `SourceService.set_output(enabled=True)` 会将 `status.amplitude` 交给 `isfinite()`。Noise 和 DC
状态合理地返回 `amplitude=None` 时，路径会抛出裸 `TypeError`，而不是类型化、零写入的
失败关闭错误。

### 高级 profile 假定完整状态

| 功能 | 当前核心假设 | 跨厂商问题 |
| --- | --- | --- |
| Harmonic | 固定 H2–H16，要求 15 个幅度和相位都有限的分量 | 设备可能只支持部分阶次，或只能无写入地读取当前选中槽位 |
| Modulation | 关闭时仍要求内部源函数、频率和深度或偏差 | 部分设备关闭时只返回 `STATE,OFF` |
| Sweep | 要求 steps、hold、return、marker 等完整字段 | 组合查询可能不返回步数，某些字段只适用于特定 spacing |
| Burst | 关闭、Gate 和 Infinity 仍要求有限 cycles、period 和全部 trigger 字段 | 这些字段不会在所有模式下同时适用 |
| Pulse | 必须声明 `hold=WIDTH` 或 `hold=DUTY` | 设备可能同时返回宽度与占空比，但不返回权威 hold 状态 |
| Counter | 除 measurement 外，要求完整阻抗、衰减、门时间和统计配置 | 基础计数器可能只提供部分测量和触发字段 |
| Coupling | 固定 base channel 与 deviation 模型 | 部分设备使用 ratio、tracking direction 或不公开 base channel |

### capability 只是粗粒度路由

`InstrumentDescriptor.capabilities` 当前是字符串 tuple。核心可以验证 capability 名字与必需方法，
但它不表示指定型号、通道、当前模式和读写方向的细节。方法存在也不会自动生成 capability。

新 Source capability 不能由插件单方增加。核心 registry 会拒绝未登记的 capability；公共
descriptor、Protocol、Service、`OperationSpec`、artifact 和版本门必须由核心同时定义。

### 输出开启不只有一个入口

当前 `source.output` 在 ON 前读取基础状态并检查 `max_source_vpp`。该检查不覆盖偏置、
谐波、AM、Noise、Combine、负载语义、频率降额和共享功率。另外，以下操作也可能开始
或改变端口信号：

- `source.arb_load` 使用 `output_on=true`；
- 输出已开启时修改频率、函数、Vpp 或占空比；
- 修改谐波、调制、Sweep、Burst、Pulse、ARB、Combine、Coupling 或 Tracking；
- arm、fire、manual trigger、Gate 或 Sync 导致信号实际发出；
- 恢复操作将执行前为 ON 的输出重新开启。

只改 `SourceService.set_output()` 会留下真实旁路。Source V2 必须按「是否可能增加端口能量或
开始发出信号」分类操作，而不是只检查方法名。

### 已有 session 底座可以复用

核心已为每次具体连接建立 `InstrumentSessionState`，并通过 `GuardedAuditedTransport` 实施
access 与 health 门禁。`SessionTransactionCoordinator` 可以限定 I/O 类型、字段、步骤、
deadline 和验证证据。

Source Service 当前尚未将普通写入、恢复和独立验证接入该协调者。现有
`RestorableSourceState` 只覆盖 output、function、frequency、Vpp 和方波占空比，不能被描述为
完整 Source snapshot。

## 术语与分层

- **可执行仪器 API V2**：当前 `wavebench.instrument.v2` 插件装载、descriptor 和 factory 合同。
- **Source V1**：当前 `SourceStatus`、`SourceDriver` 和现有 `source.*` capability 合同。
- **Source V2**：本 RFC 提议的 Source 领域能力、状态、预算和事务合同。该名称不表示
  `wavebench.instrument.v2` 已升级。
- **facet**：只在特定功能和模式下适用的类型化状态分组。
- **anchor**：决定 facet 是否可查询、可解释的基础状态字段集合。
- **语义查询计划**：核心签发的 facet、anchor、作用域、副作用上限、查询数和 deadline 合同；
  不包含 SCPI 或厂商响应格式。
- **协议查询计划**：插件为满足语义查询计划而选择的厂商命令、合法顺序和解析流程。
- **一致 snapshot**：在同一 session epoch 和有界 deadline 内，查询前后 anchor 相同且所需 facet
  没有缺少的 snapshot。
- **端口电压边界**：在明确负载语义下，信号端口最小电压下界、最大电压上界和 Vpp 上界。
- **输出预算**：将所有已启用 contributor 转换为端口电压边界，并与配置安全上限比较的核心决策。
- **结果未知**：写入可能已被仪器处理，但调用方无法证明最终结果。
- **受影响字段闭包**：某项操作直接或间接可能改变、使证据失效或作为安全前置的最小字段集合。
- **实际端接证据**：由显式实验配置、运行计划或受控人工确认绑定到资源与通道的物理负载信息。
- **显示负载**：仪器用于计算和显示幅度的负载设置；它不证明端口外部的真实端接。
- **storage mutation**：创建、覆盖、删除或重命名仪器存储槽位的操作。它可以不产生端口信号，
  但可能造成不可恢复的数据变化，不能作为普通输出配置处理。

## 目标

- 保持 V1 `source.idn`、`source.status`、基础 setter、`source.output`、CLI 和 run plan 可迁移。
- 允许驱动如实表示部分支持、模式不适用、未查询和未知状态。
- 让 snapshot 查询适配状态依赖、消费型或存在其他副作用的查询。
- 让输出决策基于端口总波形的保守边界，不把基础 Vpp 当作高级模式的充分条件。
- 让插件只声明可以无损实现且经过测试的 read、configure、enable、disable、arm 和 fire 方向。
- 将所有可能改变端口能量的入口统一到同一安全决策，并防止 Service、run plan、TUI
  或恢复路径绕过。
- 复用现有 session health、结构化 transport 错误、资源租约和核心授权，不创建第二套锁存模型。
- 将功能声明、运行时状态、安全决策和实机验收作为四层独立证据。

## 非目标

- 不统一厂商 SCPI 助记符、响应文本或私有波形存储方式。
- 不把全部 Source 私有功能压缩为最低公分母命令。
- 不允许通过发送未知命令并读取错误队列自动探测 capability。
- 不把 descriptor 声明、方法存在或单台仪器 A4 证据解释为整个型号系列可安全使用。
- 不把示波器环回测量解释为校准证书或仪器计量合格结论。
- 不要求 V1 驱动一次性迁移，也不继续向 V1 profile 增加厂商专用必填字段。
- 不在 R2 冻结 V2 写 CLI 或 run plan schema；只读 `snapshot-v2` 除外。
- 不将实际端接从仪器显示负载或型号名自动推断。
- 不在 `poisoned` session 上尝试 OFF、IDN、恢复或验证 I/O。

## 插件信任边界

Python 插件仍以 WaveBench 进程权限运行，不是安全沙箱。Source V2 可以收紧公共调用路径，
但无法阻止恶意插件绕过 Python 公共契约。

运行时安全不能只依赖 descriptor 中的自我声明。核心必须仍然执行 access policy、资源租约、
session health、`OperationSpec`、预算决策和独立回读。插件证据引用只是可审查 metadata，
不是运行时授权 token。

## 强制安全不变量

本节规则自 `Accepted R2` 起构成强制合同。M1–M2 只消费其中的只读子集；涉及能量转换、存储
mutation 或恢复写入的规则仍作为对应写 capability 的后续准入门。

1. V1 公共 model、现有 capability ID 和插件调用语法不增加新的必填字段。
2. capability 只用于路由；类型化 descriptor profile、运行时 snapshot、预算与 access/session
   门必须全部通过。
3. 任一安全相关字段为 `UNKNOWN`、`UNAVAILABLE`、`NOT_QUERIED` 或未被所需查询覆盖时，不得开启输出、
   重新开启输出或执行可能增加端口能量的写入。
4. `NOT_APPLICABLE` 不等于 0，`NOT_QUERIED` 不等于 `UNSUPPORTED`，测量值不得补成仪器状态值。
5. 输出预算只使用 `VALUE` 且有明确单位、语义和证据来源的值。
6. 上层不得使用 V2 → V1 展平视图进行预算、写前比较、恢复或安全决策。
7. 一致性未证明或 anchor 复读不一致的 snapshot 不得作为写事务 baseline。
8. 任一目标字段在一次 operation 中最多执行一次写入；结果未知不得自动重试。
9. 一次公共 operation 只能属于一个 operation context；phase authorization 必须顺序、非嵌套、
   绑定 session epoch、字段闭包、最大步骤和绝对 deadline。
10. 普通插件方法不接收 session 授权 token，不能将 session health 恢复为 `healthy`。
11. `uncertain` 只允许核心授权的有界 recovery/verification I/O；`poisoned` 上的旧连接只允许 close。
12. 应急恢复的默认结束状态是受影响输出 OFF，不会在失败路径自动恢复为 ON。
13. 恢复时重新开启输出是新的、显式授权的输出操作，必须使用 fresh snapshot 重新计算预算。
14. 显示负载不证明实际端接。实际端接无证据且负载可能改变端口电压时，预算必须失败关闭。
15. artifact 不记录授权 token、baseline nonce、完整仪器响应、真实资源串、序列号或凭据。
16. 插件未声明 Source V2 时，核心不会从型号、方法存在或 V1 profile 自动推导 V2 写能力。
17. Source V2 能量增加操作必须显式配置 Vpp 与端口绝对电压上下限；缺失不表示无限制。
18. Source V2 首个可写修订只允许相关输出 OFF 时配置；该限制不追溯改变 V1 行为，也不表示
    仪器硬件不支持 live mutation。
19. storage mutation、波形选择/配置和输出 ON 是三个独立 operation，不共享一次准入决定。

## R2 公共集成合同

Source V2 的公共 model、enum、descriptor extension 和 driver Protocol 统一放在
`wavebench.instruments.source_extensions`，并由 `wavebench.instruments` 重导出。
Service coordinator、授权句柄、baseline nonce 和 artifact writer 属于核心内部实现，
不得从该公共模块导出。

### `InstrumentDescriptor` 扩展

R2 决定参照 `ScopeDescriptorExtensions`，在 `InstrumentDescriptor` 现有末尾字段
`scope_extensions` 之后增加可选的
`source_extensions: SourceDescriptorExtensions | None = None`。此前全部字段的顺序、默认值、
`frozen`、比较和 `dataclasses.replace()` 语义保持不变。只传入旧 V2 位置参数的 descriptor
必须继续构造成功，新增字段得到 `None`；不能把 `source_extensions` 插入
`config_fields`、`resource_schemes` 或 `scope_extensions` 之前。

冻结形态：

```python
SOURCE_CONTRACT_VERSION = "wavebench.source.v2"


@dataclass(frozen=True, slots=True)
class SourceDescriptorExtensions:
    contract_version: Literal["wavebench.source.v2"]
    topology: SourceTopologyContract
    features: tuple[SourceFeatureCapability, ...]
    query_contract: SourceQueryContract
    safety_profile: SourceSafetyProfile = SourceSafetyProfile()


@dataclass(frozen=True, slots=True)
class SourceFeatureCapability:
    feature: SourceFeature
    support: SupportState
    directions: tuple[SourceFeatureDirection, ...]
    scope: SourceFacetScope
    channels: tuple[int, ...]
    applicability: SourceConstraintApplicability
    profile: SourceFeatureProfile
    evidence_refs: tuple[str, ...] = ()
```

`SourceFeatureCapability` 不使用任意 `Mapping[str, object]` 作为公共约束容器。各功能必须选择
已注册的 typed profile，例如 `SourceBasicCapabilityProfile`、
`SourceHarmonicCapabilityProfile` 或 `SourceBurstCapabilityProfile`。核心按现有 factory
生命周期分层验证：

- registry 在调用 driver factory 前验证 `source_extensions` 只能由 `kind="source"` 的
  descriptor 使用，并验证 contract version、topology、feature 名、operation 方向、通道、
  typed profile、capability 和核心版本范围相互一致；
- factory 返回实例后，核心立即验证 capability 所需的 Protocol 方法；失败时关闭 factory 已打开的
  transport，且不得调用任何 Source operation method；
- 多声明的方法不生成隐式 capability；
- profile 只能收紧核心数值上限、deadline 和恢复步骤，不能放宽；
- 使用 Source V2 的插件必须提高 wheel 与 descriptor 的最低核心版本。

R2 不改变现有 eager factory 合同：factory 可以调用 `DriverContext.open_transport()`。因此
Protocol 方法缺失能够保证「零 Source operation 命令」，不能保证「零连接建立」。离线 A0 与插件
发布检查必须在真实资源使用前发现这类声明错误。插件绕过 `DriverContext` 自行访问设备或网络
属于违反插件信任合同；核心不把进程内第三方代码描述成可沙箱化代码。

`wavebench.instrument.v2` 保持不变。新插件的 wheel `Requires-Dist` 是旧核心导入公共类型前的
第一道版本门，descriptor `wavebench_min_version` 是加载 descriptor 后、factory 前的第二道版本门。
Source V2 distribution 的生效 `Requires-Dist: wavebench ...` 必须使用 PEP 440，并明确包含
`>=descriptor.wavebench_min_version,<descriptor.wavebench_max_version`；允许额外排除已知坏版本，
但不能扩大 descriptor 区间。descriptor 的版本范围按半开区间 `[min, max)` 解释。Source V2
validator 使用 PEP 440 解析，拒绝非法版本、`min >= max`、缺少任一边界、wheel 范围扩大，或
两者首个支持核心版本不一致。该校验只作用于 opt-in 的 Source V2 插件，不改变旧 V1 descriptor
当前接受的版本字符串。

R5 在受管安装的 postflight 中执行这项交叉校验：通用 wheel 检查先在 entry point import 前确认
当前环境恰有一条生效的 `wavebench` 依赖；随后 descriptor 已加载、但 driver factory 尚未调用时，
核心要求同一条生效依赖显式包含 descriptor 的 `>=min,<max`。额外的 PEP 440 排除项可以继续收窄
可安装版本，不得排除首个支持版本或放宽上界。这个后置交叉门不能把直接 `pip --no-deps` 或手工
安装变成受支持路径，也不能反向为历史核心版本增加保护。

受管插件安装必须在 entry point import 前检查 wheel metadata。当前 registry 读取 descriptor
仍需先执行 `entry_point.load()`，因此 descriptor 门不承诺「零插件模块导入」，只承诺在 driver
factory 和仪器 I/O 前拒绝。绕过 WaveBench package inspection 而直接执行 `pip --no-deps`，或
手工放入环境的越界安装，不属于受支持的旧核心 + 新插件组合；受管安装在完成 metadata 检查后
内部使用 `--no-deps` 不受此限制。旧核心无法被本 RFC 追溯修改。R2 不新增 sidecar manifest；若未来
要求任意运行时发现路径都在模块导入前拒绝，需要另行设计不执行插件代码的 metadata 索引。

删除 Source V1、改变既有 capability 语义或签名、改变既有返回 model，或者 descriptor
无法继续 append-only 时，才需要升级 executable API。

`wavebench.instruments.source_extensions.__all__` 是 Source V2 公共符号清单，
`wavebench.instruments.__all__` 必须按现有 Scope extension 模式重导出同一组对象。两条 import
路径取得的对象 identity 必须相同。`SourceOperationContextCoordinator`、授权 token、baseline
nonce、artifact writer、raw transport 和协议私有 parser 不得进入该清单。`Accepted` 修订必须
逐项冻结 `__all__`；实现不得仅因类名没有下划线便自动导出。R5 的精确清单为：

```text
SOURCE_CONTRACT_VERSION
SOURCE_OPERATION_ARTIFACT_SCHEMA
SOURCE_SNAPSHOT_MIN_CORE_VERSION
SOURCE_SNAPSHOT_SCHEMA
ArbitraryFacet
Availability
BasicWaveFacet
BudgetEvidenceSource
BudgetProofStrength
BurstFacet
ClosedFloatInterval
ComponentAmplitudeKind
CompositeOutputBudget
HarmonicCompleteness
HarmonicFacet
ModulationFacet
Observed
OutputFacet
PortVoltageBounds
PulseFacet
ResistanceBounds
SnapshotConsistencyState
SourceActivationPredicate
SourceActivationRule
SourceAmplitude
SourceAmplitudeUnit
SourceAnchorField
SourceAffectedClosure
SourceArbitraryCapabilityProfile
SourceArbitraryOvershootConstraint
SourceArbitraryPlaybackMode
SourceBasicCapabilityProfile
SourceBudgetBlockerCode
SourceBurstCapabilityProfile
SourceBurstMode
SourceCascadeState
SourceChannelStateV2
SourceClockSyncCapabilityProfile
SourceComponentAmplitude
SourceConstraintApplicability
SourceCounterCapabilityProfile
SourceCounterInputState
SourceCounterMeasurementKind
SourceCounterMeasurementV2
SourceCrossChannelCapabilityProfile
SourceCrossChannelStateV2
SourceDescriptorExtensions
SourceDisplayLoad
SourceEnergyEffect
SourceFacetQueryContract
SourceFacetScope
SourceFeature
SourceFeatureCapability
SourceFeatureDirection
SourceFeatureProfile
SourceFieldId
SourceFieldRef
SourceFrequencyDeratingBand
SourceFrequencyDeratingConstraint
SourceFrequencyMode
SourceGatePolarity
SourceHarmonicCapabilityProfile
SourceHarmonicComponentV2
SourceInputCoupling
SourceLoadKind
SourceModulationCapabilityProfile
SourceModulationEnvelopeConstraint
SourceModulationKind
SourceModulationParameter
SourceModulationParameterKind
SourceModulationSource
SourceNoisePeakConstraint
SourceOperationContract
SourceOutputCapabilityProfile
SourceOutputPolarity
SourceProtocolQueryRecord
SourcePulseCapabilityProfile
SourcePulseHoldBasis
SourceQueryContract
SourceQueryEffect
SourceQueryExecutionRecord
SourceQueryItemOutcome
SourceQueryPhase
SourceReasonCode
SourceReferenceClockMode
SourceReferenceClockState
SourceRelationEdge
SourceRelationGraph
SourceRelationState
SourceResistanceConstraint
SourceRuntimeCapabilityProfile
SourceRuntimeIdentity
SourceSafetyConstraint
SourceSafetyConstraintKind
SourceSafetyConstraintProfile
SourceSafetyProfile
SafetyContributor
SourceScopeRef
SourceSemanticQueryItem
SourceSemanticQueryPlan
SourceSharedPowerConstraint
SourceSharedPowerBudget
SourceSharedPowerState
SourceSignalPathKind
SourceSnapshotConsistency
SourceSnapshotV2
SourceSnapshotV2Driver
SourceSweepCapabilityProfile
SourceSweepMarker
SourceSweepSpacing
SourceSyncState
SourceTerminationEvidence
SourceStorageEffect
SourceSystemStateV2
SourceTopologyContract
SourceTriggerOutput
SourceTriggerSlope
SourceTriggerSource
SourceTriggerState
SourceTypedObservation
SourceVoltageReferenceConstraint
SourceV1WriteRouteId
SourceWaveformKind
SupportState
SweepFacet
TerminationEvidenceLifetime
TerminationEvidenceSource
TerminationKind
TerminationSpec
VoltageReferenceBasis
source_snapshot_v2_document
source_v2_canonical_json
source_v2_digest
source_v2_to_data
```

`source_snapshot_timestamp_utc()`、operation artifact builder、coordinator 和错误类为核心内部符号，
不在 `__all__` 中。

### capability 与 Protocol

capability 仍是粗粒度路由，精确功能和方向由 `SourceDescriptorExtensions` 收紧。
首个实施阶段只注册一个新 capability：

| capability | required method | 语义 |
| --- | --- | --- |
| `source.snapshot_v2` | `execute_source_query_plan_v2` | 执行核心签发的语义查询计划并返回类型化观测与协议执行记录 |

`SourceSnapshotV2Driver` 是独立的 `runtime_checkable Protocol`，不向现有 `SourceDriver`
增加无条件方法。第一阶段只增加 `SourceService.snapshot_v2()` 和只读 CLI
`wavebench source snapshot-v2`；不注册任何 V2 写 capability，也不增加 Source V2 run plan 写入口。
CLI 只序列化核心构造的 `SourceSnapshotV2`，不能显示插件原始执行记录或响应文本。

```python
@runtime_checkable
class SourceSnapshotV2Driver(InstrumentDriver, Protocol):
    def execute_source_query_plan_v2(
        self,
        plan: SourceSemanticQueryPlan,
    ) -> SourceQueryExecutionRecord: ...
```

R2 否决统一的 `source.patch_v2`、`source.arm_v2` 和 `source.fire_v2`。后续写能力使用以下保留 ID；
只有对应 operation contract、Protocol、Service 和 A0 验收同时完成时，才能逐项注册：

| capability | required method | 范围 |
| --- | --- | --- |
| `source.basic_configure_v2` | `configure_source_basic_v2` | 基础函数、频率、Vpp、偏置和方波参数 |
| `source.output_v2` | `set_source_output_v2` | 单独的 ON/OFF 转换 |
| `source.harmonics_configure_v2` | `configure_source_harmonics_v2` | 谐波配置 |
| `source.modulation_configure_v2` | `configure_source_modulation_v2` | AM/FM/PM/PWM 等调制配置 |
| `source.pulse_configure_v2` | `configure_source_pulse_v2` | Pulse 配置 |
| `source.sweep_configure_v2` | `configure_source_sweep_v2` | Sweep 配置 |
| `source.burst_configure_v2` | `configure_source_burst_v2` | Burst 配置 |
| `source.arbitrary_storage_v2` | `mutate_source_arbitrary_storage_v2` | 创建或覆盖 ARB 存储槽位 |
| `source.arbitrary_select_v2` | `select_source_arbitrary_v2` | 选择并配置已存在的 ARB |
| `source.combine_configure_v2` | `configure_source_combine_v2` | Combine 关系 |
| `source.coupling_configure_v2` | `configure_source_coupling_v2` | Coupling 关系 |
| `source.tracking_configure_v2` | `configure_source_tracking_v2` | Tracking 关系 |
| `source.phase_relation_configure_v2` | `configure_source_phase_relation_v2` | 跨通道相位关系 |
| `source.burst_arm_v2` | `arm_source_burst_v2` | Burst 准备动作 |
| `source.burst_fire_v2` | `fire_source_burst_v2` | Burst 发出动作 |
| `source.sweep_arm_v2` | `arm_source_sweep_v2` | Sweep 准备动作 |
| `source.sweep_fire_v2` | `fire_source_sweep_v2` | Sweep 发出动作 |

保留 ID 不是已注册 capability，也不是实施授权。新增其它 V2 写 ID 必须通过后续 RFC 修订，
不得由插件自行拼接字符串。

Source V2 驱动不接收 `SessionAuthorization`、`InstrumentSessionState` 或 raw transport handle。
核心在授权 phase 中调用已冻结的 driver 方法，driver 只返回公共类型化 model。

核心拥有语义查询计划、descriptor 决策、activation rule、session epoch 和 snapshot consistency。
插件拥有协议查询计划，包括具体 SCPI、合法顺序和解析。driver 只报告按语义计划取得的
类型化值、未取得值的受限诊断，以及实际执行的查询项；不得自行签发
`session_epoch`、`CONSISTENT`、`UNSUPPORTED` 或 `NOT_APPLICABLE`。核心必须根据 descriptor、
anchor 和实际查询记录重建 `Observed`，并在返回 snapshot 前逐项验证。driver 不返回整包
`SourceSnapshotV2`，也不能把协议查询计划或原始响应文本放进公共返回值。

### snapshot-only Service、CLI 与 operation

R2 冻结 Service 签名：

```python
def snapshot_v2(
    self,
    *,
    correlation_id: str | None = None,
) -> SourceSnapshotV2: ...
```

未提供 correlation ID 时由核心生成。Service 在同一独占资源租约、`healthy` session epoch 和
transaction lock 内构造 semantic plan；driver 返回后，核心校验 item、effect、query count、
deadline 和观测字段，再构造 snapshot。`TransportIOError` 与 `SessionHealthError` 原样优先抛出；
公共执行记录不进入 Service 返回值。

`source.snapshot_v2` 的通用 operation metadata 固定为：

```python
OperationSpec(
    operation="source.snapshot_v2",
    instrument_kind="source",
    required_capabilities=("source.snapshot_v2",),
    effect="stateful_read",
    lease_mode="exclusive",
    changed_fields=(),
    restore_coverage="none-read-only",
    session_purpose="normal",
    required_verified_fields=(),
    verification_fields=(
        "source.identity",
        "source.channel.basic",
        "source.channel.output",
    ),
    postcondition_fields=(),
    cleanup_verification_fields=(),
    timeout_source="operation.timeout_ms",
    operation_timeout_ms=5000,
    error_check_minimum="disabled",
    risk_flags=("state_dependent_query",),
)
```

实际 deadline 取 5000 ms、descriptor `query_contract.timeout_ms` 和 connection timeout 的最小值。
operation 本身不消费错误队列；插件协议查询必须使用 `ReplayPolicy.NO_REPLAY`。

CLI 固定为 `wavebench source snapshot-v2 [--config PATH] [--resource RESOURCE]`，不接收 channel、
facet、raw query 或 effect 覆盖参数；它总是查询 descriptor topology 的完整 snapshot。普通模式输出
缩进 JSON，`--json` 使用现有 `wavebench.cli.result.v1` envelope。`result` 是
`wavebench.source.operation.v1`，其 `snapshot` 字段是 `wavebench.source.snapshot.v2`：

```json
{
  "schema": "wavebench.cli.result.v1",
  "status": "ok",
  "exit_code": 0,
  "result": {
    "schema": "wavebench.source.operation.v1",
    "operation": "source.snapshot_v2",
    "context_id": "<safe-token>",
    "correlation_id": "<safe-token>",
    "session_epoch": "<safe-token>",
    "capability_decision": {
      "capability": "source.snapshot_v2",
      "contract_version": "wavebench.source.v2",
      "descriptor_digest": "sha256:<64 lowercase hex>"
    },
    "snapshot": {
      "schema": "wavebench.source.snapshot.v2",
      "type": "SourceSnapshotV2"
    },
    "query": {
      "effect": "pure_read",
      "plan_digest": "sha256:<64 lowercase hex>",
      "query_count": 1
    },
    "session_health": {"before": "healthy", "after": "healthy"},
    "final_state": {"consistency": "consistent", "session_health": "healthy"},
    "evidence_refs": []
  }
}
```

示例中的 snapshot 只省略其余已冻结字段，不表示允许任意 shape。CLI 不提供 raw protocol record，
也不把 device-native revision token、资源串、序列号、命令或响应写入 artifact；revision token 只以
SHA-256 出现在 snapshot consistency 中。

### `OperationSpec` 与入口覆盖

每个新公共 operation 必须冻结：

- required 与 optional capability；
- effect、lease mode 和 session purpose；
- `changed_fields`、`required_verified_fields`、`verification_fields`、
  `postcondition_fields` 和 `cleanup_verification_fields`；
- `restore_coverage`、绝对 deadline 来源、最大步骤和 error policy；
- 是否可能增加端口能量，或在静态配置完成后发出信号；
- 对应 Service、CLI/run 入口和 artifact 字段。

Source V2 operation 在通用 `OperationSpec` 之外绑定以下 typed contract：

```python
class SourceEnergyEffect(StrEnum):
    NONE = "none"
    DECREASE_ONLY = "decrease_only"
    POTENTIAL_WHILE_OFF = "potential_while_off"
    MAY_INCREASE = "may_increase"
    EMIT = "emit"
    UNKNOWN = "unknown"


class SourceStorageEffect(StrEnum):
    NONE = "none"
    READ = "read"
    CREATE = "create"
    REPLACE = "replace"
    DELETE = "delete"
    UNKNOWN = "unknown"


class SourceV1WriteRouteId(StrEnum):
    SET_FREQUENCY = "source_service.set_frequency"
    SET_FUNCTION = "source_service.set_function"
    SET_AMPLITUDE_VPP = "source_service.set_amplitude_vpp"
    SET_SQUARE_DUTY_CYCLE = "source_service.set_square_duty_cycle"
    SET_OUTPUT = "source_service.set_output"
    CONFIGURE_COUPLING = "source_service.configure_coupling"
    CONFIGURE_HARMONICS = "source_service.configure_harmonics"
    CONFIGURE_AM = "source_service.configure_am_modulation"
    CONFIGURE_FM = "source_service.configure_fm_modulation"
    CONFIGURE_PM = "source_service.configure_pm_modulation"
    CONFIGURE_PWM = "source_service.configure_pwm_modulation"
    CONFIGURE_PULSE = "source_service.configure_pulse"
    CONFIGURE_BURST = "source_service.configure_burst"
    TRIGGER_BURST = "source_service.trigger_burst"
    CONFIGURE_SWEEP = "source_service.configure_sweep"
    TRIGGER_SWEEP = "source_service.trigger_sweep"
    UPLOAD_ARBITRARY = "source_service.upload_arbitrary_waveform"
    RESTORE = "source_service.restore_restorable_state"


@dataclass(frozen=True, slots=True)
class SourceOperationContract:
    operation: str
    capability: str
    feature: SourceFeature
    direction: SourceFeatureDirection
    energy_effect: SourceEnergyEffect
    storage_effect: SourceStorageEffect
    required_fields: tuple[SourceFieldId, ...]
    changed_fields: tuple[SourceFieldId, ...]
    postcondition_fields: tuple[SourceFieldId, ...]
    cleanup_verification_fields: tuple[SourceFieldId, ...]
    v1_equivalent_routes: tuple[SourceV1WriteRouteId, ...]
    v1_overlapping_routes: tuple[SourceV1WriteRouteId, ...]
    operation_timeout_ms: int
    main_max_steps: int
    recovery_max_steps: int
    verification_max_steps: int


@dataclass(frozen=True, slots=True)
class SourceAffectedClosure:
    operation: str
    context_id: str
    session_epoch: str
    baseline_snapshot_digest: str
    fields: tuple[SourceFieldRef, ...]
    required_off_outputs: tuple[SourceScopeRef, ...]
    emergency_off_outputs: tuple[SourceScopeRef, ...]
    restore_order: tuple[SourceFieldRef, ...]
    non_restorable_fields: tuple[SourceFieldRef, ...]
    closure_digest: str
```

`SourceOperationContract` 是静态注册表项；`SourceAffectedClosure` 由核心根据 request、topology、
runtime profile 和当前跨通道关系在任何 mutation 前实例化。closure 只能扩大静态字段模板，
不能遗漏 descriptor 声明的依赖。`closure_digest` 使用 canonical JSON SHA-256，并绑定 context、
epoch 和 baseline。R4 已把这些两个 model、固定 phase 和 core-only coordinator 写入核心；它们
仍不注册任何 Source V2 写 capability，也不提供 Service、CLI 或 run plan 写入口。

`v1_equivalent_routes` 与 `v1_overlapping_routes` 不得重复。前者表示同一用户意图，后者表示虽然
入口名称不同，但可能修改该 operation 的字段闭包或经同一路径发出信号。未出现在当前已注册 V2
写合同中的 route 仍需在全量审计矩阵中标记为不相交，不能因漏填 tuple 自动视为安全。

`UNKNOWN` energy/storage effect 在 I/O 前拒绝。`POTENTIAL_WHILE_OFF` 要求全部
`required_off_outputs` 已通过 fresh readback 证明 OFF，并且 operation 本身不能打开输出。
`MAY_INCREASE` 与 `EMIT` 必须通过统一预算门。`DECREASE_ONLY` 不需要预算，但仍需 access、
session、operation context 和 postcondition。

只要 descriptor 声明 V2 写 capability，以下路径就必须进入同一套核心安全决策路径：

| 路径 | 统一要求 |
| --- | --- |
| V2 输出 ON | fresh 一致 snapshot + 预算 + 写后回读 |
| V1 同义写入口调用双合同驱动 | 在 Service 边界映射到对应 V2 operation，无法无损映射时在 I/O 前拒绝 |
| `source.arb_load output_on=true` | 先在输出 OFF 的配置 phase 完成上传或选择，再用 fresh snapshot 签发只授权下一次 ON 的新决定 |
| 输出 ON 时的 Source V2 setter/patch | 首版在 I/O 前拒绝；未来若允许 live mutation，必须对完整目标状态使用专项预算合同 |
| arm/fire/trigger | 在可能发出信号前完成预算与接线证据检查 |
| 恢复为 ON | 作为独立、显式授权的 ON 操作重新计算预算 |

V1 驱动未 opt in 时继续使用现有 V1 路径，不伪装成已获得 Source V2 复合安全保证。
Source V2 的首版 live-mutation 禁令不追溯改变 V1 驱动的既有行为，也不表示硬件本身不支持
ON 状态写入。

每个 V2 写 capability 必须在 `SourceOperationContract` 中登记其 V1 等价入口、重叠字段和可能发出
信号的间接入口。双合同驱动声明该 capability 后，只有落入这些集合的 V1 路径必须映射到 V2
operation 或在 I/O 前拒绝；字段闭包完全不相交的 V1 operation 可以继续走 V1。核心仍需审计完整
V1 写表面，防止遗漏隐式副作用。OFF 不需要复合预算，但不能绕过 access、session health、
operation context 和必要回读。插件不能通过保留旧方法名重新引入同字段或同发信号路径的旁路。

`source.output_v2` 的等价集合至少包括 V1 `set_output(ON)`、ARB 的 `output_on=True`、会发出信号的
trigger/fire 和恢复 ON；`source.arbitrary_storage_v2` 至少接管 V1 上传入口，但不因此接管无关的
频率 setter。若一个 V1 方法把上传、选择和 ON 合并为一次调用，它不能部分映射，必须拆分为多个
V2 operation 或在 I/O 前拒绝。该规则允许按 feature 渐进 opt in，同时保证已迁移字段没有 V1 旁路。

### Service 与 operation context

R4 实现内部 `SourceOperationContextCoordinator`。它复用 `InstrumentSessionState`、
`SessionTransactionCoordinator` 和 guarded transport，但不直接复用
`ScopeOperationContextCoordinator` 的 scope/binary 假设。

冻结 phase：

```python
class SourceOperationPhase(StrEnum):
    PREFLIGHT = "preflight"
    MAIN = "main"
    POSTCONDITION = "postcondition"
    FAILURE_SAFE_STATE = "failure_safe_state"
    FAILURE_RESTORE = "failure_restore"
    CLEANUP_VERIFICATION = "cleanup_verification"
```

正常顺序：

```text
offline validation
  -> PREFLIGHT
  -> MAIN
  -> POSTCONDITION
  -> terminal success
```

可能发生 mutation 后的失败顺序：

```text
close MAIN authorization
  -> FAILURE_SAFE_STATE
  -> FAILURE_RESTORE
  -> CLEANUP_VERIFICATION
  -> terminal failure artifact
```

phase purpose 固定为：`PREFLIGHT`、`POSTCONDITION` 和 `CLEANUP_VERIFICATION` 使用
`verification`；`MAIN` 使用 `normal`；`FAILURE_SAFE_STATE` 和 `FAILURE_RESTORE` 使用
`recovery`。verification phase 禁止 write；每个 phase 最多进入一次，授权严格顺序且不能嵌套。

`PREFLIGHT` 负责 fresh snapshot、runtime profile、受影响字段闭包、baseline、输出 OFF 前置、
端接证据和预算。`MAIN` 只执行已冻结 request。`POSTCONDITION` 独立读取目标字段和未修改闭包。
`FAILURE_SAFE_STATE` 只执行预先计算的 emergency OFF；`FAILURE_RESTORE` 只恢复不会重新供能的
明确字段；`CLEANUP_VERIFICATION` 独立证明 OFF、恢复字段和 session 证据。

每个 context 绑定 `context_id`、`operation_id`、`correlation_id`、`session_epoch`、资源租约、
`hard_deadline`、更早的 `main_deadline`、不可退还的 cleanup reserve 和受影响字段闭包。
normal phase 不得消耗 cleanup reserve；recovery/verification phase 的实际 deadline 取静态上限与
`hard_deadline` 剩余时间的较小值。同一个 context 中最多存在一个 active authorization。

cleanup reserve 为 operation timeout 的 20%，最少 1000 ms、最多 5000 ms，但不得超过当前
hard-deadline 剩余时间的一半。caller deadline 只能继续收紧 hard deadline。若剩余时间不足以
保留至少 1 ms 的 main 时间，operation 在 transport I/O 前拒绝。

## 类型化能力模型

### feature 与方向

R2 冻结以下 enum；snapshot-only descriptor 只能声明 `READ`，其余方向保留给后续写修订：

```python
class SourceFeature(StrEnum):
    BASIC = "basic"
    OUTPUT = "output"
    HARMONICS = "harmonics"
    MODULATION = "modulation"
    SWEEP = "sweep"
    BURST = "burst"
    PULSE = "pulse"
    ARBITRARY = "arbitrary"
    COUNTER = "counter"
    REFERENCE_CLOCK = "reference_clock"
    SYNC = "sync"
    CASCADE = "cascade"
    COMBINE = "combine"
    TRACKING = "tracking"
    COUPLING = "coupling"
    COPY = "copy"
    PHASE_RELATION = "phase_relation"
    SHARED_POWER = "shared_power"


class SourceFeatureDirection(StrEnum):
    READ = "read"
    CONFIGURE = "configure"
    ENABLE = "enable"
    DISABLE = "disable"
    ARM = "arm"
    FIRE = "fire"


class SourceWaveformKind(StrEnum):
    SINE = "sine"
    SQUARE = "square"
    RAMP = "ramp"
    PULSE = "pulse"
    NOISE = "noise"
    DC = "dc"
    ARBITRARY = "arbitrary"
    OTHER = "other"


class SourceFrequencyMode(StrEnum):
    FIXED = "fixed"
    SWEEP = "sweep"
    LIST = "list"
    UNKNOWN = "unknown"


class SourceArbitraryPlaybackMode(StrEnum):
    DDS = "dds"
    TRUE_ARB = "true_arb"
    UNKNOWN = "unknown"


SourceAnchorValue: TypeAlias = (
    bool | SourceWaveformKind | SourceFrequencyMode | SourceArbitraryPlaybackMode
)
```

facet 辅助 enum 也使用封闭 value 集：

- `SourceAmplitudeUnit`：`vpp`、`vrms`、`dbm`、`v`、`unknown`；
- `SourceOutputPolarity`：`normal`、`inverted`、`unknown`；
- `SourceLoadKind`：`high_impedance`、`resistive`、`unknown`；
- `SourceModulationKind`：`am`、`dsb_am`、`fm`、`pm`、`pwm`、`ask`、`fsk`、`psk`、`other`；
- `SourceModulationSource`：`internal`、`external`、`channel`、`unknown`；
- `SourceModulationParameterKind`：`depth_percent`、`frequency_deviation_hz`、
  `phase_deviation_deg`、`duty_deviation_percent`、`symbol_rate_hz`；
- `SourceSweepSpacing`：`linear`、`logarithmic`、`step`、`unknown`；
- `SourceTriggerSource`：`internal`、`external`、`manual`、`bus`、`unknown`；
- `SourceTriggerSlope`：`positive`、`negative`、`either`、`unknown`；
- `SourceTriggerOutput`：`off`、`positive`、`negative`、`unknown`；
- `SourceBurstMode`：`triggered`、`gated`、`infinity`、`unknown`；
- `SourceGatePolarity`：`normal`、`inverted`、`unknown`；
- `SourcePulseHoldBasis`：`width`、`duty`、`unknown`；
- `SourceCounterMeasurementKind`：`frequency_hz`、`period_s`、`duty_percent`、
  `positive_width_s`、`negative_width_s`、`unknown`；
- `SourceInputCoupling`：`ac`、`dc`、`unknown`；
- `SourceReferenceClockMode`：`internal`、`external`、`auto`、`unknown`。

方向必须按 feature、通道和模式声明。例如 Harmonic `read` 不表示 `configure`；
`disable` 不表示 `enable`；Burst 的内部 `fire` 不表示外部 Gate 已完成 A5 接线验收。
这种拆分允许驱动诚实声明「可以安全关闭，但尚不能证明可安全开启」。

机器可读 feature ID 按作用域分为：

- channel：`basic`、`output`、`harmonics`、`modulation`、`sweep`、`burst`、`pulse`、`arbitrary`；
- system：`counter`、`reference_clock`、`sync`、`cascade`；
- cross-channel：`combine`、`tracking`、`coupling`、`copy`、`phase_relation`、`shared_power`。

正文中的 Harmonic、Sync、Combine 等首字母大写名称只是展示术语；注册表、artifact 和
descriptor 一律使用上述小写 ID，不允许通过大小写或单复数增加别名。

feature 集合是核心注册表，不接受插件自定义任意字符串作为新安全语义。厂商专用功能可继续
使用独立 capability，但未经核心注册时不进入通用 Source V2 预算或恢复。

R2 冻结以下 11 个只读 capability profile。布尔字段只声明该值能否读取，不提供写授权；tuple
使用 enum value 或 ID 的升序并且不重复。

```python
@dataclass(frozen=True, slots=True)
class SourceBasicCapabilityProfile:
    waveform_kinds: tuple[SourceWaveformKind, ...]
    frequency_modes: tuple[SourceFrequencyMode, ...]
    amplitude_units: tuple[SourceAmplitudeUnit, ...]
    offset_readable: bool
    phase_readable: bool
    square_duty_readable: bool


@dataclass(frozen=True, slots=True)
class SourceOutputCapabilityProfile:
    output_readable: bool
    display_load_readable: bool
    polarity_readable: bool


@dataclass(frozen=True, slots=True)
class SourceHarmonicCapabilityProfile:
    minimum_order: int
    maximum_order: int
    amplitude_kinds: tuple[ComponentAmplitudeKind, ...]
    completeness_modes: tuple[HarmonicCompleteness, ...]


@dataclass(frozen=True, slots=True)
class SourceModulationCapabilityProfile:
    kinds: tuple[SourceModulationKind, ...]
    sources: tuple[SourceModulationSource, ...]
    parameter_kinds: tuple[SourceModulationParameterKind, ...]
    inactive_readable: bool


@dataclass(frozen=True, slots=True)
class SourceSweepCapabilityProfile:
    spacing_modes: tuple[SourceSweepSpacing, ...]
    trigger_sources: tuple[SourceTriggerSource, ...]
    timing_readable: bool
    marker_readable: bool


@dataclass(frozen=True, slots=True)
class SourceBurstCapabilityProfile:
    modes: tuple[SourceBurstMode, ...]
    trigger_sources: tuple[SourceTriggerSource, ...]
    timing_readable: bool
    gate_readable: bool


@dataclass(frozen=True, slots=True)
class SourcePulseCapabilityProfile:
    hold_modes: tuple[SourcePulseHoldBasis, ...]
    delay_readable: bool
    transitions_readable: bool


@dataclass(frozen=True, slots=True)
class SourceArbitraryCapabilityProfile:
    playback_modes: tuple[SourceArbitraryPlaybackMode, ...]
    selection_readable: bool
    storage_metadata_readable: bool
    sample_rate_readable: bool


@dataclass(frozen=True, slots=True)
class SourceCounterCapabilityProfile:
    input_ids: tuple[str, ...]
    measurement_kinds: tuple[SourceCounterMeasurementKind, ...]
    configuration_readable: bool
    query_effect: SourceQueryEffect


@dataclass(frozen=True, slots=True)
class SourceClockSyncCapabilityProfile:
    reference_clock_modes: tuple[SourceReferenceClockMode, ...]
    sync_readable: bool
    cascade_readable: bool


@dataclass(frozen=True, slots=True)
class SourceCrossChannelCapabilityProfile:
    relation_kinds: tuple[SourceFeature, ...]
    supported_channel_sets: tuple[tuple[int, ...], ...]
    relation_graph_readable: bool
    shared_power_constraint_readable: bool
```

`SourceFeatureProfile` 是上述公共 profile 的封闭 union：

```python
SourceFeatureProfile: TypeAlias = (
    SourceBasicCapabilityProfile
    | SourceOutputCapabilityProfile
    | SourceHarmonicCapabilityProfile
    | SourceModulationCapabilityProfile
    | SourceSweepCapabilityProfile
    | SourceBurstCapabilityProfile
    | SourcePulseCapabilityProfile
    | SourceArbitraryCapabilityProfile
    | SourceCounterCapabilityProfile
    | SourceClockSyncCapabilityProfile
    | SourceCrossChannelCapabilityProfile
)
```

feature 与 profile 类型使用固定映射；例如 `HARMONICS` 只能使用
`SourceHarmonicCapabilityProfile`。union 新增成员属于公共合同扩展，必须由核心注册并补版本门。

### facet 作用域

```python
class SourceFacetScope(str, Enum):
    CHANNEL = "channel"
    CHANNEL_SET = "channel_set"
    INSTRUMENT = "instrument"
    INPUT = "input"


@dataclass(frozen=True, slots=True)
class SourceScopeRef:
    scope: SourceFacetScope
    channel: int | None = None
    channels: tuple[int, ...] = ()
    input_id: str | None = None


@dataclass(frozen=True, slots=True)
class SourceTopologyContract:
    channels: tuple[int, ...]
    input_ids: tuple[str, ...] = ()
```

`SourceScopeRef` 必须且只能使用与 scope 对应的字段：`CHANNEL` 使用一个正整数 `channel`；
`CHANNEL_SET` 使用至少两个、递增且不重复的 `channels`；`INPUT` 使用一个核心安全 token
格式的 `input_id`；`INSTRUMENT` 不携带这些字段。所有通道必须属于 topology。
`SourceTopologyContract.channels` 必须递增、唯一且非空；`input_ids` 必须排序稳定且不重复。

- `basic`、`output`、`harmonics`、`modulation`、`pulse`、`sweep`、`burst` 和 `arbitrary`
  通常属于 `CHANNEL`；
- `combine`、`coupling` 和 `tracking` 属于 `CHANNEL_SET`，并明确列出关系参与者；
- `reference_clock`、`sync` 和 `cascade` 属于 `INSTRUMENT` 或 `CHANNEL_SET`；
- `counter` 通常属于独立 `INPUT`，只有存在已声明路由关系时才参与输出预算。

descriptor 中的 topology 是静态上界。实际 capability 必须根据已验证的型号、固件、选件和
当前连接代次收紧。

### 字段引用

OperationSpec、query plan、snapshot、预算、恢复和 artifact 统一使用结构化字段引用，不再使用
`"coupling"` 或 `"trigger"` 这类无作用域字符串：

```python
class SourceFieldId(StrEnum):
    IDENTITY = "source.identity"
    BASIC = "source.channel.basic"
    OUTPUT = "source.channel.output"
    DISPLAY_LOAD = "source.channel.display_load"
    HARMONICS = "source.channel.harmonics"
    MODULATION = "source.channel.modulation"
    SWEEP = "source.channel.sweep"
    BURST = "source.channel.burst"
    PULSE = "source.channel.pulse"
    ARBITRARY_SELECTION = "source.channel.arbitrary_selection"
    ARBITRARY_STORAGE = "source.channel.arbitrary_storage"
    ARM_STATE = "source.channel.arm_state"
    TRIGGER_STATE = "source.channel.trigger_state"
    COMBINE = "source.cross_channel.combine"
    COUPLING = "source.cross_channel.coupling"
    TRACKING = "source.cross_channel.tracking"
    COPY = "source.cross_channel.copy"
    PHASE_RELATION = "source.cross_channel.phase_relation"
    RELATION_GRAPH = "source.cross_channel.relation_graph"
    REFERENCE_CLOCK = "source.instrument.reference_clock"
    SYNC = "source.instrument.sync"
    CASCADE = "source.instrument.cascade"
    SHARED_POWER = "source.instrument.shared_power"
    COUNTER = "source.input.counter"


@dataclass(frozen=True, slots=True)
class SourceFieldRef:
    field: SourceFieldId
    target: SourceScopeRef
```

每个 `SourceFieldId` 固定允许的 scope；构造时必须校验 field 与 target。tuple 序列化按
`field.value`、scope、channel/channels/input_id 的稳定顺序排序。厂商字段只能映射到这些 ID，
不能进入公共字段闭包。

### 跨通道关系图

```python
class SourceSignalPathKind(StrEnum):
    INTERNAL_WAVEFORM = "internal_waveform"
    OUTPUT_PORT = "output_port"
    CONFIG_TRACKING = "config_tracking"
    SHARED_RESOURCE = "shared_resource"


@dataclass(frozen=True, slots=True)
class SourceRelationEdge:
    relation_id: str
    feature: SourceFeature
    sources: tuple[int, ...]
    targets: tuple[int, ...]
    signal_path: SourceSignalPathKind
    affected_fields: tuple[SourceFieldId, ...]
    implicit_changed_fields: tuple[SourceFieldId, ...]


@dataclass(frozen=True, slots=True)
class SourceRelationGraph:
    channels: tuple[int, ...]
    edges: tuple[SourceRelationEdge, ...]
```

`sources` 和 `targets` 非空、递增且属于 topology。Combine 使用有向 edge；来源通道的输出 relay
是否参与由 `signal_path` 明示，不能从 source output 状态推断。共享功率使用
`SHARED_RESOURCE`，其 participants 为 edge 的 sources/targets 并集。

R2 首版拒绝形成有向环的关系图，也拒绝无法解析参与者的关系。affected closure 从目标 operation
沿 edge 依赖展开，必须加入 `implicit_changed_fields` 和所有可能发出信号的 target output。
例如开启 Combine 会隐式同步两通道 load 时，两通道 `DISPLAY_LOAD` 都属于 changed fields。

### activation rule

activation rule 只允许引用核心注册的 canonical anchor，不接受字符串表达式、正则表达式、
Python callback 或厂商命令：

```python
class SourceAnchorField(StrEnum):
    WAVEFORM_KIND = "waveform_kind"
    FREQUENCY_MODE = "frequency_mode"
    OUTPUT_ENABLED = "output_enabled"
    HARMONICS_ENABLED = "harmonics_enabled"
    MODULATION_ENABLED = "modulation_enabled"
    SWEEP_ENABLED = "sweep_enabled"
    BURST_ENABLED = "burst_enabled"
    ARBITRARY_PLAYBACK_MODE = "arbitrary_playback_mode"
    COMBINE_ENABLED = "combine_enabled"
    COUPLING_ENABLED = "coupling_enabled"
    TRACKING_ENABLED = "tracking_enabled"


@dataclass(frozen=True, slots=True)
class SourceActivationPredicate:
    field: SourceAnchorField
    equals: SourceAnchorValue


@dataclass(frozen=True, slots=True)
class SourceActivationRule:
    predicates: tuple[SourceActivationPredicate, ...]
```

predicate 相对于当前 semantic query item 的 target 求值。单条 rule 的 predicate 使用 AND；
多个 rule 使用 OR。predicate 按 field 排序且不重复，`equals` 类型必须与 anchor 匹配。首版没有
NOT、任意算术、跨 target 引用或插件回调；无法无损表示的激活条件，对应 facet 保持 `UNKNOWN`。

### 约束适用域与运行时收窄

```python
@dataclass(frozen=True, slots=True)
class ClosedFloatInterval:
    minimum: float
    maximum: float


@dataclass(frozen=True, slots=True)
class SourceConstraintApplicability:
    models: tuple[str, ...] = ()
    firmware_ids: tuple[str, ...] = ()
    option_ids: tuple[str, ...] = ()
    waveform_kinds: tuple[SourceWaveformKind, ...] = ()
    frequency_hz: ClosedFloatInterval | None = None
    amplitude_vpp: ClosedFloatInterval | None = None
    offset_v: ClosedFloatInterval | None = None


@dataclass(frozen=True, slots=True)
class SourceRuntimeIdentity:
    manufacturer: str
    model: str
    firmware_id: str
    option_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceRuntimeCapabilityProfile:
    session_epoch: str
    descriptor_digest: str
    identity: SourceRuntimeIdentity
    features: tuple[SourceFeatureCapability, ...]
```

数值区间必须有限、拒绝 `bool`，并满足 `minimum <= maximum`。型号和固件使用精确 ID；R2
不定义厂商固件版本排序，也不接受插件回调比较版本。安全约束若没有覆盖当前型号、固件、选件、
波形、频率、幅度、偏置和显示负载，就不能进入 `HARD_CONSERVATIVE` 证明。

`SourceRuntimeCapabilityProfile` 由核心构造，绑定当前 epoch。核心对 descriptor 与 runtime identity
取交集；运行时只能删除 feature、direction、channel 或 constraint，不能增加 descriptor 未声明的
内容。`descriptor_digest` 使用 canonical JSON 的 SHA-256，格式为 `sha256:<64 lowercase hex>`。
driver 只在查询执行记录中报告类型化 identity 观测，不自行构造 runtime profile。

### 支持状态

```python
class SupportState(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
```

- `SUPPORTED` 只表示 descriptor 声明该功能存在，仍需检查方向、通道、模式、运行时状态和安全证据；
- `UNSUPPORTED` 只能在已知型号或固件范围中使用，不发送探测命令；
- `UNKNOWN` 不得作为运行时写入授权。

## `Observed[T]` 合同

### 可用性状态

```python
class Availability(str, Enum):
    VALUE = "value"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"
    NOT_QUERIED = "not_queried"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Observed(Generic[T]):
    availability: Availability
    value: T | None = None
    reason_code: SourceReasonCode | None = None
    evidence_refs: tuple[str, ...] = ()
```

R2 的 `SourceReasonCode` 注册表固定为：

```text
descriptor_unsupported
support_unknown
not_requested
inactive_by_anchor
anchor_unavailable
response_missing_field
response_invalid_value
driver_skipped_optional
query_deadline_exceeded
query_limit_exceeded
protocol_record_invalid
required_observation_missing
session_not_healthy
consistency_unproven
consistency_drifted
```

插件只能在 `SourceProtocolQueryRecord` 中使用这些 code；最终 availability 仍由核心结合 descriptor、
activation 和执行记录构造。新增 reason code 属于 append-only 公共合同变更。

### 构造不变量

1. `VALUE` 必须携带通过对应 model 校验的非 `None` 值。`False`、`0` 和空 tuple 是合法值，
   不得使用真值判断。
2. 非 `VALUE` 必须使用 `value=None`。
3. `UNAVAILABLE`、`UNKNOWN` 和 `NOT_QUERIED` 必须携带核心注册的 `reason_code`。
4. `UNSUPPORTED` 必须能追溯到 descriptor 中的 feature/model 决策，不使用查询超时推断。
5. `NOT_APPLICABLE` 必须由已验证 anchor 和已注册 activation rule 计算；插件自由文本不是证明。
6. `NOT_QUERIED` 只表示查询计划主动跳过，不表示设备不支持。
7. `UNAVAILABLE` 只表示该值按当前合同应存在，而且完整响应已经取得、同步仍为 `proven`、
   session 仍为 `healthy`，但响应在语义上缺字段或无法解析。
8. `UNKNOWN` 表示支持或语义本身无法确定，不是查询失败的替代状态。
9. `evidence_refs` 只保存稳定、脱敏的证据 ID 或摘要，不保存原始响应、本地路径或凭据。
10. 安全相关的 `VALUE` 必须携带证据来源；纯展示字段可以不携带。
11. 非有限浮点数不能构造为 `VALUE`，也不能进入 JSON artifact。
12. facet 为 `VALUE` 只表示 facet 对象存在；其成员仍可分别为非 `VALUE`。

`TransportIOError` 和 `SessionHealthError` 不得降级成 `Observed`。一旦查询使 session 进入
`uncertain` 或 `poisoned`，核心立即停止剩余 snapshot 查询并保留结构化异常优先级；
插件不能用 `UNAVAILABLE` 吞掉连接边界变化。

### 序列化

Source V2 artifact 必须序列化 `availability`、值的类型化表示、`reason_code` 和证据摘要。
不得将非 `VALUE` 展平为 JSON `null` 后丢失原因。

R2 的 canonical serializer 规则为：dataclass 必须带 `type` 判别字段；enum 序列化为 value；tuple
序列化为 array；object key 排序；使用 UTF-8、`ensure_ascii=false`、无多余空白，并拒绝 NaN、
Infinity、未知 object 和非字符串 mapping key。摘要固定为该 canonical JSON 的
`sha256:<64 lowercase hex>`。operation artifact 可以嵌入 snapshot document，但不能嵌入插件的
`SourceQueryExecutionRecord`。

V2 → V1 adapter 可以将非 `VALUE` 映射为 `None`，但该视图只允许用于兼容显示和旧公共返回类型。

## facet 状态模型

### 分层 snapshot

R2 冻结 8 个通道 facet、2 个非通道状态和顶层 snapshot：

```python
@dataclass(frozen=True, slots=True)
class BasicWaveFacet:
    waveform_kind: Observed[SourceWaveformKind]
    waveform_id: Observed[str]
    frequency_mode: Observed[SourceFrequencyMode]
    frequency_hz: Observed[float]
    amplitude: Observed[SourceAmplitude]
    offset_v: Observed[float]
    phase_deg: Observed[float]
    square_duty_cycle_percent: Observed[float]


@dataclass(frozen=True, slots=True)
class OutputFacet:
    enabled: Observed[bool]
    display_load: Observed[SourceDisplayLoad]
    polarity: Observed[SourceOutputPolarity]


@dataclass(frozen=True, slots=True)
class HarmonicFacet:
    enabled: Observed[bool]
    completeness: Observed[HarmonicCompleteness]
    maximum_supported_order: Observed[int]
    components: Observed[tuple[SourceHarmonicComponentV2, ...]]


@dataclass(frozen=True, slots=True)
class ModulationFacet:
    enabled: Observed[bool]
    kind: Observed[SourceModulationKind]
    source: Observed[SourceModulationSource]
    parameters: Observed[tuple[SourceModulationParameter, ...]]
    internal_frequency_hz: Observed[float]
    internal_waveform_kind: Observed[SourceWaveformKind]


@dataclass(frozen=True, slots=True)
class SweepFacet:
    enabled: Observed[bool]
    start_hz: Observed[float]
    stop_hz: Observed[float]
    spacing: Observed[SourceSweepSpacing]
    steps: Observed[int]
    sweep_time_s: Observed[float]
    start_hold_s: Observed[float]
    stop_hold_s: Observed[float]
    return_time_s: Observed[float]
    trigger: Observed[SourceTriggerState]
    marker: Observed[SourceSweepMarker]


@dataclass(frozen=True, slots=True)
class BurstFacet:
    enabled: Observed[bool]
    mode: Observed[SourceBurstMode]
    cycles: Observed[int]
    phase_deg: Observed[float]
    internal_period_s: Observed[float]
    delay_s: Observed[float]
    gate_polarity: Observed[SourceGatePolarity]
    trigger: Observed[SourceTriggerState]


@dataclass(frozen=True, slots=True)
class PulseFacet:
    hold_basis: Observed[SourcePulseHoldBasis]
    width_s: Observed[float]
    duty_cycle_percent: Observed[float]
    delay_s: Observed[float]
    leading_transition_s: Observed[float]
    trailing_transition_s: Observed[float]


@dataclass(frozen=True, slots=True)
class ArbitraryFacet:
    selected_waveform_id: Observed[str]
    playback_mode: Observed[SourceArbitraryPlaybackMode]
    playback_frequency_hz: Observed[float]
    sample_rate_hz: Observed[float]
    point_count: Observed[int]
    storage_digest: Observed[str]


@dataclass(frozen=True, slots=True)
class SourceSystemStateV2:
    counters: tuple[SourceCounterInputState, ...]
    reference_clock: Observed[SourceReferenceClockState]
    sync: Observed[SourceSyncState]
    cascade: Observed[SourceCascadeState]


@dataclass(frozen=True, slots=True)
class SourceCrossChannelStateV2:
    relations: tuple[SourceRelationState, ...]
    relation_graph: Observed[SourceRelationGraph]
    shared_power: Observed[SourceSharedPowerState]


@dataclass(frozen=True, slots=True)
class SourceChannelStateV2:
    channel: int
    basic: Observed[BasicWaveFacet]
    output: Observed[OutputFacet]
    harmonics: Observed[HarmonicFacet]
    modulation: Observed[ModulationFacet]
    sweep: Observed[SweepFacet]
    burst: Observed[BurstFacet]
    pulse: Observed[PulseFacet]
    arbitrary: Observed[ArbitraryFacet]


@dataclass(frozen=True, slots=True)
class SourceSnapshotV2:
    snapshot_id: str
    context_id: str
    correlation_id: str
    captured_at_utc: str
    runtime_profile: SourceRuntimeCapabilityProfile
    channels: tuple[SourceChannelStateV2, ...]
    system: Observed[SourceSystemStateV2]
    cross_channel: Observed[SourceCrossChannelStateV2]
    consistency: SourceSnapshotConsistency
    plan_digest: str
    query_count: int
    session_health_before: str
    session_health_after: str
```

嵌套辅助类型也使用 fixed dataclass，不使用 mapping：`SourceAmplitude(value, unit)`、
`SourceDisplayLoad(kind, resistance_ohm)`、`SourceComponentAmplitude(kind, value)`、
`SourceHarmonicComponentV2(order, amplitude, phase_deg)`、
`SourceModulationParameter(kind, value)`、`SourceTriggerState(source, slope, output)`、
`SourceSweepMarker(enabled, frequency_hz)`、`SourceCounterMeasurementV2(kind, value)`、
`SourceCounterInputState(input_id, enabled, measurements, coupling, impedance_ohm, attenuation,
gate_time_s, trigger_level_v, statistics_enabled)`、
`SourceReferenceClockState(mode, frequency_hz, locked)`、
`SourceSyncState(enabled, polarity, source_channel)`、`SourceCascadeState(enabled, role)`、
`SourceRelationState(feature, channels, enabled)` 和
`SourceSharedPowerState(participants, active_power_upper_w, hard_limit_w)`。

每个数值必须有限；频率、时间、阻抗、点数、阶次和功率等非负量不得为负；百分比范围为
`[0, 100]`；相位范围为 `[0, 360]`。`storage_digest` 使用 SHA-256 格式。counter、relation、
component 和 parameter tuple 按公共 key 排序且不重复。

### 一致性模型

```python
class SnapshotConsistencyState(str, Enum):
    CONSISTENT = "consistent"
    DRIFTED = "drifted"
    UNPROVEN = "unproven"


@dataclass(frozen=True, slots=True)
class SourceSnapshotConsistency:
    state: SnapshotConsistencyState
    session_epoch: str
    anchor_fields: tuple[SourceFieldRef, ...]
    anchor_digest_before: str
    anchor_digest_after: str | None
    device_revision_token_before: str | None
    device_revision_token_after: str | None
    reason_code: SourceReasonCode | None = None
```

- `CONSISTENT` 要求查询前后 anchor 规范化值一致，并且所有必需查询在同一 epoch 和 deadline 内完成；
- `DRIFTED` 表示 anchor 已变化；
- `UNPROVEN` 表示复读、epoch 或必需证据不完整；
- device-native revision token 与核心摘要是两个独立字段，不能互相冒充。driver record 可以携带
  原生 token，但核心 snapshot 只保存 token 的 SHA-256，避免把厂商私有修订文本写入 artifact。没有原生 token 时，
  核心仍对 canonical anchor JSON 计算 SHA-256；摘要格式为 `sha256:<64 lowercase hex>`；
- `CONSISTENT` 要求两次 anchor digest 相同；若原生 token 在前后都存在，也必须相同；
- 写入前必须在同一独占资源租约和 session 事务范围内立即比较 baseline，不能依赖旧 snapshot。

### 状态依赖查询计划

查询 effect 使用单一枚举，避免多个布尔值互相矛盾：

```python
class SourceQueryEffect(str, Enum):
    PURE_READ = "pure_read"
    STATEFUL_CONSUMING_READ = "stateful_consuming_read"
    REQUIRES_SELECTOR_WRITE = "requires_selector_write"
    UNKNOWN_EFFECT = "unknown_effect"


class SourceQueryPhase(StrEnum):
    ANCHOR_BEFORE = "anchor_before"
    FACET = "facet"
    ANCHOR_AFTER = "anchor_after"


@dataclass(frozen=True, slots=True)
class SourceFacetQueryContract:
    feature: SourceFeature
    scope: SourceFacetScope
    fields: tuple[SourceFieldId, ...]
    activation_any: tuple[SourceActivationRule, ...]
    effect: SourceQueryEffect
    max_queries: int
    required: bool = False


@dataclass(frozen=True, slots=True)
class SourceQueryContract:
    anchor_fields: tuple[SourceFieldId, ...]
    facets: tuple[SourceFacetQueryContract, ...]
    max_queries: int
    timeout_ms: int


@dataclass(frozen=True, slots=True)
class SourceSemanticQueryItem:
    item_id: str
    phase: SourceQueryPhase
    feature: SourceFeature
    target: SourceScopeRef
    fields: tuple[SourceFieldRef, ...]
    activation_any: tuple[SourceActivationRule, ...]
    required: bool
    effect: SourceQueryEffect
    max_queries: int


@dataclass(frozen=True, slots=True)
class SourceSemanticQueryPlan:
    contract_version: Literal["wavebench.source.v2"]
    plan_id: str
    items: tuple[SourceSemanticQueryItem, ...]
    allowed_effects: tuple[SourceQueryEffect, ...]
    max_queries: int
    deadline_monotonic: float


class SourceQueryItemOutcome(StrEnum):
    OBSERVED = "observed"
    SEMANTIC_UNAVAILABLE = "semantic_unavailable"
    SKIPPED = "skipped"


SourceObservationValue: TypeAlias = (
    SourceRuntimeIdentity
    | BasicWaveFacet
    | OutputFacet
    | HarmonicFacet
    | ModulationFacet
    | SweepFacet
    | BurstFacet
    | PulseFacet
    | ArbitraryFacet
    | SourceDisplayLoad
    | SourceCounterInputState
    | SourceReferenceClockState
    | SourceSyncState
    | SourceCascadeState
    | SourceRelationState
    | SourceRelationGraph
    | SourceSharedPowerState
    | bool
    | str
)


@dataclass(frozen=True, slots=True)
class SourceTypedObservation:
    field: SourceFieldRef
    value: SourceObservationValue
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceProtocolQueryRecord:
    item_id: str
    effect: SourceQueryEffect
    outcome: SourceQueryItemOutcome
    query_count: int
    observations: tuple[SourceTypedObservation, ...] = ()
    reason_code: SourceReasonCode | None = None


@dataclass(frozen=True, slots=True)
class SourceQueryExecutionRecord:
    contract_version: Literal["wavebench.source.v2"]
    plan_id: str
    items: tuple[SourceProtocolQueryRecord, ...]
    query_count: int
    device_revision_token_before: str | None = None
    device_revision_token_after: str | None = None
```

`UNKNOWN_EFFECT` 不允许普通 snapshot 发送查询。`REQUIRES_SELECTOR_WRITE` 只能由独立、
受控的 stateful snapshot operation 使用，不能进入普通 profile read。

`plan_id` 和 `item_id` 由核心生成，使用短安全 token。`items` 按 phase 顺序排列；
`ANCHOR_BEFORE` 和 `ANCHOR_AFTER` 必须覆盖 `anchor_fields` 的同一组展开后字段。`max_queries`、item 上限和 deadline
必须为正且不得超过 descriptor 与 OperationSpec 的交集。第一阶段的 `source.snapshot_v2`
只允许 `PURE_READ`；消费型查询和 selector-write snapshot 需要独立 operation，不能通过扩大
`allowed_effects` 偷渡。

查询顺序：

1. 查询身份、输出、基础波形、主模式和其他 anchor；
2. 根据 descriptor 中的 typed activation rule 选择合法 facet 查询；
3. 对未激活、不支持或主动跳过的 facet 构造准确 `Observed`；
4. 重新查询 anchor；
5. 生成 `SourceSnapshotConsistency`。

核心签发语义查询计划；插件将每个 item 转换为自己的协议查询计划，选择具体 SCPI、合法顺序
和解析流程。driver 返回类型化观测和不含命令文本的 `SourceProtocolQueryRecord`。核心计算
`UNSUPPORTED`、`NOT_APPLICABLE`、`NOT_QUERIED`，注入当前 `epoch_id`，比较两次 anchor，
最后构造 consistency。未在计划中、缺少执行证明、查询计数超限、effect 超限或超出 descriptor
的观测一律不能提高可用性。

`OBSERVED` 必须覆盖该 item 的全部字段；`SEMANTIC_UNAVAILABLE` 只能在完整响应已经取得且
session 仍为 `healthy` 时使用，并携带核心注册的 reason code。`SKIPPED` 在 activation rule
判定不激活时使用 `inactive_by_anchor`；已激活但属于 optional 的 item 可以使用
`driver_skipped_optional`。required item 不能跳过，插件不能通过 `SKIPPED` 隐藏查询失败。

R2 descriptor 中每个受支持 read feature 都必须有同 scope 的 facet query contract；identity 必须由
唯一、required 的 instrument-scope item 提供。activation 引用的字段必须属于 `anchor_fields`。
展开通道、输入和通道集合后，item 最大查询数之和不能超过全局 `max_queries`。首版所有 facet
均为 `PURE_READ`，插件协议查询使用 `ReplayPolicy.NO_REPLAY`。

只能通过写入选择槽位才能读完的设备，不得将该流程伪装成 `source.snapshot_v2`。如果未来需要
这类快照，必须定义独立的 stateful snapshot operation，包含写前 baseline、有界写入、恢复和独立验证。
Protocol 返回值不得包含 SCPI、完整响应、真实资源串或异常原文。

## 谐波 facet

### 变长与稀疏分量

```python
class HarmonicCompleteness(str, Enum):
    COMPLETE = "complete"
    ACTIVE_ONLY = "active_only"
    SELECTED_ONLY = "selected_only"
    PARTIAL = "partial"


class ComponentAmplitudeKind(str, Enum):
    ABSOLUTE_VPP = "absolute_vpp"
    RELATIVE_LINEAR = "relative_linear"
    RELATIVE_DB = "relative_db"
```

约束：

- 分量按 `order` 唯一，可以稀疏；
- `maximum_supported_order` 不得由当前已读分量的最高阶次猜测；
- 必须显式记录谐波总开关和 completeness；
- 当前只返回选中槽位的设备不得伪造其他阶次为 0；
- `ABSOLUTE_VPP` 换算为对称峰值时使用 `peak_v = vpp_v / 2`；
- `RELATIVE_LINEAR` 和 `RELATIVE_DB` 必须引用明确、同一 snapshot 中的 carrier 幅度语义；
- dB 是幅度比还是功率比必须由 typed profile 冻结，不许默认猜测；
- 任一已启用分量的幅度为非 `VALUE` 时，复合输出预算必须拒绝放行。

## feature-specific patch 请求

### 操作语义

```python
class PatchAction(str, Enum):
    KEEP = "keep"
    SET = "set"


@dataclass(frozen=True, slots=True)
class PatchValue(Generic[T]):
    action: PatchAction
    value: T | None = None


class PatchMode(StrEnum):
    PATCH = "patch"
    REPLACE_ALL = "replace_all"
```

构造规则：

- `SET` 必须携带非 `None` 值；
- `KEEP` 必须使用 `value=None`；
- 关闭功能通过对 typed `enabled` 字段执行 `SET(False)`，不增加含义模糊的通用 `CLEAR`；
- 当前模式不适用的 `SET` 必须在仪器 I/O 前拒绝；
- 驱动不得静默忽略不支持的 patch 字段。

首版通用 patch 不提供 `RESET_DEFAULT` 或 `CLEAR`。厂商默认值可能随型号、固件或当前模式变化，
不能被核心解释成安全目标。未来若确需重置，必须由具体 facet 冻结目标值、预算影响和回读要求，
并使用专项 action 或 capability，不能扩展通用 `SET(None)` 的语义。

`PatchValue` 和 `PatchMode` 只是各 feature request 共用的值语义，不对应统一 capability。
`SourceBasicPatch`、`SourceHarmonicPatch`、`SourceModulationPatch` 等 request 必须是独立 dataclass，
只能包含所属 feature 的 typed 字段。核心不提供 `dict[str, PatchValue[object]]` 或动态字段名入口。

完整替换另使用 `PatchMode.REPLACE_ALL`。只有 snapshot 完整、恢复顺序已冻结且 descriptor
明确声明无损替换时才能使用。`SELECTED_ONLY`、`PARTIAL` 或安全字段未知的 snapshot
不能作为 `REPLACE_ALL` baseline。

## ARB storage mutation

ARB 上传不属于普通配置，也不能因为输出 OFF 就自动获准。R2 将存储、选择/播放配置和输出转换
拆成三个 operation context：

```python
class SourceStorageWriteMode(StrEnum):
    CREATE_ONLY = "create_only"
    REPLACE_IF_DIGEST_MATCHES = "replace_if_digest_matches"


@dataclass(frozen=True, slots=True)
class SourceArbitraryStorageRequest:
    channel: int
    slot_id: str
    write_mode: SourceStorageWriteMode
    payload_sha256: str
    payload_size_bytes: int
    expected_previous_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class SourceArbitraryStorageResult:
    channel: int
    slot_id: str
    payload_sha256: str
    payload_size_bytes: int
    write_completed: bool
    rollback_available: bool
    readback_verified: bool
```

- `slot_id` 必须显式给出；核心不自动挑选可覆盖槽位；
- `CREATE_ONLY` 要求槽位已证明为空，且 `expected_previous_sha256=None`；
- `REPLACE_IF_DIGEST_MATCHES` 要求写前取得权威旧摘要并与请求完全相同，不提供 force overwrite；
- payload 摘要使用 `sha256:<64 lowercase hex>`；大小必须为正且受 operation/profile 双重上限；
- 写入最多尝试一次，结果未知不重试；
- 成功必须通过设备回读或设备提供的权威摘要验证新内容；无法验证的 driver 不声明该 capability；
- `rollback_available=True` 只有在写前已取得可重放旧 payload、恢复步骤有界且经过 A0 故障注入时成立；
- 删除、重命名、隐式覆盖和批量清理不属于首版公共合同；
- storage mutation 只记录摘要、大小和槽位的脱敏标识，artifact 不保存 payload；
- storage mutation 请求不包含 `output_on`，也不签发能量转换准入决定。

上传完成后，选择 ARB 使用新的 `source.arbitrary_select_v2` operation。若随后需要 ON，必须重新
读取 fresh snapshot、计算预算，并以 `source.output_v2` 单独取得一次性准入决定。

## 复合输出预算

### 预算输入与输出

复合预算使用端口电压边界，不直接将所有波形当作正弦波。R3 冻结以下只读、纯计算形态；
它们不能单独授权写入，后续写修订只能消费同一计算结果，不能另造宽松模型：

```python
class BudgetProofStrength(str, Enum):
    HARD_CONSERVATIVE = "hard_conservative"
    STATISTICAL_ONLY = "statistical_only"
    MEASURED_ONLY = "measured_only"
    INCOMPLETE = "incomplete"


class TerminationKind(str, Enum):
    HIGH_IMPEDANCE = "high_impedance"
    RESISTIVE = "resistive"


class VoltageReferenceBasis(str, Enum):
    OPEN_CIRCUIT = "open_circuit"
    DELIVERED_INTO_DISPLAY_LOAD = "delivered_into_display_load"


@dataclass(frozen=True, slots=True)
class ResistanceBounds:
    minimum_ohm: float
    maximum_ohm: float


@dataclass(frozen=True, slots=True)
class TerminationSpec:
    kind: TerminationKind
    resistance_bounds: ResistanceBounds | None


class BudgetEvidenceSource(str, Enum):
    INSTRUMENT_READBACK = "instrument_readback"
    DEVICE_HARD_LIMIT = "device_hard_limit"
    EXPLICIT_TERMINATION = "explicit_termination"
    EXTERNAL_MEASUREMENT = "external_measurement"


@dataclass(frozen=True, slots=True)
class PortVoltageBounds:
    minimum_v_lower: float
    maximum_v_upper: float
    vpp_upper_v: float
    absolute_peak_upper_v: float
    rms_upper_v: float | None


@dataclass(frozen=True, slots=True)
class SafetyContributor:
    contributor_id: str
    feature: SourceFeature
    channels: tuple[int, ...]
    minimum_v: float
    maximum_v: float
    constraint_ids: tuple[str, ...]
    proof_strength: BudgetProofStrength
    evidence_sources: tuple[BudgetEvidenceSource, ...]


@dataclass(frozen=True, slots=True)
class CompositeOutputBudget:
    bounds: Observed[PortVoltageBounds]
    voltage_reference_basis: Observed[VoltageReferenceBasis]
    display_load: Observed[TerminationSpec]
    output_source_resistance: Observed[ResistanceBounds]
    actual_termination: Observed[TerminationSpec]
    shared_power: Observed[SourceSharedPowerBudget]
    proof_strength: BudgetProofStrength
    evidence_sources: tuple[BudgetEvidenceSource, ...]
    contributors: tuple[SafetyContributor, ...]
    blockers: tuple[SourceBudgetBlockerCode, ...]


@dataclass(frozen=True, slots=True)
class SourceSharedPowerBudget:
    participants: tuple[int, ...]
    observed_active_power_upper_w: float
    projected_power_upper_w: float
    effective_hard_limit_w: float
    constraint_ids: tuple[str, ...]
    evidence_sources: tuple[BudgetEvidenceSource, ...]


class SourceSafetyConstraintKind(StrEnum):
    VOLTAGE_REFERENCE = "voltage_reference"
    SOURCE_RESISTANCE = "source_resistance"
    FREQUENCY_DERATING = "frequency_derating"
    MODULATION_ENVELOPE = "modulation_envelope"
    ARBITRARY_OVERSHOOT = "arbitrary_overshoot"
    NOISE_PEAK = "noise_peak"
    SHARED_POWER = "shared_power"


class SourceBudgetBlockerCode(StrEnum):
    SNAPSHOT_NOT_CONSISTENT = "snapshot_not_consistent"
    DESCRIPTOR_MISMATCH = "descriptor_mismatch"
    TARGET_CHANNEL_UNKNOWN = "target_channel_unknown"
    BASIC_STATE_UNAVAILABLE = "basic_state_unavailable"
    AMPLITUDE_UNIT_UNSUPPORTED = "amplitude_unit_unsupported"
    WAVEFORM_UNSUPPORTED = "waveform_unsupported"
    DC_LEVEL_UNAVAILABLE = "dc_level_unavailable"
    FREQUENCY_MODE_UNSUPPORTED = "frequency_mode_unsupported"
    OUTPUT_POLARITY_UNAVAILABLE = "output_polarity_unavailable"
    VOLTAGE_REFERENCE_MISSING = "voltage_reference_missing"
    SOURCE_RESISTANCE_MISSING = "source_resistance_missing"
    DISPLAY_LOAD_UNAVAILABLE = "display_load_unavailable"
    DISPLAY_LOAD_UNSUPPORTED = "display_load_unsupported"
    ACTUAL_TERMINATION_MISSING = "actual_termination_missing"
    TERMINATION_EVIDENCE_INVALID = "termination_evidence_invalid"
    TERMINATION_NOT_RESISTIVE = "termination_not_resistive"
    HARMONIC_STATE_UNAVAILABLE = "harmonic_state_unavailable"
    HARMONIC_COMPLETENESS_INSUFFICIENT = "harmonic_completeness_insufficient"
    HARMONIC_AMPLITUDE_UNSUPPORTED = "harmonic_amplitude_unsupported"
    MODULATION_CONSTRAINT_MISSING = "modulation_constraint_missing"
    ARBITRARY_OVERSHOOT_MISSING = "arbitrary_overshoot_missing"
    NOISE_PEAK_MISSING = "noise_peak_missing"
    SWEEP_DERATING_MISSING = "sweep_derating_missing"
    ACTIVE_CHANNEL_UNKNOWN = "active_channel_unknown"
    COMBINE_STATE_UNAVAILABLE = "combine_state_unavailable"
    COMBINE_PATH_UNSUPPORTED = "combine_path_unsupported"
    SHARED_POWER_STATE_UNAVAILABLE = "shared_power_state_unavailable"
    SHARED_POWER_CONSTRAINT_MISSING = "shared_power_constraint_missing"
    SHARED_POWER_LIMIT_EXCEEDED = "shared_power_limit_exceeded"
    CONSTRAINT_NOT_HARD = "constraint_not_hard"
    VPP_LIMIT_EXCEEDED = "vpp_limit_exceeded"
    PORT_VOLTAGE_LIMIT_EXCEEDED = "port_voltage_limit_exceeded"


class SourceModulationKind(StrEnum):
    AM = "am"
    DSB_AM = "dsb_am"
    FM = "fm"
    PM = "pm"
    PWM = "pwm"
    ASK = "ask"
    FSK = "fsk"
    PSK = "psk"


@dataclass(frozen=True, slots=True)
class SourceVoltageReferenceConstraint:
    basis: VoltageReferenceBasis


@dataclass(frozen=True, slots=True)
class SourceResistanceConstraint:
    resistance_ohm: ResistanceBounds


@dataclass(frozen=True, slots=True)
class SourceFrequencyDeratingBand:
    frequency_hz: ClosedFloatInterval
    gain_upper: float


@dataclass(frozen=True, slots=True)
class SourceFrequencyDeratingConstraint:
    bands: tuple[SourceFrequencyDeratingBand, ...]


@dataclass(frozen=True, slots=True)
class SourceModulationEnvelopeConstraint:
    kind: SourceModulationKind
    gain_upper: float


@dataclass(frozen=True, slots=True)
class SourceArbitraryOvershootConstraint:
    gain_upper: float


@dataclass(frozen=True, slots=True)
class SourceNoisePeakConstraint:
    absolute_peak_upper_v: float


@dataclass(frozen=True, slots=True)
class SourceSharedPowerConstraint:
    participants: tuple[int, ...]
    maximum_power_w: float


SourceSafetyConstraintProfile: TypeAlias = (
    SourceVoltageReferenceConstraint
    | SourceResistanceConstraint
    | SourceFrequencyDeratingConstraint
    | SourceModulationEnvelopeConstraint
    | SourceArbitraryOvershootConstraint
    | SourceNoisePeakConstraint
    | SourceSharedPowerConstraint
)


@dataclass(frozen=True, slots=True)
class SourceSafetyConstraint:
    constraint_id: str
    kind: SourceSafetyConstraintKind
    applicability: SourceConstraintApplicability
    profile: SourceSafetyConstraintProfile
    proof_strength: BudgetProofStrength
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceSafetyProfile:
    constraints: tuple[SourceSafetyConstraint, ...]
```

`blockers` 是核心注册的稳定 reason code，不是可供插件自由拼接的错误文本。
`ResistanceBounds.minimum_ohm` 和 `maximum_ohm` 都必须有限且大于 0，且上界不得小于下界。
缺少有限上界不构成 `ResistanceBounds`；应缺少对应 constraint 并成为预算 blocker。`RESISTIVE`
必须携带有限上下界；`HIGH_IMPEDANCE` 仍由明确枚举表示，不能用缺失值暗示。没有可计算阻抗边界的
高阻枚举本身不能产生 `HARD_CONSERVATIVE`。`PortVoltageBounds.absolute_peak_upper_v` 按
`max(abs(minimum_v_lower), abs(maximum_v_upper))` 计算。

`SourceSafetyConstraintProfile` 是以下 typed profile 的封闭 union：电压参考、源电阻区间、
频率分段降额、调制最大包络因子、ARB 最大过冲因子、Noise 硬峰值边界和共享功率 envelope。
每种 profile 必须使用带单位字段，不能使用 `Mapping[str, object]`、表达式字符串或 callback。
无法映射到该 union 的设备事实只能作为证据记录，不能进入自动准入计算。

R3 的计算入口固定为核心内部纯函数 `evaluate_source_output_budget()`。它只接受已经构造的
`SourceSnapshotV2`、descriptor extension、`SourceEnergySafetyLimits` 和已绑定的端接证据，
不打开 session、不调用 driver、不发送 transport I/O，也不注册 capability。输入 snapshot 的
consistency、descriptor digest、目标通道、端接 context 的 correlation ID 和证据 binding 任一不符时，
函数返回 `INCOMPLETE` 预算与稳定 blocker；它不会修正、补齐或猜测任何输入。

R3 的首个可计算子集如下：

- `OPEN_CIRCUIT` 与 `DELIVERED_INTO_DISPLAY_LOAD` 使用有限纯电阻 Thevenin 区间换算；显示负载、
  源电阻和实际端接是三个独立输入；
- 输出极性必须为 `normal` 或 `inverted`。反相把参考区间 `[min, max]` 映射为 `[-max, -min]`；
  极性未知是 blocker；
- `gain_upper` 表示围绕 DC offset 的 AC 包络上界。调制、ARB 和 sweep gain 只放大 AC 分量，
  不得把 offset 一并相乘；当前首版只接受已读为 `internal` 的调制源；
- 固定频率和已完整读取的 sweep 可以计算；`list`、`unknown` 或缺失 frequency mode 固定失败关闭；
- Noise 只有匹配的硬峰值 constraint 才能参与；已启用 Harmonic 必须是 `COMPLETE` 且每个分量为
  `ABSOLUTE_VPP`；ARB、调制和 sweep 必须各自具备硬约束；
- Pulse 没有已冻结的最小／最大电平 facet，因此 R3 返回 `waveform_unsupported`，不得把 Vpp
  猜成 Pulse 的高低电平；DC 只在 Vpp 缺失或为零且 `offset_v` 可作为唯一电平时计算，非零 Vpp
  同时出现时返回 `dc_level_unavailable`；
- Combine 只接受已启用关系图中明确的 `INTERNAL_WAVEFORM` edge。其源波形经目标物理端口的
  实际端接换算；`OUTPUT_PORT` 或缺失关系图为 blocker，不得把一个通道的端接误用于另一个独立端口；
- 声明 `shared_power` 的 topology 必须同时提供 snapshot 的 active-power upper bound、runtime hard
  limit 和覆盖所有 active participant 的 descriptor hard constraint。核心比较观测值与预演的保守
  功率上界，取两类 hard limit 中更严格者；任一缺失、范围不一致或超限均拒绝。

`CompositeOutputBudget.can_authorize_energy` 仅在 `proof_strength=HARD_CONSERVATIVE`、`bounds` 为
`VALUE` 且 `blockers` 为空时为真。R3 不把这个布尔值接到任何输出、setter、trigger、恢复或
run plan 入口；M4/M5 必须在同一 operation context 内重新调用该计算器，才可能把它作为写前条件。

分段频率区间必须递增、无重叠；目标频域存在空洞时预算为 `INCOMPLETE`。gain、Vpp、峰值和功率
必须为有限非负值，gain 不得小于 1。`SourceNoisePeakConstraint` 只有在适用域内存在确定性硬边界
时才能使用 `HARD_CONSERVATIVE`；crest factor 或有限窗口测量只能生成
`STATISTICAL_ONLY`/`MEASURED_ONLY` 证据。共享功率 profile 未覆盖全部 active participant 时，
相关 topology 的输出 ON 准入固定拒绝。

### 单位与区间运算

所有计算字段使用 SI 单位，字段名最后使用 `_v`、`_hz`、`_ohm`、`_w`、`_deg` 或
`_percent` 等单位后缀；`vpp`、`rms` 和 `peak` 是位于单位后缀前的语义标记，例如
`vpp_upper_v`、`rms_upper_v` 和 `absolute_peak_upper_v`。无单位的 `amplitude` 不进入
V2 安全计算。

对称、已知 Vpp 的独立 AC 分量：

```text
require is_finite(vpp_i) and not isinstance(vpp_i, bool) and vpp_i >= 0
peak_i = vpp_i / 2
ac_peak_upper = sum(peak_i)
minimum_v_lower = dc_min_v - ac_peak_upper
maximum_v_upper = dc_max_v + ac_peak_upper
vpp_upper_v = maximum_v_upper - minimum_v_lower
```

一般 contributor 必须直接提供以伏特表示的最小和最大边界。Combine 和跨通道跟踪使用区间和
作为保守上界，不使用未证明的相位抵消降低预算。

特殊语义：

- AM 必须使用 typed profile 给出的最大包络因子；深度百分比不能在未冻结厂商语义时直接代入；
- Pulse、Square、DC 和 ARB 使用最小和最大电平，不使用正弦峰值假设；
- ARB 必须计入样本归一化、输出滤波或插值可能产生的 overshoot 边界；无法给出边界时为 `UNKNOWN`；
- Sweep 在完整频率范围内取最大边界并应用频率降额；
- Noise 只有在型号和固件范围内存在已审计的硬峰值上界时，才能进入 `HARD_CONSERVATIVE`；
- RMS 无法保守计算时可以为 `None`，但若配置了 RMS 上限，该缺失会成为 blocker；
- 多通道共享功率限制必须由 typed constraint 表示或返回可审查的计算结果，不允许使用自由文本声明。

当前 SDG2000X 插件证据中，DDS 内置目录已有 199/199 项 A4 冒烟，TARB 才是一个内置波形、
一个采样率的覆盖。本 RFC 不把两者误写成「ARB 只测了一个内置波形」，但两类证据都不能证明
用户上传波形、插值和重建滤波的确定性硬过冲上界，因此不会提高对应 ON 准入的 proof strength。

### 显式安全配置

R3 保持 `SafetyLimitsConfig` 的三个既有位置字段不变，并在
`max_power_current_limit_a` 之后追加两个带默认值的字段：

```python
@dataclass(frozen=True)
class SafetyLimitsConfig:
    max_source_vpp: float | None = None
    max_power_voltage_v: float | None = None
    max_power_current_limit_a: float | None = None
    min_source_port_voltage_v: float | None = None
    max_source_port_voltage_v: float | None = None
```

旧代码使用三个位置参数构造 `SafetyLimitsConfig` 时，参数含义不能因 Source V2 改变。
`WaveBenchConfig.with_source_resource()`、其它资源覆盖和 waveform override 必须原样保留这两个
新字段，不能在复制配置时丢失或补默认值。

配置规则：

- 绝对电压两项均缺失时，旧配置正常加载，V1 行为不变；
- 只配置其中一项时，配置加载失败；
- 两项均配置时，必须是有限的 `int` 或 `float`，拒绝 `bool`、NaN 和 Infinity，并满足
  `min_source_port_voltage_v < max_source_port_voltage_v`；区间不要求包含 0；
- 两个端点是有符号区间，不是两个「正数上限」，不得复用只接受正数的配置 parser，也不得把
  负端点改写成绝对值；
- `max_source_vpp` 继续作为独立安全轴，不能由绝对电压区间替代；
- Source V2 能量增加操作要求 `max_source_vpp` 与绝对电压两项全部显式配置；
- 不从 `max_source_vpp` 推导对称绝对区间，也不从绝对区间反推 Vpp 上限；
- 设备硬限制、实验台配置和 run 限制取交集；设备与 run 只能继续收紧实验台配置；
- 缺少必需安全轴时，在 driver factory 和 transport 打开前抛出
  `SourceSafetyLimitsRequiredError`，其 `wavebench.error.v1` code 固定为
  `source_safety_limits_required`，`details.missing_fields` 使用排序稳定的配置键；
- 两项只配置一项属于加载期 `config_error`，不是运行时缺失错误。

既有 `max_source_vpp` 的安全修复可以拒绝 `bool` 和非有限数，但不能改变正常有限正数的 V1
含义。绝对电压区间只约束 Source V2 能量操作；它不会被暗中应用到旧 `source.set_vpp` run step、
V1 CLI 或未 opt in 的 V1 driver。

缺少显式安全轴仍允许 `source.snapshot_v2`、正常 OFF、disable，以及已经证明不会发出信号的
输出 OFF 配置或独立 storage mutation。ON、fire、恢复 ON、可能发出信号的 trigger、live mutation，
以及能量影响为 unknown 的 operation 必须零仪器 I/O 拒绝。

示例配置中的「缺失表示不限」只能继续描述 V1。Source V2 文案必须明确：缺失表示没有能量转换
授权，而不是无限制。

### 负载与端接

`display_load` 来自仪器用于计算和显示幅度的状态；`output_source_resistance` 表示输出端的
等效源电阻边界；`actual_termination` 是独立的外部证据。三者不得互相自动复制。
`TerminationSpec` 用明确枚举表示高阻或电阻端接，不能使用 `None` 暗示高阻。

实际端接使用独立公共证据类型：

```python
class TerminationEvidenceSource(StrEnum):
    CONFIG = "config"
    RUN_INTENT = "run_intent"
    MANUAL_CONFIRMATION = "manual_confirmation"
    EXTERNAL_MEASUREMENT = "external_measurement"


class TerminationEvidenceLifetime(StrEnum):
    OPERATION = "operation"
    RUN = "run"
    CONFIG_DIGEST = "config_digest"


@dataclass(frozen=True, slots=True)
class SourceTerminationEvidence:
    target: SourceScopeRef
    termination: TerminationSpec
    source: TerminationEvidenceSource
    lifetime: TerminationEvidenceLifetime
    resource_fingerprint: str
    binding_digest: str
    observed_at_utc: str
    expires_at_utc: str | None
    evidence_ref: str
```

`MANUAL_CONFIRMATION` 只能使用 `OPERATION`；`RUN_INTENT` 只能使用 `RUN`；静态 fixture 配置使用
`CONFIG_DIGEST` 并同时绑定脱敏资源指纹。任何 evidence 在 resource、config、intent、correlation、
target 或有效期不匹配时都不能参与预算。仪器显示负载和插件 descriptor 不得构造
`SourceTerminationEvidence`。

对于首版支持的纯电阻模型，幅度参考和端接换算固定为 Thevenin 关系。若配置幅度 `v_ref`
表示开路电压，则 `v_port = v_ref * r_actual / (r_source + r_actual)`；若它表示显示负载
`r_display` 上的电压，则先计算
`v_open = v_ref * (r_source + r_display) / r_display`，再按实际端接计算端口电压。
存在阻抗区间时，核心用区间运算选择使绝对端口电压最大的组合，不得使用标称值代替容差上界。
幅度参考未知、源电阻无有限上界、显示参考不可计算，或实际负载超出首版纯电阻模型时，
换算结果为 blocker。确切 enum、区间序列化和频率相关模型必须在对应写 capability 的
`Accepted` 修订前冻结。

实际端接证据至少绑定：

- 资源指纹和通道；
- 端接阻抗或高阻语义；
- 证据来源和有效期；
- 本次 operation 或 run 的 correlation ID；
- 是否允许在报告中公开。

具体配置键和 CLI 人工确认语法留到 M3 入口设计阶段，但不能改变上述公共类型和有效期规则。
任何依赖端接换算的 Source V2 输出 ON 准入，都不得因为仪器显示为 `HiZ` 而猜测外部也是高阻。

### 预算准入决策

R3 将「证据来源」与「证明强度」分开。仪器数据手册、状态回读、显式实验配置和外部测量可以
作为 `evidence_sources`，但来源名称本身不决定能否通过输出 ON 准入。

本节的「输出 ON 准入」包括任何由 WaveBench 软件发起的 OFF → ON，以及会让已配置波形
实际开始发出的 arm、fire、trigger 或恢复动作，不区分有人值守和无人值守。

| proof strength | 是否充分支持输出 ON 准入 | 说明 |
| --- | --- | --- |
| `HARD_CONSERVATIVE` | 是 | 使用全部已启用 contributor、硬边界和负载转换得到保守上界 |
| `STATISTICAL_ONLY` | 否 | crest factor、概率区间或有限时间窗口不能证明瞬时硬上界 |
| `MEASURED_ONLY` | 否 | 历史或单次测量不证明当前状态的未来上界 |
| `INCOMPLETE` | 否 | 必需状态、约束或端接证据缺失，失败关闭 |

设备声明的硬限制可以成为 `HARD_CONSERVATIVE` 计算输入，但插件自报的「安全」结论不能直接
成为证明强度。后续若需允许统计预算，必须作为单独 RFC 或本 RFC 的明确修订，定义配置授权、
证据新鲜度、artifact 风险标记和无人执行边界。

输出 ON 准入至少需要：

- 预算无 blocker；
- `proof_strength=HARD_CONSERVATIVE`；
- `configured_min_v` 与 `configured_max_v` 均已显式配置且顺序有效；
- `vpp_upper_v <= max_source_vpp`；
- `minimum_v_lower >= configured_min_v` 且 `maximum_v_upper <= configured_max_v`；
- 配置 RMS 上限时，`rms_upper_v` 存在且不超限；
- 负载参考与实际端接的换算已证明；
- 频率降额和多通道共享功率限制通过；
- 所有已启用 contributor 均已纳入。

仅配置现有 `max_source_vpp` 只能维持 V1 行为，不能单独放行 Source V2 能量启动。
topology 或 profile 只要声明共享功率关系，就必须提供完整 typed power envelope；该合同冻结前，
相关 topology 的输出 ON 准入固定拒绝。

### 统一输出准入门

准入决定由核心产生，插件不能通过 `safe=True` 或等价自报绕过。决定至少绑定：

- operation context、session epoch 和一次性 snapshot ID；
- 受影响通道、跨通道关系与预期写后状态；
- 当前及目标预算、实际端接证据和配置限制；
- 允许的下一次单一能量转换 action；
- 绝对 deadline。

准入决定只能在所有配置写入完成后签发。签发后的任何配置写入、anchor 变化、连接代次变化或
context 结束都会使它失效；获准的 ON/fire/trigger action 会一次性消费它。首版禁止用一个决定
同时授权 ARB 上传和 ON，也禁止输出 ON 时执行多字段 live patch。

R2 规定未来 Source V2 首个可写修订中，高级配置只允许在所有相关输出 OFF 时执行，不自动执行
「关闭 → 配置 → 重新开启」。未来若允许 live mutation，必须通过本 RFC 修订并复用同一
写后预算门。该规则不追溯改变未 opt in 的 V1 驱动行为。

OFF 不需要复合预算，但仍需要正常 OFF 权限或核心签发的 recovery 授权。`poisoned` 连接不得
为了 OFF 再发送协议 I/O。

## 核心协调写事务

### 正常路径

1. 在 driver factory 前验证 request 类型、显式安全配置、capability、descriptor 静态 profile、
   `SourceOperationContract` 与 access policy。
2. 取得独占资源租约并调用现有 factory；factory 可以打开单一 session。实例返回后立即验证
   Protocol 方法，失败时关闭 transport、释放租约，且不调用 Source operation method。
3. 只有实例验证通过后，才构造只能收窄 descriptor 的 runtime profile 并进入 operation preflight。
4. 核心根据 request、topology、runtime profile 和跨通道关系生成 `SourceAffectedClosure`；
   无法确定依赖或 emergency OFF 范围时拒绝。
5. 在 `PREFLIGHT` 中读取 fresh 一致 snapshot，证明 required-off 输出，创建 core-owned baseline，
   并冻结 target state。
6. 对 target state 离线预演。`MAY_INCREASE` 或 `EMIT` 计算完整预算；storage mutation 执行槽位、
   摘要和覆盖策略检查；`DECREASE_ONLY` 不要求预算。
7. 立即复读安全 anchor；与 baseline 不一致时在 mutation 前拒绝。
8. 在第一次可能发送的写入前，由 `SessionTransactionCoordinator.invalidate_verified_fields()`
   使 closure 中的旧 session 证据失效。
9. 关闭 `PREFLIGHT`，在唯一 `MAIN` phase 中执行 request。每个目标字段和预先声明的辅助转换
   最多写入一次；结果未知不重试。
10. 关闭 `MAIN`，在 `POSTCONDITION` 中独立读取目标字段、隐式变化字段、未修改闭包和安全前置。
11. 只有 postcondition 全部匹配，且核心验证器提交完整 fresh evidence 后，才返回成功。

目标值与仪器当前值相同时，可以零写入返回。该返回仍必须有 fresh snapshot 和所需安全证据，
不得使用旧缓存推断成功。

「每个目标字段最多写入一次」不把辅助转换藏起来。例如从 Sweep 切换到固定频率属于独立的
`frequency_mode` mutation，必须出现在 closure、执行记录和 postcondition 中；它不能作为重复写入
同一字段的理由。仪器支持分号批处理也不能被假定具有原子性，除非公共 profile 明确证明
all-or-nothing 语义并完成对应故障注入。

postcondition readback 只证明本次 operation 的结果，不会自动恢复 session 的 `verified_fields`。
只有核心验证器在相应 verification authorization 中逐字段记录 fresh evidence，并完成验证闭包后，
字段才能重新加入当前 epoch 的 `verified_fields`。

### 失败与恢复

- 仪器 I/O 前的预检失败：零写入，session 保持当前 health；
- 写入确认未发送：零仪器变化，不执行多余 OFF；
- 写后回读不符，且通信仍为 `healthy`：进入核心授权的有界 recovery phase；
- 结果未知但同步仍可证：session 进入 `uncertain`，只允许核心授权的 recovery/verification；
- 同步不可证或丢失：session 进入 `poisoned`，旧连接不再发送 OFF 或任何查询；
- recovery 写入、OFF 回读或验证失败：保留更保守的 session health，不报告已恢复；
- 只有新 session 或原 `uncertain` session 在授权验证中覆盖完整字段闭包后，才能继续 mutation。

`MAIN` 关闭前不能签发 recovery authorization。只有 operation 开始前已经计算出完整
`emergency_off_outputs`，且 session 不是 `poisoned`，才能进入 `FAILURE_SAFE_STATE`。
该 phase 的 OFF 也最多发送一次；OFF 结果未知不得重试。`FAILURE_RESTORE` 不能恢复输出 ON、
arm/fire 状态或其它会重新供能的字段。

应急 OFF 范围由受影响字段闭包和 descriptor 的跨通道依赖决定。Combine、Tracking、Coupling
或共享功率可能要求关闭多个通道；不得默认只关闭目标通道。

启用 Combine 等会隐式改写两通道 load 的设备，closure 必须包含两个通道的 `DISPLAY_LOAD`、
相关波形预算和输出状态。未来首个写修订只在所有相关输出已证明 OFF 时允许这类配置；任一 load 或关系
回读未知时，operation 不进入 `MAIN`。

OFF 回读成功只证明对应输出字段，不会自动证明全部 Source 状态可信，也不会独立将
`uncertain` 恢复为 `healthy`。

### baseline 与 nonce

可恢复的 Source V2 写入必须使用 core-owned baseline handle，至少包含：

- `context_id`；
- `session_epoch`；
- core-generated opaque nonce；
- snapshot 摘要；
- 受影响字段闭包；
- restore order 和最大步骤；
- 明确不恢复的字段。

nonce 只能在同一 context 与 epoch 中使用一次。artifact 只记录 nonce 摘要，不记录完整值。

如果某个 facet 不可完整读取，或恢复要求未冻结的写选择动作，该 facet 必须记录为不可恢复。
这类操作只能在明确策略下执行，失败后默认保持受影响输出 OFF，不伪装恢复完整前状态。

## 错误队列与查询副作用

错误队列是可选证据，不是 Source V2 的必填 capability。

- 没有已认证错误队列的驱动可以依靠单写、独立回读、核心授权的 OFF 和 session 锁存；
- 支持错误队列的驱动必须通过独立 capability 与类型化 error policy 声明；
- 读后清除或消费型查询使用 `stateful_read` effect 和 `ReplayPolicy.NO_REPLAY`；
- 一次 operation 只能由 core 或 legacy driver 一方负责错误检查，不能重复消费；
- 错误队列失败不会被映射成假空列表。

R2 不增加新的 access mode。query-only 但会消费错误状态的 operation 使用通用
`effect="stateful_read"`、独占 lease 和 `ReplayPolicy.NO_REPLAY`，并在 Source 语义计划中标记
`SourceQueryEffect.STATEFUL_CONSUMING_READ`。它可以在 `read_only` access 下显式执行，
但不得并入首版 `source.snapshot_v2`，也不得标记为无副作用 `observe`。

## artifact 合同

### 运行时 operation artifact

R2 冻结 `wavebench.source.operation.v1` artifact。Source V2 是领域模型版本，artifact 使用
`v1` 表示该 artifact schema 的第一个版本，两者不冲突。

`source.snapshot_v2` 精确包含：

| 字段 | 语义 |
| --- | --- |
| `schema` | 固定为 `wavebench.source.operation.v1` |
| `operation` | 固定为 `source.snapshot_v2` |
| `context_id` / `correlation_id` | 单次 operation 与上层运行的关联标识 |
| `session_epoch` | 脱敏的连接代次标识 |
| `capability_decision` | capability、Source contract version 和 descriptor digest |
| `snapshot` | 完整 `wavebench.source.snapshot.v2` document |
| `query` | 固定 `pure_read` effect、plan digest 和总 query count |
| `session_health` | operation 前后 health |
| `final_state` | consistency 和 operation 结束时 health |
| `evidence_refs` | 脱敏证据 ID、摘要和验收等级，不含原始 payload |

snapshot artifact 不包含空的 `budget`、`mutation`、`postcondition`、`recovery` 或 `verification`
占位字段。后续写 operation 采用同一 schema 时，必须通过 Accepted 写修订增加这些 typed 字段，
不能让 snapshot-only 实现提前猜测其 shape。预检失败使用 `wavebench.error.v1`；纯离线拒绝不得
为了生成 artifact 打开 transport。

### 发布 conformance manifest

运行时 artifact 与插件发布证据使用不同 schema。R2 冻结：

```text
schema = wavebench.source.conformance.v1
conformance_scheme = wavebench.source.a0-a5.v1
```

manifest 至少包含：

| 字段 | 语义 |
| --- | --- |
| `manifest_id` | 稳定安全 token |
| `conformance_scheme` | 采用的等级定义和修订，不能只写裸 `A3` |
| `claimed_level` | `A0`–`A5` |
| `capability` / `feature` / `direction` | 本证据实际覆盖的公共合同范围 |
| `model` / `firmware_id` / `option_ids` / `channels` | 不允许外推的设备适用域 |
| `core_version` / `plugin_version` / `wheel_sha256` | 软件与发布物身份 |
| `descriptor_digest` / `source_contract_version` | 公共 descriptor 与 Source 合同身份 |
| `fixture` | 脱敏端口、端接和接线摘要 |
| `safety_limits` / `budget` | 当次限制、contributor、blocker 和准入结果 |
| `results` | 请求、回读、外测、容差和发送计数摘要 |
| `session_health` / `final_state` | 前后 health、最终 OFF 与恢复范围 |
| `coverage` / `limitations` | 已证明和明确未证明的内容 |
| `evidence_digest` | canonical manifest 的 SHA-256 |

manifest 在 wheel 中使用本 distribution 自己的
`.dist-info/wavebench-source-conformance/<manifest_id>.json` 路径。descriptor 的
`evidence_refs` 使用 `dist-info:wavebench-source-conformance/<manifest_id>.json`，不得引用开发机
绝对路径。核心只读取当前 distribution 的资源，拒绝路径穿越、跨 distribution 引用和摘要不匹配。

历史证据不能执行全局字母替换。若历史文档使用另一套等级定义，新 manifest 必须记录原始
scheme、原始等级、按 `wavebench.source.a0-a5.v1` 重新评定的等级和理由；没有可证明映射时，
只保留为未分级 evidence ref。一次 operation artifact 只引用 manifest ID 和摘要，不复制整份证据。

## V1 兼容与迁移

### 版本门与双合同组合

| 组合 | 预期行为 |
| --- | --- |
| 旧核心 + 旧插件 | 保持对应版本的 V1 行为 |
| 新核心 + 旧插件 | `source_extensions=None`，保持 V1 路径，不推导 V2 写能力 |
| 旧核心 + 新插件 | 受管安装由 wheel `Requires-Dist` 在 entry point import 前拒绝；绕过 package inspection 的直接 `pip --no-deps` 或手工安装不承诺零导入，且不属于支持组合 |
| 新核心 + 新插件 | 只对明确声明并通过验证的 Source V2 capability 使用新合同 |
| 新核心 + 同时声明 V1/V2 的新插件 | 新 operation 只使用 V2；同义或副作用重叠的旧写入口映射／拒绝，不相交的旧 operation 保持 V1；单次事务不混用两套安全视图 |

R2 决定保持 `wavebench.instrument.v2`。`source_extensions` 是带默认值的末尾扩展，新 Protocol
不改变现有 `SourceDriver`，新 capability 通过最低核心版本门显式 opt in。只有删除 Source V1、
改变既有 capability 语义或签名、改变既有返回 model、破坏 descriptor append-only，或者新核心
无法继续装载并执行兼容范围内的旧 V2 插件时，才升级为 `wavebench.instrument.v3`。

### 兼容性不变面

Source V2 是并列合同，不是对现有 Python model、CLI JSON 或 run artifact 的原地扩容。
以下不变面在实现前冻结：

| 层级 | 兼容合同 |
| --- | --- |
| descriptor | `source_extensions` 位于 `scope_extensions` 之后且默认 `None`；旧位置参数构造、字段默认值、比较和 `replace()` 保持原义 |
| 公共 Python API | 不向 `SourceDriver` 增加必需方法；不向 `SourceStatus` 或 `RestorableSourceState` 增加字段；V2 使用独立 Protocol 和 model |
| capability | 不修改既有 `source.*` ID、required method、参数、返回类型或副作用；V2 方法存在本身不产生 capability |
| 配置 | 旧 TOML 缺少绝对电压字段时按原值加载；新增字段仅显式授权 V2，不能反向限制或放宽 V1 |
| CLI 与 TUI | `snapshot-v2` 是附加子命令；既有子命令参数、V1 status JSON 和 TUI adapter 行为不变 |
| run plan | 首阶段不增加 V2 写 step；既有 step kind、必填字段、schema 和 V1 安全检查保持不变 |
| 恢复 artifact | 保留 `restore.source_state_scope="basic"`、`snapshot`、`snapshots` 及其 V1 字段；V2 operation artifact 使用独立 schema，不替换旧键 |
| 报告与包读取 | 旧 run package 继续可读；消费者必须忽略未知的附加 V2 artifact 引用，不能把 V2 缺失视为旧包损坏 |

V1 `SourceStatus.as_dict()` 和 `RestorableSourceState.as_dict()` 的键集合与值语义保持不变。
V2 snapshot 不得先展平到 V1 model 再用于预算或恢复。若未来需要把 V2 operation 引用嵌入
`run.json`，只能增加带独立 `schema` 的可选命名空间，并先证明旧 reader 对未知键宽容；本 RFC
首阶段不修改现有 `restore` 对象。

Source V1 的 `0.8.23` capability → required method 冻结基线为：

```text
source.idn                       -> idn
source.errors                    -> errors, assert_no_errors
source.status                    -> get_status
source.channel_profile           -> get_channel_profile
source.coupling_profile          -> get_coupling_profile
source.coupling_configure        -> configure_coupling
source.harmonic_profile          -> get_harmonic_profile
source.harmonic_configure        -> configure_harmonics
source.modulation_am_profile     -> get_am_modulation_profile
source.modulation_am_configure   -> configure_am_modulation
source.modulation_fm_profile     -> get_fm_modulation_profile
source.modulation_fm_configure   -> configure_fm_modulation
source.modulation_pm_profile     -> get_pm_modulation_profile
source.modulation_pm_configure   -> configure_pm_modulation
source.modulation_pwm_profile    -> get_pwm_modulation_profile
source.modulation_pwm_configure  -> configure_pwm_modulation
source.pulse_profile             -> get_pulse_profile
source.pulse_configure           -> configure_pulse
source.burst_profile             -> get_burst_profile
source.burst_configure           -> configure_burst
source.burst_trigger             -> trigger_burst
source.sweep_profile             -> get_sweep_profile
source.sweep_configure           -> configure_sweep
source.sweep_trigger             -> trigger_sweep
source.counter_profile           -> get_counter_profile
source.set_frequency             -> set_frequency
source.set_function              -> set_function
source.set_amplitude_vpp         -> set_amplitude_vpp
source.set_square_duty_cycle     -> set_square_duty_cycle
source.output                    -> set_output
source.arbitrary_probe           -> probe_arbitrary_queries
source.arbitrary_upload          -> upload_dg4000_dac14_block
```

该清单和 tuple 顺序使用静态契约测试锁定。注册 `source.snapshot_v2` 或后续 V2 capability 不能
修改这些映射，也不能改变内建 descriptor 对既有 V1 capability 的声明。

双合同驱动首次声明 V2 写 capability 时，核心必须审计当前全部 V1 写表面：
`set_frequency`、`set_function`、`set_amplitude_vpp`、`set_square_duty_cycle`、`set_output`、
`configure_coupling`、`configure_harmonics`、`configure_am_modulation`、
`configure_fm_modulation`、`configure_pm_modulation`、`configure_pwm_modulation`、
`configure_pulse`、`configure_burst`、`trigger_burst`、`configure_sweep`、`trigger_sweep`、
`upload_arbitrary_waveform` 和 `restore_restorable_state`，以及 run 与 TUI 间接入口。审计结果必须
逐项标记为「同义」「字段／发信号副作用重叠」或「不相交」。前两类必须有无损 V2 映射或稳定的
零写入拒绝；不相交项可以保持 V1，不能仅凭「已扫描」省略分类证据。V1-only driver 的现有恢复
顺序保持不变；声明 `source.output_v2` 的双合同驱动必须把「恢复 ON」转为独立 V2 授权，不得沿用
旧恢复路径直接重新 ON。

### R5 V1 路由清单与 artifact 边界

M4.5 将上述 18 条 V1 Service 写路由冻结成核心内部清单，并为每条记录其 `OperationSpec`、CLI、
run plan、TUI、离散扫频、恢复或安全门等间接入口，以及是否可能在输出已开启时改变信号、开始或
重新开始输出、修改仪器存储。该清单是未来 V2 写 capability 的审计输入，不是 V1→V2 自动映射：
在本修订中所有 V1 路径仍按原合同执行，任何具体 V2 写 capability 仍必须逐项标记为「同义」、
「字段／发信号副作用重叠」或「不相交」。

`run.json` 预留可选根键 `source_operations`，但只有某次运行实际产生一个或多个带独立
`wavebench.source.operation.v1` schema 的 Source V2 operation artifact 时才写入。空列表、缺失值
和所有当前 V1 run 都不得写该键；因此既有 `restore` 对象、step artifact 内的 `source_status` 和
默认 V1 run JSON 的字节表示保持不变。run package 与报告 reader 必须继续忽略未知根键，不能把
该可选命名空间的缺失当作旧包损坏。

### Source V1 生命周期

Source V1 冻结基线是 WaveBench `0.8.23` 已注册的全部既有 `source.*` capability，不只包括
status、output 和基础 setter，也包括已经发布的 profile、configure、trigger 与 ARB capability。

1. **共存期**：Source V2 首次发布后，V1 保持完整支持且默认不产生弃用诊断。旧插件继续走 V1；
   只有显式声明 V2 capability 的插件才进入新路径。
2. **冻结与弃用期**：V1 不再增加高级模型或厂商特例。允许保持签名和返回类型的安全修复、
   错误类型修复及失败关闭修复。只有 V1 operation 已有无损 V2 映射或零 I/O 拒绝规则、全部公共
   入口已迁移、双合同旁路已消除、两类协议完成试迁移、V1-only 兼容测试持续通过，并已发布
   迁移指南后，才能标记 deprecated。诊断优先出现在文档、`plugin doctor` 和
   `capability explain`，不在每次成功执行时重复打印。
3. **删除期**：Source V1 只能随 `wavebench.instrument.v3` 删除。V3 不能同时成为首次弃用通知
   和删除版本；弃用状态必须至少经历一个完整的稳定核心发布周期。

插件自己的 `wavebench_max_version` 只约束具体 distribution 版本，不是 Source V1 的自动弃用日期，
也不覆盖核心对 executable API v2 的兼容承诺。

### adapter 边界

- V1 → V2 adapter 只能产生可无损映射的 basic/output 只读视图；
- V1 `None` 默认映射为 `UNKNOWN`，不根据函数名猜测 `NOT_APPLICABLE`；
- V1 adapter 不声明 `source.output_v2` 或其他 V2 写 capability；
- V2 → V1 可将非 `VALUE` 展平为 `None`，但只用于兼容返回值和显示；
- 有损展平不得用于预算、恢复、写前比较或 capability 声明；
- 旧高级 profile 只在无损对应时保持，不继续增加厂商伪默认。

同时声明 V1 与 V2 的驱动必须保持两套公共返回类型可区分。单次写事务不得混合 V1 状态视图
与 V2 安全决策。

双合同驱动声明某项 V2 写 capability 后，同义或副作用闭包重叠的 V1 写入口必须在 Service 边界
映射到对应 V2 operation，无法无损映射时在 I/O 前拒绝；经审计确认字段闭包和发信号路径均不相交
的 V1 operation 可以继续保持原行为。未声明 V2 写 capability 的 V1-only 驱动保持原行为；
Source V2 首版禁止 live mutation 不追溯改变该路径。

### 独立 P0 缺陷修复

以下缺陷修复不需要完整 Source V2 才能设计，但 `Accepted R2` 的 M1–M2 授权不包含该修复：

1. V1 `source.output ON` 遇到 `amplitude=None`、非有限数或非 Vpp 幅度时，返回稳定 `ConfigError`；
2. `bool`、字符串、NaN、Infinity 和负值不能通过 V1 安全检查；
3. 拒绝必须发生在 driver 写入前，测试断言写入计数为 0；
4. `source.output OFF` 不因幅度缺失被拒绝，但仍受 access policy 和 session health 规则约束；
5. Service、run、CLI 和 TUI 的错误包装保持一致；
6. 该修复不声明 Noise/DC 已获得可安全开启的 V2 预算。

## 验收证据分层

本节等级 scheme 固定为 `wavebench.source.a0-a5.v1`。验收等级绑定「具体 capability × 型号 ×
固件 × 通道 × operation 方向」，不作为整个产品系列的标签，也不接受没有 scheme 的裸等级。

| 等级 | 证据 | 可以证明 | 不能证明 |
| --- | --- | --- | --- |
| A0 | 离线 fixture、model 校验与故障注入 | 命令格式、解析、发送次数、预算分支和失败关闭 | 真实仪器响应、波形或接线 |
| A1 | 实机只读 | 查询合法性、响应形态、型号和固件 | 写入、输出和触发 |
| A2 | 受控 ON/OFF | 输出转换、回读、OFF 恢复和最终状态 | 幅度、频率或波形精度 |
| A3 | 示波器通道环回 | 基础频率、Vpp、偏置、函数和占空比 | 高级波形语义或外部触发 |
| A4 | 高级波形测量 | 谐波频谱、调制包络、Sweep 路径、Burst 周期数等 | 未接线的触发、Gate、Sync 或通道间时序 |
| A5 | 真实触发或同步接线 | 外部触发、Gate、Sync 和通道间时序 | 未实际接入的其他端口 |

建议最低关系：

- 任一 capability 必须有 A0；
- profile read 至少有 A1；
- enable/disable 至少有 A2；
- 基础波形 write 至少有 A3；
- 高级 waveform write 至少有对应功能的 A4；
- external trigger、Gate 或 Sync 至少有对应功能的 A5。

每份 A1–A5 证据至少记录：

- core、插件、distribution、版本和 wheel 摘要；
- 仪器族、型号、固件、选件和通道；
- descriptor 与 Source 合同修订；
- capability、feature、direction 和 mode；
- 脱敏的端口映射、实际端接和接线说明；
- 预算输入、配置上限、决策和 blocker；
- 请求值、回读值、外测值、容差和测量方法；
- 实际写入数、已完成数和结果未知数；
- session health 前后状态；
- 最终输出状态、恢复状态和未恢复字段；
- 证据时间与不在声明范围内的项目。

验收等级是发布和 capability 评审证据，不是运行时授权。核心可以验证证据引用格式，
但不会因 descriptor 声明 `A4` 而跳过 fresh snapshot、预算或 session 门。

历史文档若使用相同字母但定义不同，必须逐份重新评定；不得用「旧 A3 → 新 A1」之类全局替换。

## 离线验收门

### model 与 descriptor

- `Observed` 全部合法与非法组合有单元测试；
- Vpp、RMS、频率、电阻和功率类型拒绝 `bool`、负值与非有限数，不在预算中静默正规化非法输入；
- feature、direction、channel、mode 和 typed profile 相互校验；
- `SourceFieldRef` 的 field/scope 组合、排序、去重和 canonical digest 有属性测试；
- runtime profile 对 descriptor 只能删减，任何新增 feature、direction、channel 或 constraint 都拒绝；
- descriptor 缺失 profile 或版本门时在 factory 前拒绝；缺 Protocol 方法时可由现有 factory 建立
  transport，但必须在任何 Source operation method 前拒绝，并关闭连接、释放租约；
- `source_extensions` 紧跟 `scope_extensions` 并保持 descriptor 最末字段；旧 V2 全位置参数 fixture、
  关键字构造、比较和 `dataclasses.replace()` 测试通过；
- `source_extensions.__all__` 与顶层重导出逐项相等且对象 identity 相同，内部 coordinator、token、
  nonce、writer 和 raw transport 不可导入；
- 多声明方法不产生隐式 capability；
- 只实现 basic V1 的第三方 Source 不需要填充高级 V2 字段。

### snapshot 与查询计划

- 非激活 Harmonic 不发送不合法查询；
- `anchor -> facet -> anchor` 中的状态漂移返回 `DRIFTED`，不能作为写 baseline；
- core-issued 语义计划与插件协议执行记录逐项匹配；插件自报 epoch、availability 或 consistency
  不能提高 snapshot 可信度；
- plan ID、item ID、phase、effect、query count 和 deadline 任一不符时失败关闭；
- 消费型查询最多发送一次，并在 artifact 中标明 effect；
- selected-only 谐波不填充未读阶次；
- query timeout、短响应、解析失败和 session health 转移的 `Observed` 语义明确。

### 预算与入口覆盖

- 基础正弦、DC、Pulse、谐波、AM、Noise、ARB、Sweep、Combine 和负载转换都有正、负 fixture；
- 任一已启用 contributor 为非 `VALUE` 时拒绝 ON，写入计数为 0；
- 50 Ω 显示参考与实际高阻端接的电压变化可表示，且不会因基础 Vpp 未超限而误放行；
- `source.output`、`arb_load output_on`、ON 状态 setter/patch、arm/fire 和恢复 ON
  的统一决策有契约测试；
- 缺少绝对端口上下限、幅度参考基准、阻抗区间或所需共享功率 envelope 时拒绝 ON；
- `max_source_vpp` 或绝对电压任一安全轴缺失时，在 driver factory 前返回
  `source_safety_limits_required` 和稳定 `missing_fields`；
- 旧 TOML、只有一个绝对电压端点、有限负端点、不包含 0 的合法区间、`bool`、字符串、NaN、
  Infinity 和 `min >= max` 均有配置 fixture；所有 `WaveBenchConfig.with_*` 复制路径保留新字段；
- OFF 不因预算缺失被拒绝，但 `uncertain`/`poisoned` 仍按 session health 合同执行。

### ARB storage

- create-only 对非空槽位零写拒绝；
- compare-and-replace 的旧摘要不匹配时零写拒绝；
- payload 摘要、大小、槽位和设备回读不一致时不报告成功；
- 上传结果未知时发送次数不超过 1，不自动改为新槽位重试；
- storage、selection 和 ON 的 operation/context/nonce 各自独立；
- 不支持权威摘要或内容回读的 fake driver 不能声明 `source.arbitrary_storage_v2`。

### 事务与恢复

- 每个目标字段写入次数不超过 1；
- 每个 phase 最多进入一次，phase 顺序、purpose 和允许 I/O 不匹配时拒绝；
- 预检、写入、回读、恢复和验证每个 phase 都有 deadline 与步骤上限，normal phase 不能消耗
  cleanup reserve；
- 第一次可能写入前失效受影响的 `verified_fields`，postcondition 不会隐式恢复旧证据；
- 辅助模式转换和设备隐式变化全部出现在 closure、执行记录和 postcondition；
- `uncertain` 普通 I/O 零发送，只允许核心授权恢复或验证；
- `poisoned` 旧连接 OFF、IDN、恢复和验证均零发送；
- recovery 失败不会把 health 改回 `healthy`；
- Combine/Coupling 故障的 OFF 范围覆盖 descriptor 声明的所有受影响通道；
- 不可恢复的 ARB memory、外部触发和私有状态显式记录为未覆盖。

### V1 兼容

- V1 公共 model 构造和重导出测试保持通过；
- 旧 `SourceStatus.as_dict()`、`RestorableSourceState.as_dict()`、CLI status JSON、run plan schema、
  `run.json.restore` 和报告读取 fixture 保持通过；
- 新核心 + 旧插件使用不导入任何 V2 symbol 的真实 entry point fixture，V1 resolve、factory 和
  operation 成功，V2 operation 以缺 capability 且零 transport 调用拒绝；
- 受管的旧核心 + 新插件 fixture 在 wheel metadata 阶段拒绝，entry point import side-effect
  sentinel 为 0；越界安装的运行时 import 风险有显式负向测试，但不伪装成支持组合；
- Source V2 wheel dependency 与 descriptor 的 PEP 440 下界、上界、marker 和非法区间有一致性测试；
- descriptor/profile/version 错误在 factory 前拒绝；缺 Protocol 方法的 factory fixture 可以建立连接，
  但 operation method 调用数和协议发送数均为 0，且 transport 与租约已清理；
- 双合同驱动的全部 V1 setter、configure、ARB、ON/OFF、trigger、restore、run 和 TUI 路径均有
  同义／副作用重叠／不相交分类；前两类无直接 driver 旁路，不相交项保持 V1 的契约测试；
- Source V1 的 0.8.23 capability 冻结清单有静态测试，V2 注册不会改变旧 required method 映射；
- V1 adapter 不能用于 V2 输出放行、恢复或写前比较；
- 旧 CLI 参数与机器可读输出、run plan step/schema、TUI 和 V1-only 恢复调用顺序不因未声明 V2
  而失效；
- V1 `source.output ON` 对 `None`、字符串、`bool`、NaN、Infinity、负数和非 Vpp 单位返回稳定
  `ConfigError` 且写入数为 0；OFF 在幅度缺失时仍可执行；
- 新 Source V2 插件的 wheel 与 descriptor 下限指向第一个正式包含合同的核心版本。

R5 已加入以下纯离线兼容 fixture，作为上述要求的持续回归：

- 合成 Source V2 wheel 在旧核心版本模拟下由 metadata gate 拒绝，entry point import sentinel 保持为零；
- 合成受管 Source V2 wheel 的 `Requires-Dist` 与 descriptor `[min,max)` 相同才可通过 postflight；
  较低下界、较宽上界、排除首个支持版本、无生效 marker、重复依赖和非法 requirement 都失败关闭，
  失败安装回滚且不留下受管记录；
- 合成 V1 entry point 仍可 resolve、factory 并成功执行一个 V1 frequency setter；同一对象调用
  `source.snapshot_v2` 在 factory/driver I/O 前以缺 capability 拒绝；
- V1 route 清单、V1 capability→method 映射、run plan step 集合、TUI/CLI 间接入口，以及 V2 run
  plan 写 step 的解析拒绝均有静态测试。

### artifact 与 conformance

- operation artifact 和 conformance manifest 分别校验各自 schema，不能互换；
- manifest 缺少 `conformance_scheme`、适用域、摘要或 limitations 时不能支持 capability 发布；
- 历史裸 A0–A5 等级不会自动映射；
- `dist-info:` evidence ref 只能解析当前 distribution 内的规范路径，路径穿越和跨包引用拒绝；
- operation artifact 只保留 manifest ID/摘要，不包含原始 payload、授权 token 或完整 nonce。

## Accepted 决议基线

1. 11 个 `SourceFeatureProfile`、8 个 channel facet、2 个非通道状态、嵌套 helper、reason code 和
   canonical serializer 按本文冻结，不保留 `object` 或自由 mapping。
2. `source_extensions.__all__`、顶层 identity re-export 和 descriptor append-only 布局按本文冻结。
3. `source.snapshot_v2` 的 `OperationSpec`、Service、CLI JSON、snapshot document 和只读 operation
   artifact 按本文冻结。
4. 首个支持核心版本为 WaveBench `0.8.24`；Source V2 descriptor 必须使用 PEP 440 且声明
   `wavebench_min_version >= 0.8.24`。
5. 本次接受只授权 M1–M2。组合／标量 fake、负向测试和兼容矩阵属于实施退出门，不再作为
   `Accepted` 之前必须先写代码的条件。

后续任一 V2 写 capability 注册前，还必须补齐该 feature 的 request/result、完整
`SourceOperationContract`、closure、预算、恢复、artifact、双合同入口和 A0 验收；本 RFC
`Accepted` 不会自动批准全部保留写 ID。

## 实施里程碑

| 里程碑 | 状态 | 范围 | 退出条件 |
| --- | --- | --- | --- |
| M0 | `Accepted` | 冻结 R2 | snapshot 公共类型、serializer、OperationSpec、Service/CLI、版本门和验收门全部决定 |
| M1 | `implemented-unreleased` | 纯 model 与 descriptor validation | `Observed`、field/scope、typed profile、runtime narrowing、显式 `__all__`、旧位置参数和负向构造测试通过；不改变 V1 |
| M2 | `implemented-unreleased` | `source.snapshot_v2` | 组合响应型与独立标量型 fake、anchor/facet/anchor、activation、语义缺字段、deadline、query limit、传输异常、artifact、CLI 和一致性 fixture 通过 |
| M3 | `implemented-unreleased` | 纯预算与显式安全配置 | 配置迁移、端接、适用域、有限纯电阻 Thevenin、DC／Noise／Harmonic／AM／ARB／Sweep／Combine／共享功率正负 fixture 通过；仍不注册写 capability |
| M4 | `implemented-unreleased` | Source operation context | phase、单写、closure、cleanup reserve、session health、nonce 和恢复 fixture 通过；仍不注册写 capability |
| M4.5 | `implemented-unreleased` | V1 路由审计与 artifact 兼容防线 | 18 条 V1 写路由及其间接入口冻结；V2 写 operation/run plan 为零；空 `source_operations` 不改变 V1 `run.json`，非空根键保持 additive |
| C1 | `implemented-unreleased` | 受管插件版本门与兼容矩阵 | metadata import-before gate、wheel/descriptor PEP 440 交叉校验、合成 V1 entry point 的 V1 成功/V2 零 I/O 拒绝，以及生命周期回滚 fixture 通过 |
| M5 | 未授权 | 分 feature 写 capability | basic/output 起步；每项分别完成 request/result、预算、恢复、双合同映射和 A0 |
| M6 | 未授权 | ARB storage 与跨通道 | CAS storage、selection、Combine/Coupling 图、多通道 OFF 和共享功率合同通过 |
| M7 | 未授权 | 插件逐项 opt in | 在单独授权和合适接线下按 scheme `wavebench.source.a0-a5.v1` 逐 capability 验收 |
| P0 | `implemented-unreleased` | V1 `amplitude=None` 失败关闭 | ON 对缺失、非有限、非 VPP 或负 Vpp 在 driver 写入前返回稳定 `ConfigError`；OFF 保持原有可执行语义 |

## 已否决方案

- **继续向 V1 profile 增加 `Optional` 字段**：不能区分不支持、不适用、未查询和查询失败。
- **使用 0 或默认值补齐 profile**：会制造虚假谐波、Burst 周期或调制参数，并污染预算。
- **为每个厂商增加核心专用字段**：会将核心变成厂商协议集合。
- **用一个 `source.patch_v2` 授权全部 feature**：不同 feature 的字段闭包、预算、恢复和验收等级不同，
  一个 capability 会掩盖真实授权范围。
- **让 profile 查询临时写选择槽位**：破坏 query-only 语义，也无法在 `read_only` 会话中工作。
- **把 ARB 上传、选择和 ON 放进一个 operation**：上传会改变存储且可能不可恢复，选择会改变配置，
  ON 则开始能量转换，三者不能共享 baseline 或准入 nonce。
- **只修改 `SourceService.set_output()`**：会留下 ARB `output_on`、ON 状态 setter、trigger 和恢复 ON 旁路。
- **把复合安全预算全部交给插件**：核心无法保证不同 Service、run 和 TUI 入口使用同一决策。
- **把 session 锁存放在 Source Service 或 driver**：Service 可被重建，driver 不能自行完成核心验证授权。
- **结果未知时盲目重试或直接重连**：新连接不证明旧写入结果，也不证明配置已经恢复。
- **在 `poisoned` session 上发送应急 OFF**：通信边界已不可证，新 I/O 会扩大未知状态。
- **用历史示波器测量替代当前预算**：历史测量不是当前仪器状态或真实端接证明。
- **用人工强制参数放行未知预算**：未知 contributor 不是数值超限，不能通过提高阈值解决。

## R2 维护者决议

- Source V2 是 Source 领域合同，不与当前可执行仪器 API V2 混用名称或版本语义。
- executable API 保持 `wavebench.instrument.v2`；`source_extensions` append-only，新方法使用独立 Protocol。
- 受管安装使用 wheel metadata 作为 import 前版本门；runtime descriptor 门位于模块导入后、factory
  前，不对越界安装虚构零导入保证。
- Source V2 沿用现有 eager factory；实例 Protocol 验证位于 factory 后、operation 前，失败时关闭
  transport，且不发送 Source operation 命令。
- Source V1 在整个 executable API v2 生命周期内保留；立即冻结，满足迁移门后才弃用，只能随 V3 删除。
- 第一阶段只注册 `source.snapshot_v2`，不注册 V2 写 capability。
- 首个支持核心版本为 `0.8.24`；snapshot-only descriptor 的版本下限不得更低。
- 查询分为核心语义计划与插件协议计划；availability、runtime profile 和 consistency 由核心构造。
- 不提供统一 `source.patch_v2`、`source.arm_v2` 或 `source.fire_v2`；写能力按 feature 和事务闭包拆分。
- `Observed` 首版限于 Source V2，不在本 RFC 中扩展到 Scope、Power 或 DMM。
- descriptor extension 使用 typed profile，不使用任意 mapping 冻结安全语义。
- 复合预算由核心统一消费；插件提供厂商状态、typed constraint 和协议回读。
- `max_source_vpp` 与端口绝对电压上下限必须显式配置，不能互相推导；缺失时 V2 能量操作失败关闭。
- 只有 `HARD_CONSERVATIVE` 可以支持输出 ON 准入。
- Source V2 首个可写修订禁止 live mutation；未 opt in 的 V1 行为不变。
- ARB storage、selection/configuration 和 ON 使用三个独立 operation。
- Source 事务复用核心 session health 和授权底座，不新增平行 `state_uncertain` 布尔值。
- 失败恢复默认以 OFF 结束；重新 ON 是新的授权操作。
- operation artifact 与 conformance manifest 使用不同 schema。
- A0–A5 scheme 固定为 `wavebench.source.a0-a5.v1`，不替代运行时 snapshot、预算或 session 门。

## R5 维护者增补

- M4.5 只冻结 V1 写路由的审计事实和 run artifact 的可选命名空间；它不把任何 V1 路径自动迁移到
  V2，也不改变 V1 restore、`source_status` 或现有安全门的行为。
- C1 的 wheel/descriptor 交叉门只适用于声明 `source.snapshot_v2` 的受管插件；V1-only wheel 和
  descriptor 继续使用现有 executable API v2 兼容规则。
- 通用 wheel metadata gate 是 entry point import 前的保护；wheel/descriptor 交叉门必须等待
  descriptor 已加载，因此只承诺在 driver factory 和仪器 I/O 前失败关闭。
- R5 的合成 fixture 证明当前核心门禁及回滚语义，不替代历史核心发行物、外部插件 wheel 或实机的
  独立兼容与 conformance 验收。

## 剩余开放问题

以下问题不阻塞 snapshot-only 的 M1–M2，但会阻塞对应写 capability：

1. `SourceTerminationEvidence` 对应的 TOML、run intent 和 CLI 人工确认语法；公共类型和有效期规则已冻结。
2. 反应性、频率相关、非线性和未知负载是否扩展首版纯电阻模型；未扩展前固定失败关闭。
3. 各型号/固件 ARB 插值、重建滤波和硬过冲上界如何取得；没有硬证据时 storage/selection 可独立验收，ON 拒绝。
4. AM、FM、PM 及其它调制的载波幅度语义、过调制钳位和硬增益上界如何映射到 typed profile。
5. 具体设备的共享功率 envelope、热降额和跨通道关系证据；公共关系图与失败关闭规则已冻结。
6. 可选 RMS 安全配置键、默认迁移和哪些波形必须提供硬 RMS 上界。
7. conformance manifest 是否增加签名、签名信任根和长期保留策略；schema、scheme、wheel 路径和摘要已冻结。
8. 历史插件证据逐份迁移后的正式 manifest 清单；禁止全局等级替换。

这些问题描述的是「未来端口电压、电流或功率能否被保守上界覆盖」，只读 snapshot 不执行
setter、trigger、storage mutation 或输出转换，因此不需要用尚未证明的负载／过冲模型放行任何
动作。它们会阻塞写能力，是因为输出 ON、fire、恢复 ON 和 live mutation 必须证明完整目标状态
在实验台绝对电压、Vpp、端接和设备共享功率边界内；缺少任一硬边界时只能失败关闭。
