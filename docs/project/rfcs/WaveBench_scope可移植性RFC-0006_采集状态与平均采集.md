# RFC-0006：可移植的采集状态与平均采集 V2

> 状态：`Accepted R1（仅 RFC-0006a）`
> 核心基线：legacy acquisition/average API 与 R1.3 acquisition control
> 范围：普通采集状态 V2、平均配置和平均采集事务
> 系列总览：[scope 可移植性 RFC 组合说明](WaveBench_scope可移植性RFC组合说明.md)
> 本轮范围：0006a 已冻结只读模型、profile 与 Service 边界；0006b 仍等待独立的 bounded transaction
> 前置裁决，不创建代码或插件 opt-in

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
`"acquisition_type"`；条件父路径可在当前 mode 下覆盖其全部已声明子路径。

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

### RFC-0006a R1 接受合同

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

R1 只接受核心模型、profile、Protocol、factory gate、OperationSpec 与 Service 的离线实现。主包内建
descriptor 和外部插件在独立 conformance、版本下限和硬件证据完成前不得声明该 capability；status V2 的发布
也不授权 RFC-0006b average capture V2。

## RFC-0006b：平均采集 V2

### 候选请求与配置

~~~python
ScopeAverageMechanism = Literal[
    "global_acquisition",
    "channel_arithmetic",
    "combined",
]

ScopeAverageCompletionEvidence = Literal[
    "device_average_complete",
    "documented_single_completion",
]

SCOPE_AVERAGE_COUNT_MAX_V2 = 65_536


@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureRequestV2:
    channels: tuple[int, ...]
    average_count: int
    mechanism: ScopeAverageMechanism
    acquisition_stopped: Literal[True]
    points: str = "dmax"


@dataclass(frozen=True, slots=True)
class ScopeAverageConfigurationV2:
    mechanism: ScopeAverageMechanism
    acquisition_type: str | None
    average_count: int
    single_count: int | None = None
    channel_arithmetic: tuple[tuple[int, str], ...] | None = None


@dataclass(frozen=True, slots=True)
class ScopeAverageCompletionProofV2:
    evidence: ScopeAverageCompletionEvidence
    mechanism: ScopeAverageMechanism
    configured_average_count: int
    configuration_readback: ScopeAverageConfigurationV2
    acquisition_completion: ScopeAcquisitionCompletion
    device_average_complete: bool | None
    contract_id: str


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

成功结果没有 `"unknown"` completion evidence。无法证明完成时返回
`completion_unproven`，并按写入结果与 session health 进入恢复或锁存流程。

`waveforms` 固定为按 request channel 顺序排列的 `tuple[WaveformData, ...]`。
`WaveformData.channel` 是规范 channel 载体；结果 verifier 要求 channel tuple 与 request
精确相等。V2 Service 可以复用现有 capture package writer，但不得改变 capture 目录和
`WaveformData` schema；operation diagnostics 另行记录 average proof 与恢复证据。

### 模型不变量

候选模型必须通过 `__post_init__` 冻结以下规则：

- request channels 非空、唯一，均为正的非 bool 整数；
- average count 是 `2..SCOPE_AVERAGE_COUNT_MAX_V2` 的非 bool 整数，并由 profile 进一步收紧；
- mechanism 必须精确匹配 descriptor 中唯一的同名 completion variant；
- `acquisition_stopped is True`，不接受 truthy 值；
- points 使用标准 waveform points 规范化器，且必须由 average profile 的
  `supported_points` 支持；
- `global_acquisition` 要求非空 acquisition type，且
  `single_count/channel_arithmetic is None`；
- `channel_arithmetic` 要求正的 single count、非空且通道唯一的 arithmetic，且
  acquisition type 为 `None`；
- `combined` 同时要求 acquisition type、single count 和 channel arithmetic；
- channel arithmetic 的通道集合必须与 request channels 精确一致；
- configuration 中的 count、token 和 arithmetic 必须满足有限范围与 safe-token 规则；
- waveforms 的 channel 顺序与 request 精确一致，不重复、不缺失；
- configuration after 必须等于 before，run state before/after 都是已停止 baseline；
- restore 必须为 completed，并精确覆盖 profile restore order；
- verification 必须为 verified，覆盖相同字段且没有 mismatch；
- completion proof 的 mechanism/count/readback 与 request 和生效配置一致。

### Descriptor profile

平均采集是写入和 acquisition operation，必须由 descriptor 显式 opt-in：

~~~python
ScopeAverageCaptureStateField = Literal[
    "scope.channel.arithmetic",
]

ScopeAverageCaptureField = ScopeAverageCaptureStateField | ScopeWaveformTransferField


@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureBinaryProfile:
    response_max_bytes: int
    operation_max_bytes: int
    query_max_count: int
    resynchronization_max_bytes: int
    framing: BinaryResponseFraming = BinaryResponseFraming.DEFINITE_BLOCK
    transport_trailing_hex: str = ""


@dataclass(frozen=True, slots=True)
class ScopeAverageCompletionVariant:
    mechanism: ScopeAverageMechanism
    evidence: ScopeAverageCompletionEvidence
    contract_id: str


@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureProfile:
    variants: tuple[ScopeAverageCompletionVariant, ...]
    supported_points: tuple[str, ...]
    average_count_min: int
    average_count_max: int
    requires_power_of_two: bool
    binary: ScopeAverageCaptureBinaryProfile
    restore_order: tuple[ScopeAverageCaptureField, ...]
    snapshot_max_steps: int
    restore_max_steps: int
    verify_max_steps: int
~~~

该 profile 追加到 `ScopeDescriptorExtensions`。规则：

- 最小值和最大值为非 bool 整数，且 `2 <= min <= max`；
- variants 非空，mechanism 唯一；每种 mechanism 只绑定一种 completion evidence；
- `supported_points` 非空、唯一，并使用标准 waveform points 规范化结果；
- 每个 variant 的 `contract_id` 是稳定、非空、长度受限的 safe token，并标识对应
  mechanism/evidence 的厂商合同或 conformance 证据；
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
- restore order 唯一，并覆盖 operation 可能改动的全部字段；
- step 上限有限，并覆盖实际 snapshot/restore/verify 次数；
- capability、profile 和 required Protocol 必须一一对应；
- 运行时设备 query 只能收紧 profile，不能扩大 descriptor 声明。

### 与 RFC-0008 的二进制边界

`ScopeAverageCaptureBinaryProfile` 是 average capture 自己的候选 profile，不是
`ScopeWaveformBinaryProfile.operations` 的新 operation kind，也不复用标准 waveform 的
`fetch`／`capture_single`／`capture_multiple` profile。它自己的 `supported_points` 是 average
points 的唯一发送前依据；不得要求或推断 `scope.fetch_waveform` capability，不能把 average
读取伪装成标准 fetch。

两条路径只应共享已经冻结的 transport 安全语义：`query_binary()`、精确 trailing、四维限制的
逐项取最小值、可信 backend gate、no-replay 与 poisoned session。RFC-0008 的 profile schema、
capability 映射和标准 waveform 恢复闭包保持不变。

在实现 0006b 前，必须另行接受一个核心内部的通用 bounded transaction 前置合同，明确平均 profile
如何复用四维 limit validator、connection-limit merge、backend gate、ledger 安装和 construction
barrier，而不复制或绕过 RFC-0008 的安全逻辑。在该前置合同进入 `Accepted` 前，0006b 保持 blocked：
不得创建 `ScopeAverageCaptureBinaryProfile`、`scope.capture_average_v2`、相关 Protocol、CLI 或
descriptor 字段的运行代码。

### Baseline 与恢复 Protocol

R1.3 acquisition baseline 只覆盖 run state、trigger 和 acquisition token，不能覆盖平均、
通道、时基和 waveform transfer。平均采集必须使用独立的完整 baseline：

~~~python
@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureStateSnapshot:
    configuration: ScopeAverageConfigurationV2
    run_state: ScopeAcquisitionRunState
    state_tokens: tuple[tuple[ScopeAverageCaptureStateField, ScopeStateToken], ...]
    waveform_transfer: ScopeWaveformTransferStateSnapshot


@dataclass(frozen=True, slots=True)
class ScopeAverageCaptureBaseline:
    context_id: str
    session_epoch: str
    baseline_nonce: str
    snapshot: ScopeAverageCaptureStateSnapshot
    restore_order: tuple[ScopeAverageCaptureField, ...]


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

`state_tokens` 只接受 average-specific `scope.channel.arithmetic` safe token，每个字段最多一次。
snapshot 必须和 waveform transfer 子快照共同精确覆盖 profile restore order；通用
run/acquisition/trigger/timebase/channel/transfer 字段只能存在于 RFC-0008 已冻结的子快照中，
不在 `state_tokens` 重复编码。typed `run_state` 必须与子快照的 run-state token 语义一致，
不一致时 baseline 构造失败。

`scope.acquisition` 是 acquisition type 和 average count 的唯一 restore owner。profile 的
restore order 必须包含该整体字段，不得另列 `scope.acquisition.type` 或
`scope.acquisition.average_count`。configuration 的 granular 字段仍进入
`changed_fields`、postcondition 和 fresh verification，用于证明整体 token 确实恢复了 type/count；
它们不获得第二套恢复写入顺序。

候选 driver facet：

~~~python
class ScopeAverageCaptureDriverV2(Protocol):
    def snapshot_average_capture_state(
        self,
        fields: tuple[ScopeAverageCaptureField, ...],
    ) -> ScopeAverageCaptureStateSnapshot: ...

    def configure_average_capture_v2(
        self,
        request: ScopeAverageCaptureRequestV2,
        *,
        baseline: ScopeAverageCaptureBaseline,
    ) -> ScopeAverageConfigurationV2: ...

    def get_device_average_complete_v2(self) -> bool | None: ...

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

核心只能在同一 parent context 中通过一个 average 专用内部 adapter 复用 R1.3 acquisition control
driver facet 取得 `ScopeAcquisitionCompletion`。它不得调用公开的 `ScopeService.acquire_single()` 或
`ScopeExtensionService.acquire_single()`，也不得创建嵌套 operation/context、独立 deadline、error
phase 或 binary ledger。核心从 average baseline 派生绑定同一 context/epoch 的 acquisition 子
baseline；该子 baseline 有独立一次性 nonce，但不能重置 parent 的任何预算或错误阶段。

average operation 无论主流程成功还是失败，都恢复完整
`ScopeAverageCaptureBaseline`。这与 R1.3 独立 SINGLE 成功后有意保留停止记录的语义不同，
不能直接复用 SINGLE 的「成功不恢复」结论。

### Capability、依赖与 OperationSpec

候选 capability：

~~~text
scope.capture_average_v2
~~~

声明该 capability 必须同时满足：

- `scope.acquisition_status_v2`；
- `scope.acquisition_run_state`；
- `scope.acquisition_control`；
- `ScopeDescriptorExtensions.average_capture_profile`；
- 上述 average snapshot/configure/completion/fetch/restore/verify 方法。

average capture 使用自己的 `ScopeAverageCaptureBinaryProfile` 和
`fetch_average_waveform_bounded()`，不把 RFC-0008 的标准 `operation_kind="fetch"` 复用为
另一个 public operation，也不要求 descriptor 声明 `scope.fetch_waveform`。两条路径只共享
`query_binary()`、四维 ledger、backend gate、no-replay 和 poison 合同；方法存在不产生标准
fetch capability。插件可以复用私有 preamble/decoder 代码，但两个 profile 分别校验。

本节只冻结候选依赖图。直到「与 RFC-0008 的二进制边界」列出的前置合同进入 `Accepted`，
上述 capability、profile、Protocol 和 `OperationSpec` 均不得实现或在 descriptor 中声明。

`scope.channel_input_state_v2` 是 switchable-termination descriptor 的条件依赖；
`scope.error_drain_v1` 是有效 error policy 非 disabled 时的条件依赖。factory 必须将静态依赖、
profile 和全部 required methods 一次校验，不得先按 legacy average 方法拒绝后再尝试 V2。

候选 `OperationSpec` 使用 `effect="acquire"`、exclusive lease、完整 changed/verification
fields、`restore_coverage="average-capture-baseline"`、60 秒核心 deadline 和 RFC-0008
四维 binary ceiling。有效 binary 限制由核心 ceiling、average binary profile 和 connection
limit 逐项取最小值。

### 核心编排

平均采集不得只交给插件中的一个自由事务方法。核心必须持有 operation context、baseline、
deadline、ledger 和恢复授权。候选顺序为：

~~~text
identity + input safety + stopped preflight
  -> snapshot acquisition/average/channel/transfer state
  -> error-before
  -> configure average + per-write readback
  -> acquire + completion proof
  -> bounded waveform fetch
  -> error-after
  -> restore
  -> fresh verify
~~~

单通道和多通道共享一次 acquisition、一个 baseline、一个 deadline 和一个 binary ledger。
不得为了补齐失败通道隐式重新触发。

### 必须覆盖的状态

changed/restore/verification 字段至少包括实际采用的下列集合：

~~~text
scope.run_state
scope.acquisition
scope.acquisition.type
scope.acquisition.average_count
scope.trigger
scope.channel.arithmetic
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

列表中的 `scope.acquisition.type` 和 `scope.acquisition.average_count` 属于 granular
changed/verification fields；restore order 只使用其 owner `scope.acquisition`。profile 可以
排除设备明确不会改变的其他字段，但必须有静态证据。不能只比较
`configuration_before == configuration_after` 而遗漏 run state、全局 acquisition type 或
transfer state。成功结果还必须满足 configuration before/after 的规范化值相等、run state
回到已停止 baseline，并由 fresh readback 证明；只比较 driver 返回对象不构成恢复证据。

### 完成证明

`device_average_complete` 只在设备提供文档化完成位且新鲜 query 读到完成时成立。

`documented_single_completion` 只在厂商合同明确说明一次完成式 SINGLE 会等待指定平均次数
累积完成时成立。仅观察到 trigger `STOP`、`*OPC?` 完成或波形可读不足以采用该证据。

descriptor 声明证据类型只表示插件允许实现该分支，不代替厂商文档、fixture 或实机验收。

`ScopeAverageCompletionProofV2` 还必须满足：

- configuration readback 与 request 的 mechanism/count 精确一致；
- acquisition completion 绑定本 operation 派生的子 baseline，并证明一次新采集；
- `device_average_complete` 分支要求 fresh `device_average_complete is True`；
- `documented_single_completion` 分支要求 device field 为 `None`，且 contract ID 与
  descriptor 中同 mechanism/evidence variant 的 contract ID 精确一致；
- `device_average_complete` 分支同样要求 contract ID 与对应 variant 一致；
- completion、configuration readback、parent baseline 和 session epoch 由核心 verifier
  交叉核对；
- 普通 R1.3 `ScopeAcquisitionCompletion` 单独存在不足以构造成功 proof。

### 输入和前置条件

- request 必须显式确认 acquisition 已停止；
- core fresh-readback 仍须验证实际 run state；
- channels 非空、唯一并符合 descriptor 范围；
- average count 落在核心、profile 和连接限制的交集；
- switchable termination 的新路径依赖 RFC-0002；
- `high_z` 通过，已证明的 `50_ohm` 只在显式授权下通过，unknown 拒绝；
- 波形读取必须使用本 RFC 的 average binary profile 和同一 opaque ledger；不得回退
  `query_bin_block()` 或标准 legacy fetch。

### 失败与恢复

1. 参数、capability、profile、输入安全和完成证据配置在主 I/O 前校验。
2. 每次配置写入后立即 readback；设备自动取整导致不一致时失败。
3. 同步仍可证明的数据或换算失败执行 restore + fresh verify。
4. 完成证据不足时，记录主失败并恢复所有已写字段。
5. session `poisoned` 后禁止 STOP、restore、verify、截图和探测 I/O。
6. transport 主异常保持 primary cause；cleanup diagnostics 不得覆盖。
7. restore 或 verify 失败时不返回波形成功值。
8. 不自动重连、不重放 acquisition/binary query、不从中间 channel 继续重采。

## 错误检查策略

average capture 是 `acquire` operation，只允许
`on_instrument_error="fail"`。`check_errors=true` 时要求
`scope.error_drain_v1`；`false` 时 disabled。recovery phase 固定不读取错误队列。

## 兼容性

1. 旧 `ScopeAcquisitionStatus`、`ScopeAverageCaptureRequest`、
   `ScopeAverageConfiguration` 和 `ScopeAverageCaptureResult` 不改。
2. 旧 `scope.acquisition_status` 和 `scope.capture_average` 不改 capability 映射。
3. R1.3 acquisition control 不改名、不扩成 average completion。
4. 新 capability 使用独立 V2 模型和 profile。
5. 旧 CLI 命令继续走 legacy Service；0006a R1 不新增 V2 CLI、run plan step 或 artifact，0006b
   Draft 也不新增。
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

- profile 范围、mechanism、power-of-two 和 completion evidence；
- stopped/input safety/channel/count 的发送前拒绝；
- 每次写入与 readback；
- 完成位、文档化 single proof 和 completion-unproven；
- bounded response/total/query/resync/deadline；
- 单/多通道同一次 acquisition；
- baseline nonce、恢复顺序、step 上限和 fresh verify；
- 数据错误后恢复、同步失步后零追加 I/O；
- before/after typed error drain；
- legacy average、standard capture、CLI 和 artifact reader 回归。

## 接受与实施顺序

RFC-0006a 已进入 `Accepted`，只授权上一节列出的只读核心实现与离线验收。以下仍是 RFC-0006b
的后续接受门，不授权其代码、descriptor 字段或插件 opt-in：

1. 冻结 completion evidence、平均次数核心硬上限和 generic bounded transaction 前置合同；
2. 该前置合同进入 `Accepted` 后，才评审 average profile、snapshot/restore/verify 模型；
3. 再评审 RFC-0002 输入安全、average binary profile 与 RFC-0008 transport 基础的接入；
4. 只有前述各项均已实现并完成单通道离线验收后，才评审同次多通道；
5. 发行兼容矩阵完成后，插件才能单独 opt-in。

具体设备在 average mode 下缺少平均完成证据时，可以采用 status V2 并将
`average.complete=None` 记录为 unavailable，但不能声明 average capture V2。
