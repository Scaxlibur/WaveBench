# RFC-0005：可组合的示波器状态快照 V2

> 状态：`Draft R1`
> 核心基线：现有完整 `ScopeSnapshot` 与 `status_summary()`
> 目标：允许设备返回可证明的类型化分区，不伪造缺失字段
> 系列总览：[scope 可移植性 RFC 组合说明](WaveBench_scope可移植性RFC组合说明.md)

## 摘要

现有 `scope.snapshot` 要求一次返回 identity、health、channel、timebase、probe、waveform
和 trigger 七个完整分区。部分仪器能可靠读取其中大部分字段，却无法证明少数厂商特有状态。
为了补齐旧模型而填入零值、空字符串或默认枚举，会把「设备没有报告」变成错误事实。

本 RFC 提议追加 snapshot V2。身份仍是必需基线，其他分区和设备相关叶字段可以明确
unavailable。现有完整 snapshot 和 partial summary 均保持原样。

## 当前边界

核心当前提供两条不同入口：

- `scope.snapshot`：只有 driver 能填满现有 `ScopeSnapshot` 时才能声明；
- `status_summary()`：缺少完整 snapshot 时，只聚合已有 IDN 和 coupling。

partial summary 是安全降级，不是可组合 typed snapshot。直接向它增加不稳定字段会改变旧
JSON 和 CLI 形状，也无法表达字段级不可提供原因。

## 候选公共模型

~~~python
ScopeSnapshotFieldV2 = Literal[
    "identity.manufacturer",
    "identity.model",
    "identity.serial_number",
    "identity.firmware",
    "identity.options",
    "health.status_byte",
    "health.operation_condition",
    "health.questionable_condition",
    "health.acquisition_available",
    "health.acquisition_count",
    "health.sample_rate_hz",
    "health.error_queue_nonempty",
    "health.waiting_for_trigger",
    "channel.channel",
    "channel.enabled",
    "channel.coupling",
    "channel.range_v",
    "channel.scale_v_per_div",
    "channel.offset_v",
    "channel.position_div",
    "channel.bandwidth_hz",
    "channel.polarity",
    "channel.skew_s",
    "channel.label",
    "channel.label_enabled",
    "channel.overloaded",
    "channel.acquisition_type",
    "timebase.acquisition_time_s",
    "timebase.divisions",
    "timebase.position_s",
    "timebase.range_s",
    "timebase.reference_percent",
    "timebase.scale_s_per_div",
    "timebase.roll_enabled",
    "probe.channel",
    "probe.attenuation_factor",
    "probe.bandwidth_hz",
    "probe.capacitance_f",
    "probe.impedance_ohm",
    "probe.name",
    "probe.probe_type",
    "waveform.channel",
    "waveform.x_start_s",
    "waveform.x_stop_s",
    "waveform.points",
    "waveform.values_per_sample",
    "waveform.x_increment_s",
    "waveform.x_origin_s",
    "waveform.y_increment_v",
    "waveform.y_origin_v",
    "waveform.y_resolution_bits",
    "trigger.trigger_type",
    "trigger.source_channel",
    "trigger.mode",
    "trigger.slope",
    "trigger.coupling",
    "trigger.level_v",
    "trigger.hysteresis_mode",
    "trigger.holdoff_mode",
    "trigger.holdoff_time_s",
]


@dataclass(frozen=True, slots=True)
class ScopeHealthSnapshotV2:
    status_byte: int | None = None
    operation_condition: int | None = None
    questionable_condition: int | None = None
    acquisition_available: int | None = None
    acquisition_count: int | None = None
    sample_rate_hz: float | None = None
    error_queue_nonempty: bool | None = None
    waiting_for_trigger: bool | None = None


@dataclass(frozen=True, slots=True)
class ScopeAnalogChannelSnapshotV2:
    channel: int
    enabled: bool | None = None
    coupling: str | None = None
    range_v: float | None = None
    scale_v_per_div: float | None = None
    offset_v: float | None = None
    position_div: float | None = None
    bandwidth_hz: float | None = None
    polarity: str | None = None
    skew_s: float | None = None
    label: str | None = None
    label_enabled: bool | None = None
    overloaded: bool | None = None
    acquisition_type: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeTimebaseSnapshotV2:
    acquisition_time_s: float | None = None
    divisions: int | None = None
    position_s: float | None = None
    range_s: float | None = None
    reference_percent: float | None = None
    scale_s_per_div: float | None = None
    roll_enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class ScopeProbeSnapshotV2:
    channel: int
    attenuation_factor: float | None = None
    bandwidth_hz: float | None = None
    capacitance_f: float | None = None
    impedance_ohm: float | None = None
    name: str | None = None
    probe_type: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeWaveformMetadataSnapshotV2:
    channel: int
    x_start_s: float | None = None
    x_stop_s: float | None = None
    points: int | None = None
    values_per_sample: int | None = None
    x_increment_s: float | None = None
    x_origin_s: float | None = None
    y_increment_v: float | None = None
    y_origin_v: float | None = None
    y_resolution_bits: int | None = None


@dataclass(frozen=True, slots=True)
class ScopeTriggerSnapshotV2:
    trigger_type: str
    source_channel: int | None = None
    mode: str | None = None
    slope: str | None = None
    coupling: str | None = None
    level_v: float | None = None
    hysteresis_mode: str | None = None
    holdoff_mode: str | None = None
    holdoff_time_s: float | None = None


@dataclass(frozen=True, slots=True)
class ScopeSnapshotV2:
    identity: ScopeIdentitySnapshot
    health: ScopeHealthSnapshotV2 | None = None
    channel: ScopeAnalogChannelSnapshotV2 | None = None
    timebase: ScopeTimebaseSnapshotV2 | None = None
    probe: ScopeProbeSnapshotV2 | None = None
    waveform: ScopeWaveformMetadataSnapshotV2 | None = None
    trigger: ScopeTriggerSnapshotV2 | None = None
    unavailable_fields: tuple[ScopeSnapshotFieldV2, ...] = ()
    not_applicable_fields: tuple[ScopeSnapshotFieldV2, ...] = ()


@dataclass(frozen=True, slots=True)
class ScopeSnapshotProfileV2:
    readable_fields: tuple[ScopeSnapshotFieldV2, ...]
    max_queries: int
    conditionally_applicable_fields: tuple[ScopeSnapshotFieldV2, ...] = ()
    allowed_effect: Literal["pure_read"] = "pure_read"
~~~

每个 V2 子模型同样把非公共、非必有叶字段设为可空。仅把整个分区改成 `None` 而继续要求
分区内部所有旧字段非空，不能解决跨厂商问题。

`ScopeSnapshotFieldV2` 是公共模型字段路径的完整封闭集合。profile 的
`readable_fields` 非空、唯一，并且必须包含全部 `identity.*` 字段；
`conditionally_applicable_fields` 唯一且是 readable fields 的子集，不得包含 identity 或
分区身份字段。`max_queries` 是有限正的非 bool 整数。首版
`allowed_effect` 固定为 `"pure_read"`，不接受临时写入、binary query 或消费型 query。
profile 追加到 `ScopeDescriptorExtensions.snapshot_profile_v2`；运行时结果必须遵守
readable/conditional 的精确字段合同。

channel、probe 和 waveform 分区存在时，其 `channel` 必须等于请求 channel；trigger 分区
存在时 `trigger_type` 必须是非空 safe token。profile 只要声明某分区的其他字段，就必须同时
声明该分区的 channel 或 trigger-type 身份字段。timebase/health 没有分区身份字段，按叶字段
逐项声明。分区身份字段必须是非条件 readable。

候选 Protocol 与 capability：

~~~python
class ScopeSnapshotDriverV2(Protocol):
    def get_snapshot_v2(
        self,
        channel: int,
        *,
        fields: tuple[ScopeSnapshotFieldV2, ...],
    ) -> ScopeSnapshotV2: ...
~~~

~~~text
scope.snapshot_v2 -> get_snapshot_v2
~~~

候选 Service 名为 `snapshot_v2(channel)`。CLI 名称在进入 `Accepted` 前冻结；不得让旧
`scope status` 静默改变返回 schema。首版调用方不选择 fields；核心把已验证 profile 的
`readable_fields` 原样传给 driver，避免任意字段列表扩大 query 面。

## 可组合规则

### Identity

`identity` 必须来自当前连接代次的新鲜查询或该 operation 已验证的 identity 证据。不得从
descriptor 的型号字符串构造仪器身份。

### 分区

- 完整分区不可提供时，该分区为 `None`，并记录该分区的全部封闭叶路径；
- 分区存在但某个叶字段不可提供时，该叶字段为 `None`，并记录叶路径；
- 路径使用公共模型字段名，不使用厂商命令；
- 相同设备/profile 下的静态 unavailable 集应稳定；
- driver 不得因一次 query 失败临时把字段改成 unavailable 后返回成功。

上面的「运行时收紧」只允许 conditionally applicable 字段根据当前已查询状态进入
`not_applicable_fields`。profile 的 readable 非条件字段必须成功返回非空值，不能在运行时
改成 unavailable。profile 未列入 readable 的字段固定为 `None` 并进入
`unavailable_fields`；readable 条件字段当前不适用时固定为 `None` 并进入
`not_applicable_fields`。两组路径分别排序、去重、互斥，每个空叶字段恰好由其中一组解释，
非空字段不得出现在任一组。

当完整分区没有任何 readable 字段时，结果中的分区为 `None`，序列化器将该分区的全部封闭
叶路径规范化到 unavailable 集；公共模型不另引入不属于 `ScopeSnapshotFieldV2` 的父路径。

### 查询失败

声明可查询的字段发生 transport、协议或解析失败时，整个 operation 失败。已经取得的分区可以
作为脱敏 diagnostics 保存，但不能构造部分成功的 `ScopeSnapshotV2`。

这条规则避免同一 capability 在通信故障时悄悄缩小返回内容。以后若需要流式 partial result，
应新增明确的结果 envelope。

## 消费型状态

`NO_REPLAY` 只保证命令不被完整重放，不会消除读后清除副作用。首版 snapshot V2 中：

- `*STB?`、`*ESR?` 和错误队列默认不查询；
- `status_byte`、condition register 和 `error_queue_nonempty` 没有无损合同时保持
  unavailable；
- 不为填充 health 分区调用 `scope.error_drain_v1`；
- snapshot 不隐式 clear、截图、读取 waveform payload 或触发 acquisition。

因此 RFC-0001 不是 snapshot V2 的硬依赖。一个可选健康字段不能阻塞 channel、timebase、
probe、waveform metadata 或 trigger 分区。

## 状态读取边界

snapshot V2 是 `stateful_read / exclusive`，R1 只允许纯文本状态 query。核心把 phase 的
allowed I/O 固定为 text query，并将 guarded transport 的实际 query 增量与
`profile.max_queries` 对账。

若某个 waveform metadata query 会改变 source、format、range 或其他仪器状态，该字段在 R1
保持 unavailable。以后若确需可恢复读取，必须另行增加 profile、typed baseline、
restore/verification Protocol 和 changed/verification fields；不能把临时写入隐藏在
`get_snapshot_v2()` 中。

## Capability 与 factory

- capability 声明但缺 V2 方法时，在 factory 后、第一次仪器 I/O 前拒绝；
- 方法存在但 capability 未声明时不自动暴露；
- invalid channel 和请求 schema 在 I/O 前拒绝；
- construction barrier 覆盖 opt-in factory；
- capability 声明要求 `snapshot_profile_v2` 非空且通过静态校验；
- driver 的 fields/result 必须遵守 readable/conditional/unavailable 精确集合，不能扩大或
  静默缩小 descriptor 事实；
- query count 超出 profile 时是 driver contract violation，operation 失败。

所有候选 public dataclass，包括嵌套 V2 子模型，都必须在 `__post_init__` 中执行类型、有限
数值、字段集合和 availability 不变量。构造失败属于参数或 driver contract failure；Service
不修正无效对象。

## 兼容性

1. 保留现有 `ScopeSnapshot`、`scope.snapshot` 和 `get_snapshot()`。
2. 保留 `status()`、`status_summary()`、`--strict` 和既有 JSON/text 输出。
3. 新模型不通过 adapter 填充旧模型的强制字段。
4. 旧 driver、fake 和内建 descriptor 无需实现 V2。
5. 新 driver 可以同时声明完整旧 snapshot 和 V2；相同字段的值必须一致。
6. V2 capability 不表示返回所有分区，只表示返回对象遵守本 RFC 的可用性语义。

## Artifact 与序列化

Service 候选返回现有 `ScopeExtensionOperationResult`，value 为 `ScopeSnapshotV2`，
diagnostics 保存下列字段。operation artifact 复用 `wavebench.scope.operation.v1` 的追加字段，
不创建与 R1.3 平行的 envelope。

成功结果至少记录：

- schema/version；
- requested channel；
- available sections；
- sorted `unavailable_fields`；
- query count 摘要；
- session epoch 的脱敏关联；
- 是否执行了可恢复事务；
- completion 与 failure diagnostics。

artifact 不复制原始 SCPI、真实 resource、序列号或完整设备响应。`identity` 可以存在于
直接成功值，但 artifact 只保存脱敏身份摘要，不复制 `serial_number`。JSON 保留 `null`，CLI
使用「不可提供」或等价稳定文字，不显示成 `0`、`False` 或空字符串。

## 验收矩阵

- 模型：分区/叶字段可空、有限数值、路径稳定和 identity 必需；
- 一致性：每个 unavailable 路径对应空字段，非空字段不在路径集合；
- failure：单次 query/解析失败不降级成成功；
- consumption：不隐式读取 `*ESR?`、错误队列、截图或 waveform data；
- stateful metadata：如有临时写入，changed/restore/fresh verify 全覆盖；
- capability/factory：缺方法、额外方法、未声明能力和 construction barrier 零 I/O；
- legacy：完整 snapshot、partial summary、strict status、CLI 和 JSON 黄金基线；
- cross-driver：至少两个不同仪器族的 fixture 能返回不同分区子集。

## 开放问题

进入 `Accepted` 前必须裁决：

1. V2 Service 和 CLI 的最终命名；
2. identity 直接成功值和 CLI 的序列号显示策略。
