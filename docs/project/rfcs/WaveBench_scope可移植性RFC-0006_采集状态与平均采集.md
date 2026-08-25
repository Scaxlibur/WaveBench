# RFC-0006：可移植的采集状态与平均采集 V2

> 状态：`Implemented R1（未发布；0006a/0006b-0 内部前置）；Accepted R1（0006b 单通道）`
> 核心基线：legacy acquisition/average API 与 R1.3 acquisition control
> 范围：普通采集状态 V2、平均配置和平均采集事务
> 系列总览：[scope 可移植性 RFC 组合说明](WaveBench_scope可移植性RFC组合说明.md)
> 本轮范围：0006a 已完成核心只读模型、profile、factory gate 与 Service；0006b-0 已完成内部
> bounded transaction 内核；0006b R1 已接受单通道 public contract，核心 public 实现尚未开始，插件 opt-in 仍为 Draft

## 摘要

本 RFC 把两个不同问题分开：

1. RFC-0006a：读取设备实际具备的 acquisition、average 和 segmented 状态；
2. RFC-0006b：配置平均采集、证明完成、取得波形并恢复全部状态。

核心 R1.3 已经实现 run state、continuous start、完成式 single 和 stop。该合同不等于平均状态，
也不能由 SINGLE 完成证明推出 average complete。本 RFC 复用 R1.3 控制，不再建立第二套
start/single/stop API。

## 当前问题

现有 `ScopeAcquisitionStatus` 强制要求平均和分段字段同时存在。现有
`ScopeAverageConfiguration` 又固定要求 `single_count` 和逐通道 arithmetic。不同设备可能
使用全局 acquisition type、逐通道 arithmetic 或两者组合，不能用默认值把一种机制伪装成
另一种。

现有 `ScopeAverageCaptureRequest` 还把平均次数固定为 2～1024 的 2 次幂。设备范围应由有限的
核心硬上限与 descriptor profile 共同收紧，不能把某一仪器的限制写成跨厂商事实。

## 与 R1.3 acquisition control 的关系

以下公共合同直接复用：

- `ScopeAcquisitionRunState`；
- `ScopeAcquisitionControlProfile`；
- `scope.acquisition_run_state`；
- `scope.acquisition_control`；
- start/single/stop 的 baseline、completion proof、failure cleanup 和 fresh verification。

复用边界：

- run state 只描述当前观察；
- `ScopeAcquisitionCompletion` 只证明一次 SINGLE；
- `STOP` 不证明平均累积完成；
- `*OPC?` 不证明平均累积完成；
- acquisition count 的变化不自动等于平均次数已经满足。

## RFC-0006a：采集状态 V2

### R1 模型

~~~python
ScopeAcquisitionStatusFieldV2 = Literal[
    "acquisition_type",
    "run_state",
    "sample_rate_hz",
    "memory_depth",
    "average",
    "average.configured_count",
    "average.complete",
    "segmented",
    "segmented.option_installed",
    "segmented.enabled",
    "segmented.maximum_enabled",
    "segmented.capacity",
    "segmented.available",
]


@dataclass(frozen=True, slots=True)
class ScopeAverageStatusV2:
    configured_count: int
    complete: bool | None = None


@dataclass(frozen=True, slots=True)
class ScopeSegmentedStatusV2:
    option_installed: bool | None = None
    enabled: bool | None = None
    maximum_enabled: bool | None = None
    capacity: int | None = None
    available: int | None = None


@dataclass(frozen=True, slots=True)
class ScopeAcquisitionStatusV2:
    acquisition_type: str | None = None
    run_state: ScopeAcquisitionRunState | None = None
    sample_rate_hz: float | None = None
    memory_depth: int | None = None
    average: ScopeAverageStatusV2 | None = None
    segmented: ScopeSegmentedStatusV2 | None = None
    unavailable_fields: tuple[ScopeAcquisitionStatusFieldV2, ...] = ()
    not_applicable_fields: tuple[ScopeAcquisitionStatusFieldV2, ...] = ()
~~~

~~~python
SCOPE_ACQUISITION_STATUS_V2_MAX_QUERIES = 32


@dataclass(frozen=True, slots=True)
class ScopeAcquisitionStatusProfileV2:
    readable_fields: tuple[ScopeAcquisitionStatusFieldV2, ...]
    max_queries: int
    conditionally_applicable_fields: tuple[ScopeAcquisitionStatusFieldV2, ...] = ()
    allowed_effect: Literal["pure_read"] = "pure_read"
~~~

该 profile 以 append-only 的 `ScopeDescriptorExtensions.acquisition_status_profile_v2` 追加，且只有
声明 `scope.acquisition_status_v2` 的 descriptor 才能提供它。它不是 R1.3 acquisition control profile，
也不改变 legacy `ScopeAcquisitionStatus`。

`readable_fields` 是本设备可能返回值的完整字段闭包，按 `ScopeAcquisitionStatusFieldV2` 声明顺序排列、
去重，必须包含非条件的 `"acquisition_type"`。`"average"`／`"segmented"` 是分区路径：任何子路径
出现时，父路径必须出现；`"average"` 还要求 `"average.configured_count"`，`"segmented"` 至少要求一个
segmented 叶路径。`conditionally_applicable_fields` 是 `readable_fields` 的子集，不能包含
`"acquisition_type"`；条件父路径可在当前 mode 下覆盖其全部子路径，包括 profile 未声明的静态叶字段。

`max_queries` 是 `1..32` 的非 bool 整数。它计数从进入 `get_acquisition_status_v2()` 到返回或抛出期间的
全部受 guard 计数的文本 `query()`，包括 acquisition type、适用性、run state 和所有叶字段的判定。
这个 phase 只允许 `query()`；禁止 write、binary query、`query_float_list()`、legacy `idn()`、`*STB?`、
`*ESR?`、error drain、acquisition control 或任何额外 Service preflight。超额是 driver 合同违反，operation
失败，不重放或续读。

`run_state` 应直接复用 R1.3 类型，不再定义含义重叠的字符串。若设备没有声明
`scope.acquisition_run_state`，status V2 可以把该分区保持 unavailable；不得由 trigger token
临时拼出一个弱化对象。

### R1 capability

~~~python
class ScopeAcquisitionStatusDriverV2(Protocol):
    def get_acquisition_status_v2(
        self,
        *,
        fields: tuple[ScopeAcquisitionStatusFieldV2, ...],
    ) -> ScopeAcquisitionStatusV2: ...
~~~

~~~text
scope.acquisition_status_v2 -> get_acquisition_status_v2
~~~

该 operation 是 `stateful_read / exclusive`。它不触发、停止或重新配置 acquisition；Service 只调用这个
V2 方法一次，并将 profile 的 `readable_fields` 原样传入，不调用 legacy status、average capture 或 R1.3
acquisition-control Service。

### 状态不变量

- average configured count 是正的非 bool 整数；
- `complete=None` 表示设备没有完成位；
- segmented 数量非空时为非负非 bool 整数；
- sample rate 为有限正数，memory depth 为正的非 bool 整数；
- acquisition type 使用规范化 safe token，不保存原始 SCPI；
- `STOP`、`*OPC?`、已配置 count 或 elapsed time 不得填充 `complete=True`；
- 声明可读的字段 query 失败时 operation 失败，不改写为 unavailable。

`unavailable_fields` 只接受 `ScopeAcquisitionStatusFieldV2`。路径必须合法、唯一、排序，并满足：

- `unavailable_fields` 只表示 descriptor 静态无法提供的字段；
- `not_applicable_fields` 只表示当前已读取 mode 下没有语义的字段；
- 两个 tuple 都按 `ScopeAcquisitionStatusFieldV2` 声明顺序排序、去重且彼此互斥；
- 分区整体为 `None` 时，在其中恰好一组记录 `"average"`、`"segmented"` 或 `"run_state"`，
  不再同时记录其子路径；
- 分区存在但叶字段不可提供或当前不适用时，只记录相应叶路径，例如
  `"average.complete"`；
- 路径对应字段必须为 `None`；非空字段不得列入任一 tuple；
- 同一结果不得在任一 tuple 内或两者之间同时包含父路径和其子路径。

两个 availability tuple 是结果 `None` 值的最小、精确覆盖：普通顶层字段为 `None` 时使用自己的路径；
`average`／`segmented` 整体为 `None` 时只使用父路径，父路径覆盖其全部子路径；分区存在时只使用其中
为 `None` 的叶路径。父路径不得和任何子路径同时出现，即使它们位于不同 tuple。`run_state` 没有子路径，
为 `None` 时只使用 `"run_state"`。

profile 和结果还必须相互收紧：未列入 `readable_fields` 的字段只能由 `unavailable_fields` 覆盖；已列入
且非条件的字段必须有值，除非被当前 `not_applicable_fields` 中的条件父路径覆盖；条件字段可以有值，或由
自己／条件父路径进入 `not_applicable_fields`。声明可读字段的 query、解析或类型验证失败必须使 operation
失败，不能转写为任一 availability path。

当前 acquisition mode 不是 average 时，整个 `average` 分区或只在该 mode 下无意义的叶字段必须
进入 `not_applicable_fields`，不能伪装成静态 unavailable。当前 mode 是 average 但设备没有完成
位时，才将 `"average.complete"` 记录为静态 unavailable；`complete=True` 只能来自文档化的完成位。
segmented 分区也按相同规则区分「设备没有查询合同」和「当前配置下没有语义」。

若 profile 不含 `"run_state"`，结果必须将它静态标记为 unavailable。若 profile 包含它，descriptor 必须
同时声明 `scope.acquisition_run_state`；V2 driver 可以在同一 query budget 内读取自身的状态证据，但不得调用
`ScopeExtensionService`、legacy `get_acquisition_status()` 或从 trigger token 推导 run state。已声明独立
run-state capability 不强制 profile 返回该字段。

所有 R1 dataclass 必须在 `__post_init__` 中执行上述验证，不能只依赖 Service 文本约定。

### RFC-0006a R1 核心实现状态

核心开发线已实现 `ScopeAcquisitionStatusV2`、average/segmented 分区模型、append-only
`ScopeAcquisitionStatusProfileV2`、独立 Protocol、`scope.acquisition_status_v2`、strict factory
construction barrier、portability-V2 `OperationSpec` 和 `ScopeService.acquisition_status_v2()`。

`scope.acquisition_status_v2` 与 profile、`ScopeAcquisitionStatusDriverV2.get_acquisition_status_v2()` 一一对应。
factory 在首次仪器 I/O 前必须同时校验 capability、append-only profile、严格核心版本门、可调用方法，以及
上述 run-state 条件依赖；声明该 capability 时启用 strict construction barrier。factory 返回 driver、profile 和
Protocol/backend 校验完成前，所有 query、write、binary 和 OPC I/O 都必须以
`factory_construction_pending` 发送前拒绝；失败时关闭已开的 transport。额外存在但未声明的 V2 方法不能产生
capability 或 latch。

该 operation 使用独立 portability-V2 `OperationSpec`：60 秒 deadline、`stateful_read / exclusive`、
`error_check_minimum="disabled"`、无 required verified fields、无 restore coverage。`ScopeService`
`acquisition_status_v2()` 在一个受 `max_queries` 限制的 normal phase 中运行，因此没有 legacy identity
preflight、R1.3 identity preflight、error drain 或 `scope.acquisition_run_state` Service 调用。

R1 不新增 V2 CLI、run-plan step、artifact 或持久化 JSON schema。现有 `wavebench scope acquisition-status`、
其文本／JSON 输出、`ScopeService.acquisition_status()`、`ScopeAcquisitionStatus`、
`scope.acquisition_status -> get_acquisition_status()` 均保持不变；即使 descriptor 同时声明 V1 和 V2，旧命令
也只走 legacy 路由。V2 Service 不得 fallback 到 V1 status、`capture_average()` 或 R1.3 control。

R1 的核心离线实现已通过模型/profile、capability、factory、纯文本 query budget、legacy route 和
MSO8000 未 opt-in 兼容回归。主包内建 descriptor 和外部插件在独立 conformance、版本下限和硬件证据完成前
不得声明该 capability；status V2 的核心实现或发布也不授权 RFC-0006b average capture V2。

## RFC-0006b：平均采集 V2

### R1 单通道请求与配置

~~~python
ScopeAverageMechanism = Literal["global_acquisition"]
ScopeAverageCompletionEvidence = Literal["device_average_complete"]

SCOPE_AVERAGE_COUNT_MAX_V2 = 65_536


@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureRequestV2:
    channels: tuple[int, ...]
    average_count: int
    mechanism: ScopeAverageMechanism
    acquisition_stopped: Literal[True]
    points: str = "dmax"
    allow_50ohm: bool = False


@dataclass(frozen=True, slots=True)
class ScopeAverageConfigurationV2:
    mechanism: ScopeAverageMechanism
    acquisition_type: str
    average_count: int


@dataclass(frozen=True, slots=True)
class ScopeAverageCompletionProofV2:
    evidence: ScopeAverageCompletionEvidence
    mechanism: ScopeAverageMechanism
    configured_average_count: int
    configuration_readback: ScopeAverageConfigurationV2
    acquisition_completion: ScopeAcquisitionCompletion
    device_average_complete: Literal[True]
    contract_id: str
    context_id: str
    session_epoch: str
    acquisition_baseline_nonce_digest: str


@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureResultV2:
    request: ScopeAverageCaptureRequestV2
    waveforms: tuple[WaveformData, ...]
    configuration_before: ScopeAverageConfigurationV2
    configuration_after: ScopeAverageConfigurationV2
    run_state_before: ScopeAcquisitionRunState
    run_state_after: ScopeAcquisitionRunState
    completion: ScopeAverageCompletionProofV2
    restore: "ScopeAverageCaptureRestoreResult"
    verification: "ScopeAverageCaptureVerification"
~~~

R1 只接受一个 request channel、`global_acquisition` 和 `device_average_complete`。`channels` 保留 tuple
形状以便后续追加多通道变体，但 R1 构造和 profile preflight 必须要求其长度恰为 1。`channel_arithmetic`、
`combined`、callbacks、partial result 和 `documented_single_completion` 均不属于 R1，必须通过后续独立
接受的 R2 合同追加。

成功结果没有 `"unknown"` completion evidence。completion 无法证明、`device_average_complete` 为 false、
类型不符或 query/parse 失败均为 `completion_unproven` operation failure；不得返回部分 waveform、
缓存结果或带空 completion 的成功对象。

`waveforms` 固定为按 request channel 顺序排列的 `tuple[WaveformData, ...]`，R1 长度恰为 1。
`WaveformData.channel` 是规范 channel 载体；结果 verifier 要求唯一 channel 与 request 精确相等。
R1 只提供 Service dataclass 返回，不创建 V2 CLI、run-plan step、capture package writer 或新的 artifact
schema；operation diagnostics 只使用既有脱敏 scope-operation / error-check envelope。

### 模型不变量

R1 dataclass 必须通过 `__post_init__` 冻结以下规则：

- request channels 为长度恰 1 的 tuple，其中 channel 为正的非 bool 整数；
- average count 是 `2..SCOPE_AVERAGE_COUNT_MAX_V2` 的非 bool 整数，并由 profile 进一步收紧；
- mechanism 只能为 `"global_acquisition"`；
- `acquisition_stopped is True`，不接受 truthy 值；
- `allow_50ohm` 必须是真 bool；它是本 operation 的显式输入安全授权，不改变 descriptor 或仪器设置；
- points 先由标准 waveform points 规范化器收敛到 `"def"`、`"max"` 或 `"dmax"`，再由 profile 的
  `supported_points` 发送前校验；
- configuration 的 acquisition type 是非空 safe token，mechanism/count 与 request 和 profile 精确一致；
- waveforms 的唯一 channel 与 request 精确相等；
- configuration after 必须等于 before，run state before/after 都是已停止 baseline；
- restore 必须为 completed，并精确覆盖 profile restore order；
- verification 必须为 verified，覆盖相同字段且没有 mismatch；
- completion proof 的 mechanism/count/readback、context/epoch 和 child-baseline nonce digest 与 request、
  profile 和生效配置精确一致；`device_average_complete` 必须是真正的 `True`。

### Descriptor profile

平均采集是写入和 acquisition operation，必须由 descriptor 显式 opt-in：

~~~python
ScopeAverageCaptureField = Literal[
    "scope.run_state",
    "scope.acquisition",
    "scope.trigger",
    "scope.timebase",
    "scope.channel_display",
    "scope.channel_vertical",
    "scope.waveform_source",
    "scope.waveform_mode",
    "scope.query_response_header",
    "scope.waveform_format",
    "scope.waveform_byte_order",
    "scope.waveform_points",
    "scope.waveform_transfer_window",
]


@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureBinaryProfile:
    response_max_bytes: int
    operation_max_bytes: int
    query_max_count: int
    resynchronization_max_bytes: int
    framing: BinaryResponseFraming = BinaryResponseFraming.DEFINITE_BLOCK
    transport_trailing_hex: str = ""


@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureProfileV2:
    global_acquisition_type: str
    completion_contract_id: str
    channel_range: tuple[int, int]
    supported_points: tuple[str, ...]
    average_count_min: int
    average_count_max: int
    requires_power_of_two: bool
    binary: ScopeAverageCaptureBinaryProfile
    restore_order: tuple[ScopeAverageCaptureField, ...]
    snapshot_max_steps: int
    main_max_steps: int
    restore_max_steps: int
    verify_max_steps: int
~~~

该 profile 以 append-only 的 `ScopeDescriptorExtensions.average_capture_profile_v2` 追加。规则：

- 最小值和最大值为非 bool 整数，且 `2 <= min <= max`；
- `global_acquisition_type` 与 `completion_contract_id` 都是非空、长度受限的 safe token；后者只标识
  已审查的设备完成位合同，不替代厂商文档、离线 fixture 或实机验收；
- channel range 是两个满足 `1 <= min <= max` 的非 bool 整数；R1 request 的唯一 channel 必须落入范围；
- `supported_points` 非空、唯一，并使用标准 waveform points 规范化结果；
- profile 只能收紧核心硬上限；
- `average_count_max <= SCOPE_AVERAGE_COUNT_MAX_V2`；首版核心上限 `65_536` 是有限资源门，
  不表示任一设备支持该值，descriptor 必须进一步收紧；
- `requires_power_of_two` 必须是真正的 bool；
- binary framing 首版固定为 `DEFINITE_BLOCK`，trailing 是最长 16 bytes 的精确小写
  十六进制；response/total/query 为有限正的非 bool 整数，resynchronization 为有限非负的
  非 bool 整数；
- binary profile、OperationSpec 和 connection limit 逐项取最小值，且不得超过 RFC-0008 的
  `8 MiB / 64 MiB / 256 / 64 KiB` 核心 ceiling；
- 首版沿用 RFC-0008 的可信 PyVISA/RsInstrument VISA `INSTR` backend gate；Serial、
  SocketIO、duck transport 或仅实现公开 `query_binary()` 的对象在 binary command 前拒绝；
- restore order 唯一、按 `ScopeAverageCaptureField` 声明顺序排列，且 R1 必须覆盖全部字段；
  `scope.acquisition` 是 acquisition type/count 的唯一 restore owner，不能用
  `scope.acquisition.type` 或 `scope.acquisition.average_count` 替代；
- snapshot/restore/verify step 上限有限且不少于 restore field 数；`main_max_steps` 是 `8..128` 的
  非 bool 整数，覆盖两次 configuration write、两次即时 readback、stopped recheck、一次 SINGLE、
  一次 fresh device-complete read 和一次 bounded waveform fetch；
- capability、profile 和 required Protocol 必须一一对应；
- 运行时设备 query 只能收紧 profile，不能扩大 descriptor 声明。

### 与 RFC-0008 的二进制边界

`ScopeAverageCaptureBinaryProfile` 是 average capture 自己的 R1 profile，不是
`ScopeWaveformBinaryProfile.operations` 的新 operation kind，也不复用标准 waveform 的
`fetch`／`capture_single`／`capture_multiple` profile。它自己的 `supported_points` 是 average
points 的唯一发送前依据；不得要求或推断 `scope.fetch_waveform` capability，不能把 average
读取伪装成标准 fetch。

两条路径只应共享已经冻结的 transport 安全语义：`query_binary()`、精确 trailing、四维限制的
逐项取最小值、可信 backend gate、no-replay 与 poisoned session。RFC-0008 的 profile schema、
capability 映射和标准 waveform 恢复闭包保持不变。

average 不得复制或绕过 RFC-0008 的安全逻辑；它只可接入已由下节 0006b-0 冻结的通用 bounded
transaction 内核。0006b-0 本身没有创建 `ScopeAverageCaptureBinaryProfile`、`scope.capture_average_v2`、
相关 Protocol、CLI 或 descriptor 字段；这些 R1 average 专属类型仅由本节的单通道合同授权。

### RFC-0006b-0：通用 bounded transaction 前置合同

本节是 core-only 的 `Accepted R1` 内部合同。它把 RFC-0008 已有的 `ScopeBinaryLimits`、
`BinaryQueryLedger`、`ScopeOperationContextCoordinator`、guarded `query_binary()` 和可信 backend
检查明确为可被未来 scope acquire/write operation 复用的最小内核；它不改变标准 waveform profile、
capability、baseline 或 executor，也不向插件暴露新入口。

#### 有效 binary 限制

任何 bounded operation 必须在创建 context 前同时得到完整的 operation-spec、descriptor-profile，以及完整或
未配置（视为 `+∞`）的 connection 四维限制。有效限制逐项计算为：

~~~text
effective_response_max = min(spec response, profile response, connection response)
effective_operation_max = min(spec total, profile total, connection total)
effective_query_max_count = min(spec query count, profile query count, connection query count)
effective_resynchronization_max = min(spec resync, profile resync, connection resync)
~~~

前三项是正的非 bool 整数，resynchronization 是非负的非 bool 整数，且 total 不小于 response。
profile 和 spec 缺任一项时在任何 I/O 前拒绝。connection 限制只能由核心配置／Service 传入；未配置某一
connection 层时，该层在内部交集计算中视为 `+∞`，不能由 driver、descriptor 或调用方构造、扩张或重置。
结果被 opaque ledger 固定，driver 只能把单次 `max_bytes` 进一步收紧。

#### factory backend 与 construction barrier

可信 binary backend 是 factory-only、operation-agnostic 的验证结论：仅核心验证过的 PyVISA 或
RsInstrument VISA message-based `INSTR` 路径可获得该标记。Serial、SocketIO、duck transport 或仅实现
公开 `query_binary()` 的对象都不通过。该标记绑定 factory 创建的 `GuardedAuditedTransport` 和同一 session
epoch，不可由 driver、profile 或 Service 设置；它不蕴含标准 waveform capability。

未来声明任何 bounded-binary V2 capability 时，该 capability 必须进入 strict construction latch 集合。
factory 可以打开 transport，但必须在 factory 返回、capability/profile/required-method/backend 通过全部静态
验证后才释放 latch；此前任何 I/O 均以 `factory_construction_pending` 发送前拒绝。profile 或方法缺失、
backend 不可信、transport 数量不是预期值时，关闭已开的 transport 且零仪器 I/O。标准 waveform 继续使用
同一 generic 验证结论，不改变其 descriptor profile/capability 映射。

#### context、ledger 与失败收敛

每个 bounded operation 只创建一个 core-owned context、absolute deadline 和 binary ledger；phase 严格顺序且
不可嵌套，binary I/O 仅在 main phase。ledger 不跨 context／epoch 复用，不退款、不重建，也不允许 replay、
continuation 或 legacy `query_bin_block()` fallback。进入 cleanup 后 ledger 立即失效。

同步已证明的 application/data failure 可以在同一 deadline 的静态 recovery/verification phase 中恢复；同步未知、
framing/termination 恢复失败或 resynchronization 超限立即 poisoned，之后禁止所有 backend I/O。cleanup 未完成、
restore/verify 失败或不能证明恢复，也必须以 poisoned 收敛且不得返回成功值。主异常保持 primary cause；
artifact 只记录现有 scope operation schema 中的 phase history、budget 摘要、session health、baseline nonce digest、
restore/verification outcome 和脱敏 cleanup diagnostics，不保存命令、payload 或 nonce 原文。

#### 0006b 接入条件

0006b 后续的 average profile、baseline、Protocol 和 executor 必须分别拥有自己的字段闭包、restore owner 和
OperationSpec；只能向本内核提交 limits、framing、phase specs 与 recovery/verification 闭包。它不得复用
`ScopeWaveformBinaryProfile.operations`、标准 waveform capability、`ScopeWaveformTransfer*` 类型或
`BoundedWaveformExecutor` 的业务编排。average 公共代码必须另行接受，并同时冻结 completion evidence、
composite baseline 精确覆盖、error policy 和无 CLI/artifact 边界；本文件后续的 R1 单通道合同完成了该裁决。

#### 0006b-0 R1 核心实现状态

核心开发线已将 factory-owned bounded backend marker 从 waveform 专用内部名称收敛为 operation-agnostic
bounded-binary marker：标准 waveform executor 继续检查同一 factory 验证结论，旧私有 waveform marker 和
validator 仅保留为委托到 generic 实现的兼容别名，不产生第二份状态或旁路。可信 backend 仍仅限
PyVISA/RsInstrument VISA `INSTR`，construction latch、transport 关闭和零 I/O failure 语义不变。

`ScopeOperationContextCoordinator` 已有的 `ScopeBinaryLimits.intersect()` 现以离线回归固定为
spec/profile/connection 三方逐项最小值；未提供 connection 限制仍等价于 `+∞`，直到另一个核心配置合同
引入真实 connection binary-limit source。该内部 refactor 本身没有创建 average capability、profile、Protocol、
descriptor 字段、CLI、artifact 或插件 opt-in。核心和外部 MSO8000 插件的既有离线回归均通过。

### R1 baseline 与恢复 Protocol

R1.3 acquisition baseline 只覆盖 run state、trigger 和 acquisition token，不能覆盖完整 average
配置和 waveform transfer。R1 average 使用独立的完整 baseline，并仅把 R1.3 baseline 作为同一 parent
context 内完成证明的 child baseline：

~~~python
@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureStateSnapshot:
    captured_fields: tuple[ScopeAverageCaptureField, ...]
    configuration: ScopeAverageConfigurationV2
    run_state: ScopeAcquisitionRunState
    run_state_token: str | None = None
    acquisition_token: str | None = None
    trigger_token: str | None = None
    timebase_token: str | None = None
    channel_display_token: str | None = None
    channel_vertical_token: str | None = None
    waveform_source_token: str | None = None
    waveform_mode_token: str | None = None
    query_response_header_token: str | None = None
    waveform_format_token: str | None = None
    waveform_byte_order_token: str | None = None
    waveform_points_token: str | None = None
    waveform_transfer_window_token: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureBaseline:
    context_id: str
    session_epoch: str
    baseline_nonce: str
    snapshot: ScopeAverageCaptureStateSnapshot
    restore_order: tuple[ScopeAverageCaptureField, ...]
    acquisition_baseline: ScopeAcquisitionControlBaseline


@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureRestoreResult:
    status: Literal["completed", "failed", "not_attempted"]
    attempted_fields: tuple[ScopeAverageCaptureField, ...]
    restored_fields: tuple[ScopeAverageCaptureField, ...]
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureVerification:
    status: Literal["verified", "mismatch", "unavailable"]
    verified_fields: tuple[ScopeAverageCaptureField, ...]
    mismatched_fields: tuple[ScopeAverageCaptureField, ...]
    error_code: str | None = None
~~~

`ScopeAverageCaptureStateSnapshot` 是 average 自己的 token 闭包，不复用
`ScopeWaveformTransferStateSnapshot`、`ScopeWaveformTransferBaseline`、restore result 或 verification
类型。`captured_fields` 必须与 parent restore order 精确相等；每个 captured field 只有一个 non-empty
safe token。typed `run_state` 和 `configuration` 是 token 的语义投影，不是第二个 restore owner。

`scope.acquisition` 是 acquisition type 和 average count 的唯一 restore owner。`scope.acquisition.type`
和 `scope.acquisition.average_count` 只可作为 changed/postcondition/fresh-verification fields，不能进入
average restore order。`ScopeAverageCaptureBaseline.acquisition_baseline` 的 context/epoch 必须与 parent
相同、nonce 必须不同；其 run state、trigger token 和 acquisition token 必须分别与 average snapshot 的投影及
对应 token 一致。child baseline 只用于 `ScopeAcquisitionCompletion` 验证，不能触发第二次 restore。

R1 driver facet：

~~~python
class ScopeAverageCaptureDriverV2(ScopeAcquisitionRunStateDriver, Protocol):
    def snapshot_average_capture_state(
        self,
        fields: tuple[ScopeAverageCaptureField, ...],
    ) -> ScopeAverageCaptureStateSnapshot: ...

    def set_average_acquisition_type_v2(
        self,
        acquisition_type: str,
        *,
        baseline: ScopeAverageCaptureBaseline,
    ) -> None: ...

    def get_average_configuration_v2(
        self,
        *,
        baseline: ScopeAverageCaptureBaseline,
    ) -> ScopeAverageConfigurationV2: ...

    def set_average_count_v2(
        self,
        average_count: int,
        *,
        baseline: ScopeAverageCaptureBaseline,
    ) -> None: ...

    def acquire_average_single_v2(
        self,
        *,
        baseline: ScopeAverageCaptureBaseline,
        deadline: float,
    ) -> ScopeAcquisitionCompletion: ...

    def get_device_average_complete_v2(
        self,
        *,
        baseline: ScopeAverageCaptureBaseline,
    ) -> bool: ...

    def fetch_average_waveform_bounded(
        self,
        channel: int,
        *,
        points: str,
        baseline: ScopeAverageCaptureBaseline,
    ) -> WaveformData: ...

    def restore_average_capture_state(
        self,
        baseline: ScopeAverageCaptureBaseline,
    ) -> ScopeAverageCaptureRestoreResult: ...

    def verify_average_capture_state_restored(
        self,
        baseline: ScopeAverageCaptureBaseline,
    ) -> ScopeAverageCaptureStateSnapshot: ...
~~~

core 只在同一 parent context 中调用这些方法。两次 setter 分别代表唯一的 global acquisition type 写入和
average count 写入；每次 setter 返回后，core 必须立即调用 `get_average_configuration_v2()`，确认 type 或
完整 type/count，之后才允许下一次 write、SINGLE 或 binary I/O。最终 aggregate configuration 不是替代
每次 readback 的证据。

`acquire_average_single_v2()` 只可使用 parent baseline 中的 child acquisition baseline，并返回
`ScopeAcquisitionCompletion`。core 使用现有 acquisition-control profile 对该 completion 进行验证；它不得调用
公开的 `ScopeService.acquire_single()` 或 `ScopeExtensionService.acquire_single()`，也不得创建嵌套
operation/context、独立 deadline、error phase 或 binary ledger。child baseline 不能重置 parent 的任何预算。

average operation 无论主流程成功还是失败，都恢复完整
`ScopeAverageCaptureBaseline`。这与 R1.3 独立 SINGLE 成功后有意保留停止记录的语义不同，
不能直接复用 SINGLE 的「成功不恢复」结论。

### R1 capability、依赖与 OperationSpec

R1 capability：

~~~text
scope.capture_average_v2
~~~

声明该 capability 必须同时满足：

- `scope.idn`；
- `scope.acquisition_status_v2`；
- `scope.acquisition_run_state`；
- `scope.acquisition_control`；
- `scope.channel_input_state_v2`；
- `ScopeDescriptorExtensions.average_capture_profile_v2`；
- `get_channel_input_state_v2()`、`get_acquisition_run_state()` 和上述 average
  snapshot/set/readback/acquire/completion/fetch/restore/verify 方法。

average capture 使用自己的 `ScopeAverageCaptureBinaryProfile` 和
`fetch_average_waveform_bounded()`，不把 RFC-0008 的标准 `operation_kind="fetch"` 复用为
另一个 public operation，也不要求 descriptor 声明 `scope.fetch_waveform`。两条路径只共享
`query_binary()`、四维 ledger、backend gate、no-replay 和 poison 合同；方法存在不产生标准
fetch capability。插件可以复用私有 preamble/decoder 代码，但两个 profile 分别校验。

R1 的上述 capability/profile/Protocol/OperationSpec 以本节进入 `Accepted` 后才可实现或在 descriptor 中声明。

`scope.channel_input_state_v2` 是 R1 的必需依赖，不回退 legacy coupling；
`scope.error_drain_v1` 是有效 error policy 非 disabled 时的条件依赖。factory 必须将静态依赖、
profile 和全部 required methods 一次校验，不得先按 legacy average 方法拒绝后再尝试 V2。

R1 `OperationSpec` 使用 `effect="acquire"`、exclusive lease、`restore_coverage="average-capture-baseline"`、
60 秒核心 deadline 和 RFC-0008 四维 binary ceiling。其 restore/cleanup verification fields 是完整
`ScopeAverageCaptureField` 闭包；`scope.acquisition.type` 和 `scope.acquisition.average_count` 只追加到
changed/postcondition verification fields。有效 binary 限制由核心 ceiling、average binary profile 和 connection
limit 逐项取最小值。

### 核心编排

平均采集不得只交给插件中的一个自由事务方法。core 必须持有 operation context、baseline、
deadline、ledger 和恢复授权。R1 顺序为：

~~~text
identity + exact input-state safety + stopped snapshot preflight
  -> error-before
  -> one MAIN: type write/readback -> count write/readback -> stopped recheck
     -> SINGLE completion -> fresh device-average-complete -> one bounded waveform fetch
  -> error-after
  -> restore
  -> fresh verify
~~~

R1 的 preflight 在任何 write 前读取唯一 channel 的 `ScopeChannelInputStateV2` 和 average snapshot。
输入 state 的 channel 必须等于 request；只有 `high_z` 默认通过，`50_ohm` 还必须由
`request.allow_50ohm is True` 显式授权，`unknown`、query/parse failure、channel mismatch 或非 stopped state
均发送前失败。core 不发送 STOP 来满足这一前置条件。main 只能进入一次；不存在第二 channel、callback、
partial result、自动重触发或中间续读。

### 必须覆盖的状态

R1 changed/restore/verification 字段使用下列集合：

~~~text
scope.run_state
scope.acquisition
scope.acquisition.type
scope.acquisition.average_count
scope.trigger
scope.timebase
scope.channel_display
scope.channel_vertical
scope.waveform_source
scope.waveform_mode
scope.query_response_header
scope.waveform_format
scope.waveform_byte_order
scope.waveform_points
scope.waveform_transfer_window
~~~

`scope.acquisition.type` 和 `scope.acquisition.average_count` 属于 granular changed/verification fields；
restore order 只使用其 owner `scope.acquisition`。R1 profile 不得排除任何
`ScopeAverageCaptureField`。不能只比较 `configuration_before == configuration_after` 而遗漏 run state、
全局 acquisition type 或 transfer token。成功结果还必须满足 configuration before/after 的规范化值相等、
run state 回到 stopped baseline，并由 fresh readback 证明；只比较 driver 返回对象不构成恢复证据。

### 完成证明

R1 只接受 `device_average_complete`：设备必须有文档化完成位，且在同一 main phase、configuration
readback 和已验证的 SINGLE completion 后，以新鲜 query 返回真正的 `True`。仅观察到 trigger `STOP`、
`*OPC?` 完成、count 变化或波形可读不足以采用该证据。

`completion_contract_id` 标识 descriptor 声明的完成位合同，不代替厂商文档、fixture 或实机验收；没有完成位、
不能读到 true 或无法关联到 profile contract 时不得声明 R1 capability。`documented_single_completion`
必须通过后续 R2 接受门，冻结厂商文档适用范围、fixture/实机证据和 core verifier 后才能加入。

`ScopeAverageCompletionProofV2` 还必须满足：

- configuration readback 与 request 的 mechanism/count 精确一致；
- acquisition completion 绑定本 operation 派生的子 baseline，并证明一次新采集；
- `device_average_complete` 分支要求 fresh `device_average_complete is True`；
- contract ID 与 profile 的 completion contract ID 精确一致；
- context、session epoch 和 child-baseline nonce digest 与 parent baseline 精确一致；
- completion、configuration readback、parent baseline 和 session epoch 由核心 verifier
  交叉核对；
- 普通 R1.3 `ScopeAcquisitionCompletion` 单独存在不足以构造成功 proof。

### 输入和前置条件

- request 必须显式确认 acquisition 已停止；
- core fresh-readback 仍须验证实际 run state；
- 唯一 channel 符合 descriptor range；
- average count 落在核心和 profile 的交集；binary response/total/query/resync 另按三方限制交集计算；
- R1 的新路径依赖 RFC-0002 input-state，不以 switchable/固定 termination 区分或回退 legacy coupling；
- `high_z` 通过，已证明的 `50_ohm` 只在显式授权下通过，unknown 拒绝；
- 波形读取必须使用本 RFC 的 average binary profile 和同一 opaque ledger；不得回退
  `query_bin_block()` 或标准 legacy fetch。

### 失败与恢复

1. 参数、capability、profile、输入安全和完成证据配置在主 I/O 前校验。
2. 每次 configuration write 后立即 readback；设备自动取整、type/count 错配或 stopped state 漂移时失败。
3. 同步仍可证明的数据或换算失败执行 restore + fresh verify。
4. 完成证据不足时，记录主失败并恢复所有已写字段。
5. session `poisoned` 后禁止 STOP、restore、verify、截图和探测 I/O。
6. transport 主异常保持 primary cause；cleanup diagnostics 不得覆盖。
7. restore 或 verify 失败时不返回波形成功值。
8. 不自动重连、不重放 acquisition/binary query，也不追加第二 channel。

## 错误检查策略

average capture 是 `acquire` operation。R1 固定从 `ScopeConfig.check_errors` 得到以下唯一映射：

~~~text
true  -> ErrorCheckSpec(policy="required", timing="before_and_after", max_records=16,
                        on_instrument_error="fail")
false -> ErrorCheckSpec(policy="disabled")
~~~

required 路径在 first write 前要求 `scope.error_drain_v1` 与 callable `drain_errors()`，并完成
error-before。error-after 只在 final main I/O 成功后、restore 前执行；main failure 或 session unhealthy
导致无法执行时，既有 error artifact 必须分别记录 `main_operation_failed` 或 `session_unhealthy`。
recovery/verification 固定不读取错误队列；legacy `scope.errors` 不得代替 typed drain。

## 兼容性

1. 旧 `ScopeAcquisitionStatus`、`ScopeAverageCaptureRequest`、
   `ScopeAverageConfiguration` 和 `ScopeAverageCaptureResult` 不改。
2. 旧 `scope.acquisition_status` 和 `scope.capture_average` 不改 capability 映射。
3. R1.3 acquisition control 不改名、不扩成 average completion。
4. 新 capability 使用独立 V2 模型和 profile。
5. 旧 CLI 命令继续走 legacy Service；0006a 和 0006b R1 均不新增 V2 CLI、run plan step、capture package
   格式或 artifact schema。
6. 内建 driver 没有 opt-in 时继续在 capability gate 拒绝，不做探测。
7. status V2 发布不自动授权 average capture V2。
8. average core 合同发布不自动授权某个型号的 completion evidence。
9. `scope.capture_average_v2` 不调用 legacy `capture_average()`，也不回退 legacy。

## 验收矩阵

### Status V2

- average/segmented 分区分别可用、unavailable 或当前 mode 下 not applicable；
- STOP、OPC、configured count 和 elapsed time 都不能伪造 complete；
- 数值、safe token、unavailable paths 和 query failure；
- capability/Protocol/factory 零 I/O；
- legacy acquisition status 与 R1.3 control 回归。

### Average capture V2

- R1 profile range、`global_acquisition`、power-of-two、canonical points 和 `device_average_complete`；
- 单 channel/stopped/input-state/count 的发送前拒绝，以及 `50_ohm` 的显式 request 授权；
- type write/readback、count write/readback、stopped recheck、child-baseline-bound SINGLE 与 fresh complete bit；
- bounded response/total/query/resync/deadline、factory generic backend gate 和 no-replay；
- 独立 average baseline、restore owner、nonce digest、step 上限、fresh verify 与成功后同样 restore；
- 数据错误后恢复、同步失步后零追加 I/O、primary exception 和 cleanup poison；
- before/after typed error drain 及固定 omission artifact；
- legacy average、standard waveform、CLI、run plan、artifact reader 和 builtin descriptor 回归；
- R2 前置：多通道、callbacks、partial result、channel arithmetic、combined mechanism 和
  `documented_single_completion` 均不得作为 R1 成功路径。

## 接受与实施顺序

RFC-0006a/0006b-0 已完成核心离线实现但尚未发布。RFC-0006b R1 现接受单通道
`global_acquisition + device_average_complete` 的 public contract，为后续独立的 model/profile/baseline/
Protocol/OperationSpec/Service/factory gate 和单通道离线验收冻结边界；它不授权 CLI、run plan、artifact schema、
内建 descriptor、外部插件 opt-in 或任何 R2 变体。本轮只完成这项文档接受，不开始公共实现。后续实施顺序为：

1. 先实现本节 R1 单通道合同并完成离线 conformance；
2. 只有 R1 单通道回归、发行兼容矩阵和独立设备证据完成后，才评审同次多通道；
3. `channel_arithmetic`、`combined`、callbacks、partial result 和 `documented_single_completion` 必须各自
   通过独立 Accepted 附录；
4. 插件只在首个包含完整合同的正式核心发行版、离线 conformance 和对应实机证据均具备后单独 opt-in。

具体设备在 average mode 下缺少平均完成证据时，可以采用 status V2 并将
`average.complete=None` 记录为 unavailable，但不能声明 average capture V2。
