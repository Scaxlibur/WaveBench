# RFC-0004：可移植的数字通道状态 V2

> 状态：`Draft R1`
> 核心基线：现有 `ScopeDigitalChannelStatus`
> 目标：保留未知值、共享状态和字段作用域
> 系列总览：[scope 可移植性 RFC 组合说明](WaveBench_scope可移植性RFC组合说明.md)

## 摘要

现有 `ScopeDigitalChannelStatus` 要求一次填满 activity、technology、threshold、
hysteresis、deskew、size、position、label 等字段。该模型适合能够查询完整状态的仪器，
不能诚实表达只提供其中一部分状态的设备。

本 RFC 提议追加 digital status V2。新模型不把不可查询字段填成默认枚举或零值，也不把
整机、POD 和逐通道状态混在同一作用域中。现有 digital waveform bitset 模型保持独立。

## 当前问题

跨厂商数字状态通常分成四类：

- 模块或逻辑分析选件是否存在；
- D0～Dn 的逐通道显示、位置和标签；
- 一组数字通道共享的 POD 阈值；
- 整机共享的数字显示大小和 timing calibration。

旧模型中的 `threshold_coupled`、`deskew_s` 和 `size` 没有明确区分这些作用域。
设备没有 activity、technology、hysteresis 或 label-enabled query 时，填入 `LOW`、
`MANUAL`、`NORMAL`、`False` 或空字符串都会制造并不存在的设备状态。

## 候选公共模型

候选模型按作用域分层：

~~~python
ScopeDigitalThresholdScope = Literal["channel", "pod", "unknown"]
ScopeDigitalActivityV2 = Literal["LOW", "HIGH", "TOGGLE", "unknown"]
ScopeDigitalTechnologyV2 = Literal["TTL", "ECL", "CMOS", "MANUAL", "unknown"]
ScopeDigitalHysteresisV2 = Literal["MAXIMUM", "ROBUST", "NORMAL", "unknown"]
ScopeDigitalSizeV2 = Literal[
    "SMALL",
    "MEDIUM",
    "LARGE",
    "DIV1",
    "DIV2",
    "DIV4",
    "DIV8",
    "unknown",
]

ScopeDigitalStatusFieldV2 = Literal[
    "displayed",
    "position_div",
    "label",
    "label_enabled",
    "activity",
    "technology",
    "hysteresis",
    "pod",
    "pod.threshold_v",
    "pod.threshold_scope",
    "shared",
    "shared.module_present",
    "shared.timing_calibration_s",
    "shared.size",
]


@dataclass(frozen=True, slots=True)
class ScopeDigitalPodStatusV2:
    start_channel: int
    stop_channel: int
    threshold_v: float | None = None
    threshold_scope: ScopeDigitalThresholdScope | None = None


@dataclass(frozen=True, slots=True)
class ScopeDigitalSharedStatusV2:
    module_present: bool | None = None
    timing_calibration_s: float | None = None
    size: ScopeDigitalSizeV2 | None = None


@dataclass(frozen=True, slots=True)
class ScopeDigitalChannelStatusV2:
    channel: int
    displayed: bool | None = None
    position_div: float | None = None
    label: str | None = None
    label_enabled: bool | None = None
    activity: ScopeDigitalActivityV2 | None = None
    technology: ScopeDigitalTechnologyV2 | None = None
    hysteresis: ScopeDigitalHysteresisV2 | None = None
    pod: ScopeDigitalPodStatusV2 | None = None
    shared: ScopeDigitalSharedStatusV2 | None = None
    unavailable_fields: tuple[ScopeDigitalStatusFieldV2, ...] = ()
~~~

以上名称仍为候选。V2 枚举显式增加 `"unknown"`，不修改旧 Literal。进入 `Accepted` 前需要
确认 `timing_calibration_s` 是否应进一步拆成独立整机状态。无论最后采用平铺还是分层模型，
字段作用域和 unknown/unavailable 区别都不得丢失。

候选 Protocol 与 capability：

~~~python
class ScopeDigitalStatusDriverV2(Protocol):
    def get_digital_status_v2(
        self,
        channel: int,
    ) -> ScopeDigitalChannelStatusV2: ...
~~~

~~~text
scope.digital_status_v2 -> get_digital_status_v2
~~~

该 operation 是 `stateful_read / exclusive`。它不触发 acquisition，也不读取 waveform
推断 activity。

## 不变量

### 通道与 POD

- channel、POD 起止编号都是非负或正的非 bool 整数，最终编号基准在接受前冻结；
- POD 范围必须包含当前 channel；
- `start_channel <= stop_channel`；
- POD 阈值不能伪装成逐通道独立阈值；
- `threshold_scope="pod"` 时必须存在 POD 范围；
- threshold、position 和 timing 数值必须有限。
- shared 分区存在时至少一个叶字段非空；全为空时使用 `shared=None` 和父 unavailable path。

### 未提供字段

- 静态不可查询字段为 `None`；
- 对应路径进入已排序、去重的 `unavailable_fields`；
- 设备明确返回 unknown token 时使用类型允许的 unknown 值，不把它写成 unavailable；
- 单次 query 失败、响应截断或解析失败使 operation 失败；
- 全局字段不能复制后再描述为「逐通道独立读值」。

`unavailable_fields` 只接受 `ScopeDigitalStatusFieldV2`。pod/shared 整体为 `None` 时只记录
父路径，不再记录其子路径；分区存在时父路径不得出现，空叶字段各自记录叶路径。同一结果
不得同时包含父路径与其子路径。每个 `None` 必须恰好由一个路径解释，非空字段不得出现在
路径集合。

### 标签

`label=""` 只能表示设备实际返回空标签。`label=None` 表示没有可证明的标签 query。
`label_enabled=None` 不能从标签字符串是否为空推导。

所有候选 public dataclass 必须在 `__post_init__` 中执行本 RFC 的类型、有限数值、作用域、
枚举和 availability 不变量。构造失败属于参数或 driver contract failure；Service 不修正
无效对象。

## Capability 与 Service

- capability 声明但缺少 V2 方法时，factory 在仪器 I/O 前拒绝；
- 方法存在但 capability 未声明时不暴露 Service；
- 参数错误在 I/O 前拒绝；
- Service 返回 V2 模型，不适配成旧 `ScopeDigitalChannelStatus`；
- CLI 若进入后续实施，只能追加 V2 入口，旧 `digital-status` 输出不改变；
- capability explain 应明确显示「旧 digital status」与「V2 digital status」是两项独立能力。

## 与 digital waveform 的边界

`ScopeDigitalWaveform` 的 `uint16` bitset 足以表达 D0～D15 的逻辑结果，本 RFC 不修改该
模型。具体插件在声明 `scope.digital_waveform` 前仍须证明：

- BYTE/WORD payload 到 LOW/HIGH 的编码；
- 多字节样本的字节序；
- 数字 channel 到 bit 位置的映射；
- 点数、X 轴和同次 acquisition 关系；
- transfer 状态恢复。

digital status V2 通过不能替代这些证据，也不能自动产生 digital waveform capability。

## 兼容性

1. 保留 `scope.digital_status -> get_digital_status`。
2. 不修改旧 `ScopeDigitalChannelStatus` 必填字段。
3. 旧 driver 可以只实现旧 capability。
4. 新 driver 可以同时声明两项能力，但不得用 V2 的 `None` 填充旧必填模型。
5. 内建 descriptor 没有声明 digital capability 时继续在 capability gate 拒绝，不新增探测。
6. 序列化保留 `null` 和稳定 unavailable paths，不格式化成默认枚举。

## 验收矩阵

- 模型：channel/POD 范围、有限值、bool-as-int、稳定 unavailable paths；
- 作用域：逐通道、POD 与 shared 字段不互相冒充；
- unknown：静态 unavailable、查询后 unknown、I/O/解析失败三者分离；
- label：空字符串、不可查询和 enabled unknown；
- capability：缺方法、额外方法、未声明能力和 construction barrier 零 I/O；
- Service/JSON：null、枚举和字段路径稳定；
- legacy：旧模型、旧 capability、CLI、fake 和内建 driver 回归；
- waveform：V2 status 不产生 waveform capability。

## 非目标

- 不通过 waveform 推断 activity；
- 不根据阈值猜测 TTL、ECL 或 CMOS technology；
- 不增加数字 channel setter；
- 不在本 RFC 中冻结数字 waveform 编码；
- 不把某个厂商的 POD 布局写成所有示波器的固定通道数。
