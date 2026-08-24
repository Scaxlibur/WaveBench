# RFC-0006：可移植的采集状态与平均采集 V2

> 状态：`Draft R1`
> 核心基线：legacy acquisition/average API 与 R1.3 acquisition control
> 范围：普通采集状态 V2、平均配置和平均采集事务
> 系列总览：[scope 可移植性 RFC 组合说明](WaveBench_scope可移植性RFC组合说明.md)

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

### 候选模型

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
~~~

`run_state` 应直接复用 R1.3 类型，不再定义含义重叠的字符串。若设备没有声明
`scope.acquisition_run_state`，status V2 可以把该分区保持 unavailable；不得由 trigger token
临时拼出一个弱化对象。

### 候选能力

~~~python
class ScopeAcquisitionStatusDriverV2(Protocol):
    def get_acquisition_status_v2(
        self,
    ) -> ScopeAcquisitionStatusV2: ...
~~~

~~~text
scope.acquisition_status_v2 -> get_acquisition_status_v2
~~~

该 operation 是 `stateful_read / exclusive`。它不触发、停止或重新配置 acquisition。

### 状态不变量

- average configured count 是正的非 bool 整数；
- `complete=None` 表示设备没有完成位；
- segmented 数量非空时为非负非 bool 整数；
- sample rate 为有限正数，memory depth 为正的非 bool 整数；
- acquisition type 使用规范化 safe token，不保存原始 SCPI；
- `STOP`、`*OPC?`、已配置 count 或 elapsed time 不得填充 `complete=True`；
- 声明可读的字段 query 失败时 operation 失败，不改写为 unavailable。

`unavailable_fields` 只接受 `ScopeAcquisitionStatusFieldV2`。路径必须合法、唯一、排序，
并满足：

- 分区整体为 `None` 时记录 `"average"`、`"segmented"` 或 `"run_state"`，不再同时记录
  其子路径；
- 分区存在但叶字段不可提供时只记录叶路径，例如 `"average.complete"`；
- 路径对应字段必须为 `None`；
- 非空字段不得列入 unavailable；
- 同一结果不得同时包含父路径和其子路径。

所有候选 dataclass 必须在 `__post_init__` 中执行上述验证，不能只依赖 Service 文本约定。

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

核心在同一 parent context 中复用 R1.3 acquisition control driver facet取得
`ScopeAcquisitionCompletion`，但不调用公开 `ScopeService.acquire_single()`，也不创建嵌套
operation。核心从 average baseline 派生绑定同一 context/epoch 的 acquisition 子 baseline；
该子 baseline 有独立一次性 nonce，但不能重置 deadline、error phase 或 binary ledger。

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
5. 旧 CLI 命令继续走 legacy Service；V2 CLI 只能追加。
6. 内建 driver 没有 opt-in 时继续在 capability gate 拒绝，不做探测。
7. status V2 发布不自动授权 average capture V2。
8. average core 合同发布不自动授权某个型号的 completion evidence。
9. `scope.capture_average_v2` 不调用 legacy `capture_average()`，也不回退 legacy。

## 验收矩阵

### Status V2

- average/segmented 分区分别可用或 unavailable；
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

## 实施顺序

1. 先实现 RFC-0006a 的纯读取模型。
2. 冻结 completion evidence 与平均次数核心硬上限。
3. 实现 average profile、snapshot/restore/verify 模型。
4. 接入 RFC-0002 输入安全和 RFC-0008 bounded waveform。
5. 实现单通道，再实现同次多通道。
6. 完成发行兼容矩阵后，插件才能单独 opt-in。

具体设备缺少平均完成证据时，可以采用 status V2 并返回 `complete=None`，但不能声明
average capture V2。
