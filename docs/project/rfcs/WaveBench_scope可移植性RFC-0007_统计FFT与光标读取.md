# RFC-0007：可移植的统计、FFT 与光标读取 V2

> 状态：`Implemented R1（未发布；0007a/0007b）；Accepted R1（0007c）`
> 核心基线：现有 statistics、FFT status、math metadata 与 cursor readout
> 范围：三个独立的只读 V2 capability
> 系列总览：[scope 可移植性 RFC 组合说明](WaveBench_scope可移植性RFC组合说明.md)
> 本轮范围：0007a/0007b 已完成核心模型、profile、factory gate 与 Service；0007c 已接受核心合同，
> 仍不创建 V2 CLI、artifact、run plan step 或插件 opt-in

## 摘要

现有统计、FFT 和光标模型分别固定了 slot、RTM 风格 FFT 字段和单 source/固定单位。三项问题
只共享「不伪造未知值」原则，I/O、寻址和结果模型彼此独立。本 RFC 将它们拆成三个 capability，
允许分别实现、发布和验收。

现有 `scope.math_metadata` 保持有效；它提供 waveform preamble 和轴信息，不等于 FFT 状态。
现有插件已经公开的窄 cursor 子集只要能无损映射，继续保留。

## RFC-0007a：测量统计 selector

### 问题

旧 API 以整数 `slot` 寻址。部分设备只能用 measurement item 和 source 组合查询，且不能把
前面板历史位置反查为 item/source。把位置编号当作可查询 slot 会选择错误的测量项。

### R1 模型

~~~python
@dataclass(frozen=True, slots=True)
class ScopeMeasurementSelector:
    slot: int | None = None
    item: str | None = None
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScopeMeasurementStatisticsRequestV2:
    selector: ScopeMeasurementSelector
    configured: bool
    include_buffer: bool = False
    acquisition_stopped: bool = False


ScopeMeasurementSelectorMode = Literal["slot", "item_sources"]


@dataclass(frozen=True, slots=True)
class ScopeMeasurementStatisticsProfileV2:
    selector_modes: tuple[ScopeMeasurementSelectorMode, ...]
    max_queries: int
    supports_buffer: Literal[False] = False
    slot_range: tuple[int, int] | None = None
    supported_items: tuple[str, ...] = ()
    item_source_count_range: tuple[int, int] | None = None
    allowed_effect: Literal["pure_read"] = "pure_read"


@dataclass(frozen=True, slots=True)
class ScopeMeasurementStatisticsV2:
    selector: ScopeMeasurementSelector
    category: str
    actual: float
    average: float
    standard_deviation: float
    minimum: float
    maximum: float
    waveform_count: int
    buffered_values: tuple[float, ...] | None = None
~~~

R1 选择「完整统计成功值」：`actual`、`average`、`standard_deviation`、`minimum`、`maximum` 与
`waveform_count` 必须在成功结果中全部存在并通过有限数／范围校验。设备对任一聚合项返回空值、
未支持或无法解析时，operation 失败；不为 statistics V2 新增 availability path，也不把空值、
零值或旧缓存伪装成结果。这个保守边界允许旧 statistics API 继续保留其既有可空语义。

selector 必须且只能采用一种模式，且输入本身就是唯一规范形：核心不排序、不去重、不改大小写；结果的
`selector` 必须与 request selector 严格相等。

- slot 模式：`slot` 为正的非 bool 整数，`item is None`，`sources == ()`；
- item 模式：`slot is None`，item 为非空 safe token，sources 非空且唯一。

结果必须回显规范化 selector，不能只返回 category 而丢失 source。

### Buffer

R1 不读取 statistics 历史 buffer。`ScopeMeasurementStatisticsProfileV2.supports_buffer` 因而只能为
真正的 `False`，`include_buffer=True` 必须在打开 session／发送任何仪器 I/O 前失败，成功结果的
`buffered_values` 必须为 `None`。不得：

- 返回空 tuple 伪装支持；
- 重复查询 CURRENT 构造历史；
- 从 WaveBench 进程中的旧结果拼接设备 buffer；
- 以 binary、消费性 query 或未受独立 bounded/恢复合同保护的读取声称支持 buffer。

未来若设备有真实 buffer query，必须通过单独的 Accepted 附录冻结 response/operation/query/
resynchronization 限制、是否消费记录、session 后果与 restore/verify 需求；不能只把
`supports_buffer` 改为 `True`。

`ScopeMeasurementStatisticsProfileV2` 以 append-only 的
`ScopeDescriptorExtensions.measurement_statistics_profile_v2` 追加。selector modes 非空、唯一并按
`("slot", "item_sources")` 的声明顺序排列；`supports_buffer` 必须是真正的 `False`。`max_queries` 是
`1..32` 的非 bool 整数，`allowed_effect` 固定为 `"pure_read"`。包含 `slot` 时 slot range 必须为两个满足
`1 <= min <= max` 的非 bool 整数，否则必须为 `None`。包含 `item_sources` 时
`supported_items` 非空、唯一且均为 safe token，source count range 必须为两个满足
`1 <= min <= max` 的非 bool 整数；不包含该模式时 items 为空且 range 为 `None`。

请求的 selector mode、source 数量和 buffer 需求由核心在统计 I/O 前与 profile 比较。profile
还必须校验 slot 范围或 item allowlist。上述检查在打开 scope session 前执行；任何不匹配均发送前失败，
不能把这些判断推迟到已执行 CURRENT query 之后。

`max_queries` 计数从进入 `get_measurement_statistics_v2()` 到返回或抛出期间的全部受 guard 计数的
文本 `query()`，包括 configured 判定、selector 状态和六项统计值。该 phase 只允许 `query()`；禁止
write、binary query、`query_float_list()`、legacy `idn()`、`*STB?`、`*ESR?`、error drain、
acquisition control 和任何额外 Service preflight。超额是 driver 合同违反，operation 失败，不重放或续读。

`configured is True` 是调用方的明确意图确认；`False` 或 truthy 非 bool 在本地、零 I/O 拒绝。driver 仍须
在同一预算内取得新鲜证据，证明请求 selector 当前可被精确读取；未配置、selector 不匹配、状态模糊或读取
期间漂移均为 operation failure，不转成 availability、零值或缓存结果。`acquisition_stopped` 仍须是真正的
bool，但 R1 buffer 被拒绝，不据此调用 stop/single/control 或改变 acquisition。

R1 capability：

~~~text
scope.measurement_statistics_v2 -> get_measurement_statistics_v2
~~~

## RFC-0007b：FFT status V2

### 问题

旧 `ScopeFftStatus` 强制要求 average complete、RBW 和 sample rate，却不能承载 source、
window、vertical unit 与 frequency range。设备没有相应 query 时，不能用全局 acquisition
sample rate、频率跨度或点数推导这些字段。

### R1 模型

~~~python
ScopeFftStatusFieldV2 = Literal[
    "source",
    "window",
    "vertical_unit",
    "frequency_start_hz",
    "frequency_stop_hz",
    "average_complete",
    "resolution_bandwidth_hz",
    "sample_rate_hz",
]


@dataclass(frozen=True, slots=True)
class ScopeFftStatusV2:
    math_index: int
    source: str | None = None
    window: str | None = None
    vertical_unit: str | None = None
    frequency_start_hz: float | None = None
    frequency_stop_hz: float | None = None
    average_complete: bool | None = None
    resolution_bandwidth_hz: float | None = None
    sample_rate_hz: float | None = None
    unavailable_fields: tuple[ScopeFftStatusFieldV2, ...] = ()
~~~

~~~python
SCOPE_FFT_STATUS_V2_MAX_QUERIES = 32


@dataclass(frozen=True, slots=True)
class ScopeFftStatusProfileV2:
    readable_fields: tuple[ScopeFftStatusFieldV2, ...]
    max_queries: int
    allowed_effect: Literal["pure_read"] = "pure_read"
~~~

这不是 request variant profile：它只声明同一 descriptor 对已配置 FFT 状态的静态可读字段、文本查询预算和
纯读取效果。它以 append-only 的 `ScopeDescriptorExtensions.fft_status_profile_v2` 追加，并与
`scope.fft_status_v2` 和 `ScopeFftStatusDriverV2.get_fft_status_v2()` 一一对应。

`readable_fields` 非空、唯一，按 `ScopeFftStatusFieldV2` 声明顺序排列；`max_queries` 为 `1..32` 的
非 bool 整数，`allowed_effect` 固定为 `"pure_read"`。未列入 profile 的字段只能为 `None` 并进入
`unavailable_fields`；已列入的字段必须有值且不得进入 `unavailable_fields`。R1 没有 FFT
`not_applicable_fields`：一个字段不是 profile 的静态可读字段，就不能因为某次 query 失败被临时降级。

不变量：

- math index 是正的非 bool 整数；
- 频率、RBW 和 sample rate 非空时必须有限，RBW/sample rate 为正；
- start/stop 同时存在时满足 `start < stop`；
- source/window/unit 使用规范化 safe token；
- 无 query 的字段为 `None` 并进入 unavailable paths；
- 一次 query 或解析失败使 operation 失败；
- 不隐式读取 FFT waveform；
- 不从 `scope.math_metadata` 推导配置状态。

`unavailable_fields` 只接受 `ScopeFftStatusFieldV2`，按定义顺序输出且不重复。每个空字段
必须存在对应路径，每个非空字段不得进入路径集合。frequency start/stop 若只能成对查询，则
必须同时有值或同时 unavailable；一次 query/parse failure 不能转成 unavailable。

R1 capability：

~~~text
scope.fft_status_v2 -> get_fft_status_v2
~~~

## RFC-0007c：带单位的光标读数 V2

### 问题

旧 `ScopeCursorReadout` 只有一个 source，并把水平差固定为秒和倒数赫兹。双 source、
tracking、角度、百分比和 source-defined vertical unit 无法无损映射。

### R1 模型

~~~python
ScopeCursorUnit = Literal["s", "Hz", "degree", "percent", "source"]
ScopeCursorReadoutFieldV2 = Literal[
    "cursor_index",
    "source_a",
    "source_b",
    "x_a",
    "x_b",
    "x_delta",
    "inverse_x_delta",
    "y_a",
    "y_b",
    "y_delta",
]


@dataclass(frozen=True, slots=True)
class ScopeCursorQuantity:
    value: float
    unit: ScopeCursorUnit
    source_unit: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeCursorReadoutV2:
    cursor_index: int | None
    mode: str
    function: str
    source_a: str | None
    source_b: str | None
    x_a: ScopeCursorQuantity | None = None
    x_b: ScopeCursorQuantity | None = None
    x_delta: ScopeCursorQuantity | None = None
    inverse_x_delta: ScopeCursorQuantity | None = None
    y_a: ScopeCursorQuantity | None = None
    y_b: ScopeCursorQuantity | None = None
    y_delta: ScopeCursorQuantity | None = None
    unavailable_fields: tuple[ScopeCursorReadoutFieldV2, ...] = ()
    not_applicable_fields: tuple[ScopeCursorReadoutFieldV2, ...] = ()
~~~

~~~python
ScopeCursorAddressing = Literal["global", "indexed"]


@dataclass(frozen=True, slots=True)
class ScopeCursorReadoutProfileV2:
    readable_fields: tuple[ScopeCursorReadoutFieldV2, ...]
    conditionally_applicable_fields: tuple[ScopeCursorReadoutFieldV2, ...]
    addressing: ScopeCursorAddressing
    max_queries: int
    allowed_effect: Literal["pure_read"] = "pure_read"
~~~

`ScopeCursorQuantity` 不变量：

- value 为有限数值；
- 只有 `unit="source"` 时允许非空 `source_unit`；
- 非 source unit 时 `source_unit is None`；
- source unit 是规范化、长度受限的可见单位，不包含原始命令或资源信息。

readout 不变量：

- A/B source 分别保存，不拼接成一个字符串；
- 不同 source 不得只保留其中一个；
- 缺少某个 quantity 时为 `None`；
- Hz、degree 和 percent 不能放入旧 `x_delta_s`；
- mode/function/source query 失败时 operation 失败；
- mode/function 必须是非空、规范化且长度受限的 safe token；它们不是 availability 字段；
- cursor index 为 `None` 只表示设备采用全局 cursor，不伪造公共 index；它不能表示未知 index、
  query 失败或解析失败；
- `source_a` 和 `source_b` 必须同时有值或同时为 `None`，不得只保留其中一个；两者为 `None` 时也必须
  使用同一 availability 分类。

`unavailable_fields` 表示设备没有可证明的 query；`not_applicable_fields` 表示字段在当前
已查询 mode/function 下没有语义。两组路径来自包含 cursor index、A/B source 和各 quantity
名称的封闭集合，分别排序、去重、互斥。每个值为 `None` 的字段必须恰好由其中一组解释，
非空字段不得出现在任一组。query/parse failure 不能进入这两组。

`ScopeCursorReadoutProfileV2` 是 descriptor append-only 的
`ScopeDescriptorExtensions.cursor_readout_profile_v2`。它不是请求 variant profile：它只约束该设备的
寻址方式、静态可读字段、当前 mode/function 可条件适用字段和纯文本 query 预算。

- `readable_fields` 非空、唯一，并按 `ScopeCursorReadoutFieldV2` 声明顺序排列；至少包含一个 quantity
  字段。`source_a` 和 `source_b` 必须同时出现或同时不出现；
- `conditionally_applicable_fields` 唯一、按同一顺序排列，是 `readable_fields` 的子集；source A/B
  也必须同时出现或同时不出现；
- `addressing="global"` 时，request 必须为 `cursor_index=None`，result 也必须为 `None`，并且
  `cursor_index` 必须进入 `not_applicable_fields`；它不得进入 profile 的 readable/conditional 字段；
- `addressing="indexed"` 时，request 必须是正的非 bool 整数，result 必须精确回显该 index，且
  `cursor_index` 必须是 readable、非 conditional 字段；
- 除上述 global index 特例外，未列入 `readable_fields` 的字段必须为 `None` 且进入
  `unavailable_fields`；列入但不 conditional 的字段必须有值；列入 conditional 的字段必须有值，或仅因本次
  已新鲜查询的 mode/function 没有语义而进入 `not_applicable_fields`。任何 query/parse failure 都不能用
  unavailable/not-applicable 隐藏；
- `max_queries` 必须是 `1..32` 的非 bool 整数，`allowed_effect` 固定为 `"pure_read"`。

`configured_cursor is True` 是调用方的明确意图确认；`False` 或 truthy 非 bool 必须在打开 session／发送任何
I/O 前拒绝。driver 仍须在同一预算内新鲜证明目标寻址、当前配置、mode/function/source 和读取期间稳定性；
未配置、地址错配、状态模糊或漂移是 operation failure，而不是 availability。计数窗口覆盖一次
`get_cursor_readout_v2()` 从进入至返回/抛出期间全部受 guard 计数的文本 `query()`，包括寻址、当前 cursor
配置、mode/function、A/B source、适用性和 quantity。该 phase 只允许 `query()`；禁止 write、binary query、
`query_float_list()`、legacy `idn()`、`*STB?`、`*ESR?`、error drain、math metadata、legacy cursor 和任何
额外 Service preflight。超额、重放或续读均为 failure。

R1 capability：

~~~text
scope.cursor_readout_v2 -> get_cursor_readout_v2
~~~

## Protocol 与 operation

三项分别使用独立 Protocol：

~~~python
class ScopeMeasurementStatisticsDriverV2(Protocol):
    def get_measurement_statistics_v2(
        self,
        request: ScopeMeasurementStatisticsRequestV2,
    ) -> ScopeMeasurementStatisticsV2: ...


class ScopeFftStatusDriverV2(Protocol):
    def get_fft_status_v2(
        self,
        math_index: int,
        *,
        configured_fft: bool,
    ) -> ScopeFftStatusV2: ...


class ScopeCursorReadoutDriverV2(Protocol):
    def get_cursor_readout_v2(
        self,
        cursor_index: int | None,
        *,
        configured_cursor: bool,
    ) -> ScopeCursorReadoutV2: ...
~~~

三个 operation 都是独立的 `stateful_read / exclusive`，不形成 `scope.analysis_v2` 这样的捆绑能力。
0007a 和 0007b R1 已分别注册 capability、OperationSpec 和 Service；0007c R1 已接受同样的核心实现边界：
60 秒 deadline、`stateful_read / exclusive`、`error_check_minimum="disabled"`、无 required verified fields、
无 restore coverage，以及一次只允许 `query()` 的 profile budget phase。

核心开发线已实现 `scope.measurement_statistics_v2` 的模型、profile、Protocol、factory gate、OperationSpec
和 Service，不新增 `measurement-statistics-v2` CLI、artifact、run plan step 或持久化 JSON schema。已有
`measurement-statistics`、`fft-status` 和 `cursor-readout` 继续只调用 legacy Service；后续 CLI
只有在单项 capability 另行冻结参数、JSON 成功形状和 artifact 版本后才能追加。

`scope.measurement_statistics_v2` 与
`ScopeDescriptorExtensions.measurement_statistics_profile_v2`、
`ScopeMeasurementStatisticsDriverV2.get_measurement_statistics_v2()` 一一对应。它使用独立
portability-V2 `OperationSpec`：60 秒 deadline、`stateful_read / exclusive`、
`error_check_minimum="disabled"`、无 required verified fields、无 restore coverage。Service 只调用 V2
driver 方法一次，先执行 profile 的零 I/O request preflight，再进入只允许 `query()` 的 `max_queries`
phase；不得调用 legacy statistics、legacy identity preflight、error drain 或 R1.3 extension Service。

FFT 和 cursor 首版没有请求 variant profile：它们读取当前已配置状态，是否支持该状态只能在有限状态 query 后判断。
0007b 的静态 FFT status profile 不改变这一点；它不选择 request variant，只约束已经证明为 FFT 的字段闭包和
query budget。statistics、FFT 和 cursor capability 声明都触发 strict construction barrier：factory 返回、
profile/Protocol/capability 校验完成前，
所有仪器 I/O 都以 `factory_construction_pending` 发送前拒绝；required method/profile 缺失时关闭已开的
transport。0007c 采用同一 barrier：`scope.cursor_readout_v2` 与 profile、Protocol 和
`get_cursor_readout_v2()` 一一对应；缺 profile/方法在 factory 后、首次 I/O 前失败并关闭已开的 transport；
方法存在但 capability 未声明不触发 latch。

0007b 使用独立 portability-V2 `OperationSpec`：60 秒 deadline、`stateful_read / exclusive`、
`error_check_minimum="disabled"`、无 required verified fields、无 restore coverage。Service 在 session 外拒绝
非正／bool `math_index` 和 `configured_fft is not True`；后者只是调用意图，driver 必须在一个只允许
`query()` 的 `max_queries` phase 内提供新鲜证据，证明该 index 当前是目标 FFT。非 FFT、未配置、索引不匹配、
状态模糊或读取期间漂移均为 operation failure。

该 phase 计数一次 `get_fft_status_v2()` 从进入到返回／抛出期间的全部受 guard 计数的文本 `query()`，包括
FFT 配置证明和所有可读字段。禁止 write、binary query、`query_float_list()`、FFT waveform、legacy `idn()`、
`*STB?`、`*ESR?`、error drain、math metadata、legacy FFT 或任何额外 Service preflight；超额、重放或续读
均为 failure。Service 只调用 V2 driver 方法一次，不得 fallback 到 legacy `get_fft_status()`。

0007b R1 不新增 `fft-status-v2` CLI、artifact、run-plan step 或持久化 JSON schema。既有
`wavebench scope fft-status`、legacy model、Protocol、capability 和 JSON 继续只走 legacy Service。

核心开发线已实现 `ScopeFftStatusV2`、`ScopeFftStatusProfileV2`、独立 Protocol、
`scope.fft_status_v2`、strict factory construction barrier、portability-V2 `OperationSpec` 和
`ScopeService.fft_status_v2()`。核心离线实现覆盖 model/profile、capability、factory、query budget、
non-query I/O 拒绝、math metadata 隔离和 legacy route；主包内建 descriptor 和外部插件在独立 conformance、
版本下限和硬件证据完成前不得声明 FFT V2。该实现不授权 0007c cursor。

0007a R1 核心离线实现已覆盖 selector/profile 的发送前拒绝、完整结果和 selector echo、buffer result 拒绝、
factory zero-I/O、受预算文本 query、non-query I/O 拒绝和 legacy route。它本身不授权 0007b；后者只由本节
单独接受的 FFT 合同授权。主包内建 descriptor 和外部插件在独立 conformance、版本下限和硬件证据完成前不得
声明 statistics、FFT 或 cursor V2；0007a/0007b/0007c 都不授权其他项的插件 opt-in。

## 模型校验

0007a/0007b/0007c R1 dataclass 必须以 `__post_init__` 实现本 RFC 的不变量：

- selector 精确 XOR，slot/index/count 均拒绝 bool；
- item/source/mode/function/unit 使用长度受限的 safe token；
- sources 和 channel-like 集合非空时唯一；
- 所有统计值、buffer 值、FFT 数值和 cursor quantity 都是有限数；
- waveform count 为非负的非 bool 整数；
- statistics result selector 必须等于请求 selector；R1 的 `buffered_values is None`；
- statistics V2 不允许部分聚合成功：五个数值统计项和 `waveform_count` 缺失、空值或非有限值均为
  operation failure，不进入 unavailable/not-applicable；
- FFT start/stop、RBW/sample rate 和 unavailable paths 相互一致；
- cursor unit/source-unit 配对；
- cursor unavailable/not-applicable 路径封闭、互斥并与 `None` 精确对应。

模型构造失败是参数或 driver contract failure，不得在 Service 中自动修正常量、排序 sources
或删除重复字段。

## 前置条件

- `configured`、`configured_fft` 和 `configured_cursor` 必须分别为真正的 `True`；调用方确认不能替代
  driver 的新鲜状态解析；
- 统计 selector/buffer 的静态不支持由 profile 在打开 session／任何 I/O 前拒绝；
- FFT 或 cursor 的当前配置只有在有限状态 query 后才能识别；0007b/0007c 的 query failure 不得写成
  unavailable，且不支持时在任何 write/binary I/O 前 fail-closed，并保留已经发生的 stateful-read 审计；
- operation 不修改前面板配置来制造一个可读状态；
- 若某个查询本身具有消费或状态副作用，必须在 `OperationSpec` 明确声明，不能作为普通 read
  隐藏。

## 现有窄 cursor 子集

旧 capability 可以继续只支持能够无损映射到 `ScopeCursorReadout` 的子集，例如：

- 固定公共 index；
- manual mode；
- A/B 同源；
- TIME + SEC；
- AMPL + source unit。

其他模式在 legacy 结果构造前拒绝是正确的 fail-closed 行为。V2 发布后，插件可以独立声明
V2 以扩展双源和多单位；不得要求旧 capability 自动升级。

## 与其他分析能力的边界

- `scope.math_metadata` 继续提供 preamble/轴信息，不承载 FFT 配置；
- `scope.reference_metadata` 需要真实 reference 轴、点数和 source 证据；
- `scope.history_timestamps` 需要逐帧时间语义；
- spectrum、FFT phase、reference/history 不因本 RFC 自动开放；
- 本 RFC 不配置 measurement、FFT 或 cursor，只读取已经配置的状态。

## 兼容性

1. 保留旧 statistics、FFT 和 cursor 模型、Protocol、capability 与 CLI。
2. 三项 V2 分别追加，任何一项不依赖另外两项。
3. 旧统计 slot 不适配成 item/source 猜测。
4. 旧 FFT 强制字段不通过默认值填充。
5. 旧 cursor 的成功子集继续返回旧模型。
6. 新 capability 未声明时，旧 driver/fake/内建 descriptor 不需要新方法。
7. 方法存在不产生 capability；缺方法的声明在 factory 后、第一次 I/O 前拒绝。
8. JSON 保留 selector、source、unit 和 null，不回写到含义错误的旧字段。
9. 0007a/0007b/0007c R1 不新增 V2 CLI、artifact 或 run plan step，也不改变旧命令路由。`cursor_readout_v2()`
   仅返回 dataclass；若未来需要 CLI/artifact，必须先通过独立 Accepted 附录冻结 global/index 参数、成功 JSON
   的字段/null/path 顺序和版本化 artifact schema。

## 验收矩阵

### Statistics（0007a R1）

- selector XOR、slot 范围、item/source safe token 和 source unique；
- selector mode、source count 和 buffer profile；
- result selector 回显；
- R1 buffer 拒绝、零 I/O 行为，以及未来 buffer 另行接受门；
- 非 finite 数值、waveform count 与解析失败；
- 旧 slot API 回归。

### FFT（0007b R1）

- optional fields、有限值、start/stop 和 unavailable paths；
- 不从全局 sample rate/span/points 推导；
- profile/readable-fields、configured precondition 与纯文本 query budget；
- math metadata 不触发 FFT status 的隐式成功；
- 旧 FFT API 回归。

### Cursor

- global/indexed profile、正的非 bool index 和 `configured_cursor=True` 的零 I/O preflight；
- A/B 同源与双源、source 成对约束，以及秒、赫兹、角度、百分比和 source/source-unit 配对；
- static unavailable、conditional not-applicable、global index 特例与每个 `None` 的精确 path；
- mode/function/source/quantity 错配、parse failure、超 budget 和 non-query I/O 不得伪装成 availability；
- capability/profile/method 一一对应、strict factory latch、legacy route 完全隔离；
- legacy 窄子集继续成功，无法无损映射的配置继续拒绝；至少两种仪器族或 fixture 覆盖不同 unit/optional 组合。

### 共同

- capability/Protocol/construction barrier 零 I/O；
- query/解析失败不转成 unavailable；
- Service 追加式兼容，且 0007a/0007b R1 不改变 CLI；
- 至少两个不同仪器族或 fixture 证明不同 selector/optional/unit 组合。

## 接受与实施顺序

RFC-0007a/0007b 已完成核心离线实现但尚未发布。RFC-0007c 现以本文件冻结的 model/profile/addressing、
availability、pure-text budget、strict factory barrier、Service 边界和无 CLI/artifact 决定进入 `Accepted`，
授权核心离线实现及上述验收矩阵；它不授权 descriptor opt-in、插件 conformance 分支、版本下限升级或硬件
验收。任何一项通过不改变另两项状态。具体插件只有在对应核心合同正式发布、离线 conformance 完成并获得
设备证据后，才可以声明相应 capability。
