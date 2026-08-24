# RFC-0007：可移植的统计、FFT 与光标读取 V2

> 状态：`Accepted R1（仅 RFC-0007a）`
> 核心基线：现有 statistics、FFT status、math metadata 与 cursor readout
> 范围：三个独立的只读 V2 capability
> 系列总览：[scope 可移植性 RFC 组合说明](WaveBench_scope可移植性RFC组合说明.md)
> 本轮范围：0007a 已冻结 selector/profile、纯文本预算与 Service 边界；0007b/0007c 仍为 Draft，
> 不创建 V2 CLI、artifact、run plan step 或插件 opt-in

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

### 候选模型

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

候选能力：

~~~text
scope.fft_status_v2 -> get_fft_status_v2
~~~

## RFC-0007c：带单位的光标读数 V2

### 问题

旧 `ScopeCursorReadout` 只有一个 source，并把水平差固定为秒和倒数赫兹。双 source、
tracking、角度、百分比和 source-defined vertical unit 无法无损映射。

### 候选模型

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
- cursor index 为 `None` 只表示设备采用全局 cursor，不伪造公共 index。

`unavailable_fields` 表示设备没有可证明的 query；`not_applicable_fields` 表示字段在当前
已查询 mode/function 下没有语义。两组路径来自包含 cursor index、A/B source 和各 quantity
名称的封闭集合，分别排序、去重、互斥。每个值为 `None` 的字段必须恰好由其中一组解释，
非空字段不得出现在任一组。query/parse failure 不能进入这两组。

候选能力：

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
0007a R1 按本节注册 capability、OperationSpec 和 Service；0007b/0007c 仍待各自接受后再实施。

R1 只授权 `scope.measurement_statistics_v2` 的模型、profile、Protocol、factory gate、OperationSpec 和
Service，不新增 `measurement-statistics-v2` CLI、artifact、run plan step 或持久化 JSON schema。已有
`measurement-statistics`、`fft-status` 和 `cursor-readout` 继续只调用 legacy Service；后续 CLI
只有在单项 capability 另行冻结参数、JSON 成功形状和 artifact 版本后才能追加。

`scope.measurement_statistics_v2` 与
`ScopeDescriptorExtensions.measurement_statistics_profile_v2`、
`ScopeMeasurementStatisticsDriverV2.get_measurement_statistics_v2()` 一一对应。它使用独立
portability-V2 `OperationSpec`：60 秒 deadline、`stateful_read / exclusive`、
`error_check_minimum="disabled"`、无 required verified fields、无 restore coverage。Service 只调用 V2
driver 方法一次，先执行 profile 的零 I/O request preflight，再进入只允许 `query()` 的 `max_queries`
phase；不得调用 legacy statistics、legacy identity preflight、error drain 或 R1.3 extension Service。

FFT 和 cursor 首版没有
请求 variant profile：它们读取当前已配置状态，是否支持该状态只能在有限状态 query 后判断。
statistics capability 声明触发 strict construction barrier：factory 返回、profile/Protocol/capability 校验完成前，
所有仪器 I/O 都以 `factory_construction_pending` 发送前拒绝；required method/profile 缺失时关闭已开的
transport。0007b/0007c 仍须在各自 Accepted 合同中独立决定其 factory 语义。

## 模型校验

0007a R1 dataclass 与 0007b/0007c 候选 dataclass 必须以 `__post_init__` 实现本 RFC 的不变量：

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

- `configured`、`configured_fft` 和 `configured_cursor` 必须是真正的 bool；0007a R1 要求
  `configured is True`，调用方确认不能替代 driver 的新鲜状态解析；
- 统计 selector/buffer 的静态不支持由 profile 在打开 session／任何 I/O 前拒绝；
- FFT 或 cursor 的当前配置只有在有限状态 query 后才能识别；不支持时在任何 write/binary I/O
  前 fail-closed，并保留已经发生的 stateful-read 审计；
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
9. 0007a R1 不新增 V2 CLI、artifact 或 run plan step，也不改变旧命令路由；0007b/0007c Draft 亦不新增。

## 验收矩阵

### Statistics（0007a R1）

- selector XOR、slot 范围、item/source safe token 和 source unique；
- selector mode、source count 和 buffer profile；
- result selector 回显；
- R1 buffer 拒绝、零 I/O 行为，以及未来 buffer 另行接受门；
- 非 finite 数值、waveform count 与解析失败；
- 旧 slot API 回归。

### FFT

- optional fields、有限值、start/stop 和 unavailable paths；
- 不从全局 sample rate/span/points 推导；
- configured precondition；
- math metadata 不触发 FFT status 的隐式成功；
- 旧 FFT API 回归。

### Cursor

- A/B 同源与双源；
- 秒、赫兹、角度、百分比和 source unit；
- source/source_unit 配对；
- optional quantity、global cursor 与 invalid index；
- unavailable 与 not-applicable 路径；
- legacy 窄子集继续成功，无法无损映射的配置继续拒绝。

### 共同

- capability/Protocol/construction barrier 零 I/O；
- query/解析失败不转成 unavailable；
- Service 追加式兼容，且 0007a R1 不改变 CLI；
- 至少两个不同仪器族或 fixture 证明不同 selector/optional/unit 组合。

## 接受与实施顺序

RFC-0007a 已进入 `Accepted`，只授权上一节列出的 statistics 核心实现与离线验收。以下仍是
RFC-0007b/0007c 的后续接受门，不授权其代码、descriptor 字段或插件 opt-in：

1. RFC-0007b：冻结 FFT configured precondition、optional-field availability 与 JSON 形状；
2. RFC-0007c：冻结 cursor mode/function/source/unit、unavailable/not-applicable 与 JSON 形状；
3. 每一项单独进入 `Accepted` 后，才分别评审 capability、factory latch、Service、CLI/artifact 和
   两个仪器族或 fixture 的离线验收。

任何一项通过不改变另两项状态。具体插件只有在对应核心合同正式发布、离线 conformance 完成并获得
设备证据后，才可以声明相应 capability。
