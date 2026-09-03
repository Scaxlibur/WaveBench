# WaveBench RF 信号源领域设计（迁移前归档）

> 历史领域设计。当前通用模型见[Capability 模型](../concepts/capability-model.md)和[安全模型](../concepts/safety-model.md)，实际操作见[使用 RF 信号源](../how-to/use-rf-source.md)。型号级事实以仪器插件仓库为准。

## 文档定位

本文定义独立 `rf_source` 领域合同，说明它为什么不能复用普通函数发生器的 `source` 合同，以及 Core 与仪器插件应如何分阶段实现。Core `0.8.25` 开发线已具备 M0 只读、M1 OFF-only CW、M2 端口输出、M3 内部正弦调制及按模式关闭、M3-MO 受限调制输出、M4 Pulse 和 frequency-only Step Sweep 配置，以及受限的 A5 Pulse Output 合同与控制入口；DSG830 已凭 A1／A2／A3／A4／A4-MO／A5 Pulse Output 证据开放 snapshot、OFF-only CW、受 safety 限制的 output、RF-OFF 内部正弦调制及按模式关闭、固定 profile 调制输出、RF-OFF Pulse 配置、保持 Sweep disabled 的 Step Sweep 配置，以及一条后面板「PULSE IN/OUT」输出路径。M3 的 PM production profile 仅为 `1.25 rad`；M3-MO 的 production profile 为 AM `50 %`／`1 kHz`、FM `20 kHz`／`1 kHz`、PM `1.25 rad`／`1 kHz`，最大 `-50 dBm`。离线代码与宽于 production profile 的映射不能替代相应 capability 的实机证据。

阅读顺序如下：

1. 本文界定领域模型、安全规则和 production capability 的证据门槛。
2. [RF 信号源开发里程碑](rf-source-milestones.md) 说明 Core 与 DSG830 插件的交付顺序。
3. [设备抽象层](device-abstraction-pre-migration.md) 和 [多仪器流程设计](multi-instrument-flow-pre-migration.md)说明当前通用分层与 run plan 边界。
4. 面向使用者的配置与操作顺序见 [RF 信号源使用指南](rf-source-guide-pre-migration.md)；当前可执行命令、配置字段和 step kind 仍以 `wavebench --help`、`wavebench run schema`、`wavebench.example.toml` 与参考文档为准。

## 当前状态

| 范围 | 当前状态 | 边界 |
| --- | --- | --- |
| Core `0.8.25` 开发线 | 已实现 `rf_source` kind、append-only descriptor extension、`[rf_source]`、M0 只读路径、M1 OFF-only CW、M2 端口输出、M3 内部正弦 AM／FM／PM 及按模式关闭、M3-MO profile-bound 调制输出、M4 Pulse／frequency-only Step Sweep 配置，以及 A5 Pulse Output 的 Service／CLI／run／artifact。 | production capability 仍由各插件的实机证据逐项决定。 |
| DSG830 包 `0.2.0` | 已迁移为 `kind="rf_source"`，提供 `rf_out` 静态 topology、严格 snapshot parser、`:FREQ`／`:LEV`／`:OUTP`、内部正弦 AM／FM／PM、internal／single Pulse、frequency-only Step Sweep 和 `:PULM:OUT:STAT` 输出映射；A1／A2／A3／A4／A4-MO／A5 Pulse Output 证据已经完成。 | production descriptor 声明 `rf_source.idn`、`rf_source.snapshot`、OFF-only `rf_source.cw_configure`、受 safety 限制的 `rf_source.output`、`rf_source.modulation_configure`、`rf_source.modulation_disable`、AM `50 %`／`1 kHz`、FM `20 kHz`／`1 kHz`、PM `1.25 rad`／`1 kHz`、最大 `-50 dBm` 的 `rf_source.modulated_output_enable`、`rf_source.pulse_configure`、受限 `rf_source.pulse_output` 和保持 Sweep disabled 的 `rf_source.sweep_configure`。PM 的 RF-OFF 配置范围仍限于 `1.25 rad`。 |
| 实机证据 | A1、A2、A3、A4 调制／Pulse／Step Sweep、A4-MO 与一条 A5 Pulse Output 路径已完成。 | A5 只提升已验证的「PULSE IN/OUT」output 方向；不提升 Pulse input、`TRIGGER IN`、trigger、fire、sync／reference、Level Sweep 或 list。 |

普通 `source` 仍是面向函数／任意波形发生器的 Vpp、offset、数字 channel 与波形模型。它不是 RF 领域的兼容别名。

除明确标为「生产只读」「A2 已提升」「A3 已提升」「A4 已提升」或「离线已完成」的内容外，本文中的其它 M4 子项、production 写 capability 与 A5 均为目标合同或证据门。M1 仅在已取得 A3 证据的插件上开放 OFF-only CW；M2 仅在已取得 A2 证据的插件上开放端口级 output；DSG830 的 M3、M4 Pulse 和 M4 Step Sweep 已由 A4 分别提升，但各项均保持其声明的 profile 和输出边界。

## 术语与证据级别

| 术语 | 含义 |
| --- | --- |
| 当前能力 | 已在当前 Core 或当前 production descriptor 中声明，并由程序入口实际消费的能力。 |
| 离线合同 | model、parser、fake transport、SCPI 映射和包装测试的结果；不授权真实仪器写入。 |
| 测试 descriptor | 只在 fake transport 测试中声明后续 capability 的 descriptor，不得随生产包对真实设备开放。 |
| production descriptor | 面向已联网设备的公开 descriptor；只能声明已有对应实机证据的 capability。 |

## 目标

WaveBench 的独立 `rf_source` 仪器域面向以频率、功率等级、RF 输出、调制、脉冲和扫频为主要控制对象的射频信号源，不复用普通函数发生器的 `source`、Vpp、offset、channel、ARB 或波形模型。

RIGOL DSG830 是第一个适配目标和手册验证样本，不是该领域的边界。核心合同不得出现 DSG830 专用 SCPI、固定频率范围、固定功率范围、固定端口名或厂商状态位。设备差异由插件 descriptor、driver 和证据记录承载。

本文覆盖 M0 的当前只读实现、已完成 A1／A2／A3／A4 调制／Pulse／Step Sweep／A4-MO 和 A5 Pulse Output 的提升边界，以及 M1、M3、M3-MO 与 M4 的离线开发和 fake transport 验证边界。DSG830 的 M3、M3-MO 和受限 A5 Pulse Output 已完成 Core 与实机验收并进入 production；其它 A5 接口仍另行处理，离线代码不能替代这些证据。

## 范围与非目标

### 目标交付

- M0 已提供 `rf_source` plugin kind、配置、capability、model、driver Protocol、只读 Service／CLI／doctor、run status 和 artifact namespace。
- M1 已提供 OFF-only CW 的 typed request／result、单次写入、独立 snapshot 回读、CLI、run step 和 artifact；DSG830 已由 A3 将其提升到 production。
- M2 已提供端口级 RF ON/OFF 事务、ON safety preflight、一次性 OFF recovery、CLI、run step 和 artifact；DSG830 的 A2 已将这一 capability 提升到 production。
- 定义多 RF 输出端口的通用模型；首个 DSG830 适配器只声明一个端口。
- 定义 CW 频率／dBm 功率配置、RF 输出控制、AM／FM／PM、profile-bound 调制输出、Pulse、Step Sweep、arm／fire／stop 的标准 operation 合同；M4 当前完成 internal／single Pulse 与保持 Sweep disabled 的 frequency-only Step Sweep 配置子集，A5 当前只完成一个固定后面板 Pulse 输出子集。
- 为每条写路径定义输入校验、RF OFF 配置前置条件、独立回读、状态异常失败关闭、fake transport 故障注入和包装测试要求。

### 明确不做

- 默认测试不访问、查询或写入已联网的真实仪器；实机 I/O 只能在单项 A 级证据流程中执行。
- 不发送 `*RST`、preset、memory、IQ、correction、任意波、list 上传或仪器文件系统命令。
- 不将 `dBm` 换算为 Vpp，也不从连接器铭文、仪器显示或型号名推断实际端接。
- 不将设备专用 ALC、衰减器、参考时钟、同步、外部触发或保护复位抽象为未定义的通用字段。
- 不将未完成实机验收的 snapshot 或写 driver 方法暴露为 production descriptor capability；A2 只授权已验收插件的 `rf_source.output`，A3 只授权已验收插件的 `rf_source.cw_configure`，A4 只授权已验收范围内的 RF-OFF 调制、调制关闭、Pulse 或 Step Sweep 配置 capability，A4-MO 只授权其精确声明的调制输出 profile，A5 只授权逐条确认的物理接口、方向和电气 profile。

## 分层与职责

| 层 | 职责 | 不承担的职责 |
| --- | --- | --- |
| 核心 `rf_source` 域 | 公共 model、capability、访问控制、资源租约、session health、安全预检、Service、CLI、run plan 与 artifact | 厂商 SCPI、厂商响应解析、设备功能猜测 |
| 插件 descriptor | 稳定 driver ID、输出端口拓扑、支持功能、有效范围、功率参考与证据引用 | 建立连接、扫描资源、隐式授权 |
| 插件 driver | SCPI 命令、响应解析、写后设备 readback、私有状态映射、`close()` | 读取完整配置、另建 transport、直接写 run artifact、重试能量操作 |
| 实验室配置 | 当前 resource、访问模式、端口安全限制、实际端接声明 | 替代设备能力或实机证据 |

核心复用既有 registry、factory、`DriverContext`、`GuardedAuditedTransport`、资源租约、access policy 与 session health。它不复用 `SourceDriver`、`SourceStatus`、`SourceService`、Source V2 extension、Vpp safety limit 或普通 source restore。

## 通用对象模型

### 输出端口与拓扑

RF 输出端口使用 descriptor 声明的稳定 `port_id`，而不是数字 channel。例如，一个单端口设备可声明 `"rf_out"`；多端口设备可声明多个不同端口。`port_id` 只在同一 driver ID 内稳定，不承担物理接头型号、实际端接或资源地址语义。

```python
@dataclass(frozen=True, slots=True)
class RfOutputPortProfile:
    port_id: str
    frequency_min_hz: float
    frequency_max_hz: float
    power_min_dbm: float
    power_max_dbm: float
    power_reference_impedance_ohm: float


@dataclass(frozen=True, slots=True)
class RfSourceTopology:
    ports: tuple[RfOutputPortProfile, ...]
```

所有范围端点必须有限，最小值不得大于最大值；端口 ID 必须非空、排序稳定且不重复。`power_reference_impedance_ohm` 表示设备用来定义 dBm 的固定参考阻抗，不等于 DUT 的实际端接。

### 可观测状态

不同设备对状态的读取能力不同。公共 model 使用独立的 `RfObserved[T]` 表示值及其可用性：

```python
from typing import Generic, TypeVar


T = TypeVar("T")


class RfAvailability(StrEnum):
    VALUE = "value"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RfObserved(Generic[T]):
    availability: RfAvailability
    value: T | None = None
    reason_code: RfReasonCode | None = None
```

`VALUE` 必须携带通过类型与有限值校验的值；其它可用性必须使用 `value=None`。安全相关字段不是 `VALUE` 时，所有依赖该字段的能量增加 operation 必须在写入前拒绝。

`reason_code` 只用于稳定、脱敏的原因标识，不能保存原始 SCPI 响应或厂商私有错误文本。M0 必须通过边界测试冻结 `VALUE` 的有限数／布尔值校验，以及非 `VALUE` 情况下 `value` 与 `reason_code` 的组合规则。

```python
@dataclass(frozen=True, slots=True)
class RfPortSnapshot:
    port_id: str
    frequency_hz: RfObserved[float]
    power_dbm: RfObserved[float]
    output_enabled: RfObserved[bool]
    modulation: RfObserved["RfModulationState"]
    pulse: RfObserved["RfPulseState"]
    sweep: RfObserved["RfSweepState"]


@dataclass(frozen=True, slots=True)
class RfSourceSnapshot:
    ports: tuple[RfPortSnapshot, ...]
    protection: RfObserved["RfProtectionStatus"]
```

`RfModulationState`、`RfPulseState` 与 `RfSweepState` 是封闭的类型化状态，不是自由 mapping。某个 feature 的状态模型尚未完成时，driver 必须返回非 `VALUE`，不能用猜测字段填充。M3／M4 在相应 capability 进入 descriptor 前冻结这些状态的枚举、模式和 readback 语义。

`RfProtectionStatus` 只保留规范化的活动 condition。descriptor 对每个已知 condition 声明明确 policy；出现未声明、未知或无法解释的 active code 时，一律阻止 RF ON。artifact 不保存原始状态寄存器、SCPI 响应或厂商私有文本。

```python
@dataclass(frozen=True, slots=True)
class RfProtectionStatus:
    active_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RfProtectionConditionPolicy:
    code: str
    blocks_output_enable: bool
```

### 功能 profile 与 capability

descriptor 在 `rf_source_extensions` 中声明 topology、功能和方向。它必须位于 `InstrumentDescriptor` 的末尾，不能复用或改变既有 `source_extensions` 的布局。核心只接受 `kind="rf_source"` 的 descriptor 提供该字段；目标 operation 缺少所需 capability、profile、driver method 或版本门时，必须在仪器 operation 前拒绝。

```python
class RfFeature(StrEnum):
    CW = "cw"
    OUTPUT = "output"
    MODULATION = "modulation"
    MODULATED_OUTPUT = "modulated_output"
    PULSE = "pulse"
    PULSE_OUTPUT = "pulse_output"
    SWEEP = "sweep"
    TRIGGER = "trigger"


class RfFeatureDirection(StrEnum):
    READ = "read"
    CONFIGURE = "configure"
    ENABLE = "enable"
    DISABLE = "disable"
    ARM = "arm"
    TRIGGER = "trigger"
    FIRE = "fire"
    STOP = "stop"
```

```python
@dataclass(frozen=True, slots=True)
class RfFeatureCapability:
    feature: RfFeature
    directions: tuple[RfFeatureDirection, ...]
    port_ids: tuple[str, ...]
    profile: "RfFeatureProfile"


@dataclass(frozen=True, slots=True)
class RfSourceDescriptorExtensions:
    contract_version: Literal["wavebench.rf_source.v1"]
    topology: RfSourceTopology
    features: tuple[RfFeatureCapability, ...]
    protection_conditions: tuple[RfProtectionConditionPolicy, ...]
```

每个 protection policy 的 `code` 必须非空且唯一。Core 以 policy 集合识别已知 condition；只有 `blocks_output_enable=False` 的已知 active code 可以不阻断 RF ON。不存在 policy 的 active code 必须拒绝 RF ON。

`RfFeatureProfile` 是 `RfCwProfile`、`RfOutputProfile`、`RfModulationProfile`、`RfModulatedOutputProfile`、`RfPulseProfile`、`RfPulseOutputProfile`、`RfSweepProfile` 或 `RfTriggerProfile` 的封闭联合。`RfModulatedOutputProfile` 只列出已逐项证实允许在调制开启时启用 RF 的内部 Sine profile，并且必须是基础调制 profile 的子集，功率上限不得超过端口范围。`RfPulseOutputProfile` 明确一个物理接口 ID、唯一 output 方向、可读回状态、电平、源阻抗和固定的内部 Pulse profile；它不隐含同名接口的 input 方向。`RfTriggerProfile` 只描述可读取的逻辑 Pulse／Sweep trigger configuration 值；它不表示物理 trigger／sync 接口、方向、电平或端接。每种 profile 只描述所属 feature 的模式、方向、可读回字段和数值范围；不能用自由 mapping、SCPI 字符串或厂商回调扩展安全语义。

每个 `RfFeatureCapability` 必须指定 feature、direction、适用端口、静态限制和可读回字段。静态 profile 只能收紧设备支持范围，不能授权未声明的 operation。`rf_source.pulse_trigger` 对应 `PULSE / TRIGGER`；`rf_source.sweep_fire` 对应 `SWEEP / FIRE`；其他 operation 也必须在 M0–M4 的 descriptor validator 中有唯一映射。

Core 在调用目标 driver operation 前校验 request、access、descriptor 静态 schema、capability 名称、profile、版本和配置。factory 返回 driver 后再校验 capability 所需方法；现有 factory 可以在构造 driver 时打开已配置 transport，因此不承诺「方法校验发生在建立连接之前」。descriptor 导入和静态校验不得进行 I/O，且任何 SCPI operation 都不得绕过上述校验。

标准 capability 与 driver 方法如下：

| capability | driver 方法 | 作用 |
| --- | --- | --- |
| `rf_source.idn` | `idn()` | 身份查询 |
| `rf_source.snapshot` | `get_rf_snapshot()` | 只读完整快照 |
| `rf_source.trigger_snapshot` | `get_rf_trigger_snapshot(port_id)` | 只读逻辑 Pulse／Sweep trigger configuration；不表示物理 trigger connector。 |
| `rf_source.cw_configure` | `configure_cw(request)` | 端口频率与 dBm 功率配置 |
| `rf_source.output` | `set_rf_output(request)` | 单端口 RF ON/OFF |
| `rf_source.modulation_configure` | `configure_rf_modulation(request)` | 已声明的 AM／FM／PM 配置 |
| `rf_source.modulation_disable` | `disable_rf_modulation(request)` | 关闭一个已明确识别的调制模式与全局调制开关 |
| `rf_source.modulated_output_enable` | `get_rf_modulation_snapshot(port_id, kind)`、`set_rf_output(request)` | 只在已激活 profile 精确匹配时，单次启用 RF；不配置或关闭调制。 |
| `rf_source.pulse_configure` | `configure_rf_pulse(request)` | 已声明的 Pulse 配置 |
| `rf_source.pulse_output` | `get_rf_pulse_output_snapshot(port_id, interface_id)`、`set_rf_pulse_output(request)` | 已声明的物理 Pulse 输出接口；不控制 RF 输出或接收设备。 |
| `rf_source.pulse_trigger` | `trigger_rf_pulse(request)` | 已声明的 Pulse 触发 |
| `rf_source.sweep_configure` | `configure_rf_sweep(request)` | 已声明的 Sweep 配置 |
| `rf_source.sweep_arm` | `arm_rf_sweep(request)` | 准备 Sweep |
| `rf_source.sweep_fire` | `fire_rf_sweep(request)` | 发起已准备 Sweep |
| `rf_source.sweep_stop` | `stop_rf_sweep(request)` | 停止 Sweep |

所有 request 均显式携带 `port_id`。同一 operation 不可借默认端口、当前前面板选择或 driver 私有缓存猜测目标端口。

`rf-source set-frequency` 与 `rf-source set-power` 是独立的 CLI／run 操作名，但共同使用 `rf_source.cw_configure` capability 和同一类 CW request／postcondition 合同；一个 operation 不能借另一个 operation 的成功隐式取得写入授权。

## 通用写入与安全合同

### 配置模型

`[rf_source]` 是独立配置段。端口安全限制按 descriptor 的 `port_id` 声明：

```toml
[rf_source]
driver = "vendor.model"
resource = "<configured-resource>"
access = "read_only"

[[rf_source.safety.ports]]
port_id = "rf_out"
minimum_frequency_hz = 9000
maximum_frequency_hz = 3000000000
maximum_power_dbm = -20
actual_termination_ohm = 50
```

每个会执行 RF ON、fire 或其它可能增加 RF 端口能量的 operation 都要求目标端口拥有完整安全配置。配置范围只能收紧 descriptor 的设备范围。`actual_termination_ohm` 必须是有限正数，并绑定当前端口；M0–M4 与 M3-MO 仅在它与 descriptor 的 `power_reference_impedance_ohm` 精确相等时允许使用 dBm 输出安全判断，不进行阻抗或电压换算。

### Operation 顺序

CW、调制、Pulse 与 Sweep 配置必须按以下顺序执行：

1. 在目标 operation 的 SCPI I/O 前校验 request、access、capability 与 descriptor profile；只有可能增加端口能量的 operation 才同时校验完整安全配置；
2. 获得独占资源租约，创建一次受管 driver session；
3. 读取 fresh snapshot，确认目标 RF 输出为 OFF，且无与 operation 冲突的活动 feature；
4. 调用一次对应 driver 配置方法；
5. 读取独立 postcondition，逐字段比较请求值、端口状态和隐式变化；
6. 成功后返回类型化结果与脱敏 artifact。

主写开始后遇到结果不明、写后 readback 失败或保护状态变化时，不重试同一写入。M1 CW 与 M3 调制配置不执行 RF OFF recovery，而是将 session 保持在更保守状态；M2 与 M3-MO 的 RF ON 事务只在 session health 允许时最多执行一次目标端口 RF OFF 并独立回读。

### 调制关闭与恢复

`rf_source.modulation_disable` 是一个独立的、按模式寻址的写事务，不是 reset，也不等同于 RF 输出关闭。它只在目标 RF 输出为 OFF、Pulse/Sweep 已关闭、protection 清晰，并且状态明确表明仅请求的 AM、FM 或 PM 模式已启用时才发送关闭写入；随后必须用 RF snapshot 和调制状态回读确认全局调制及所有模式均已关闭。

已一致关闭的状态以零写方式返回。混合模式、状态矛盾、未知状态或写后结果不明都会拒绝或使 session 降为不确定状态，不能改用宽泛关闭命令重试。DSG830 的 A4 调制与 A4-MO 清理证据已经覆盖该事务，因此 production descriptor 声明 `rf_source.modulation_disable`，并提供 `wavebench rf-source modulation disable --port PORT_ID --modulation-kind am|fm|pm` 与 `rf_source.modulation_disable` run step。

RF ON 是独立 operation。其 preflight 必须确认：

- `access = "read_write"`；
- target port 的频率、dBm 功率、输出状态、调制、Pulse、Sweep 和 protection 全部为 `VALUE`；
- frequency 与 power 同时在端口安全配置及 descriptor profile 范围内；
- 实际端接与设备 dBm 参考阻抗相等；
- 没有活动的 blocking protection condition；
- 没有尚未获得专项输出安全规则的调制、Pulse 或 Sweep 状态。

RF OFF 不依赖频率、功率、端接或 protection readback；它仍受 access、session health 和单次写入规则限制。

### M3-MO：受限调制输出

`rf_source.modulated_output_enable` 是与 `rf_source.output` 分开的特殊 capability。它只接受一个精确的、已经激活的内部 Sine AM／FM／PM request：不会配置调制，不会在成功后自动 RF OFF，也不会关闭调制。普通 `rf_source.output` 的 ON preflight 仍要求所有调制模式关闭，不能用这个 capability 放宽其边界。

特殊 operation 在一个独占 session 中依次读取 RF snapshot 和目标完整调制 snapshot，确认目标 RF 为 OFF、调制全局开关与唯一目标模式已开启、source／waveform／数值／内部频率精确匹配、Pulse／Sweep disabled、protection 清晰，以及频率、功率、50 Ω 实际端接和 descriptor 中更窄的调制输出 profile 均满足。随后只发送一次 RF ON，并再次读取两类 snapshot，确认 RF 为 ON 且调制 profile 未改变。

ON 写入、RF readback 或调制 readback 任何一项不确定时，绝不重试 ON；只允许沿用 M2 的一次受 guard RF OFF recovery。恢复后 session 保持不确定，调用方不得假设调制已关闭。`RfModulatedOutputProfile` 必须显式声明可用端口、精确 mode profile 与最大功率，不能从基础 `RfModulationProfile` 或普通 output capability 自动推导。

DSG830 的 A4-MO 受控证据提升三个精确 profile：AM `50 %`／内部 `1 kHz`、FM `20 kHz` 频偏／内部 `1 kHz`、PM `1.25 rad` 相偏／内部 `1 kHz`，最大功率均为 `-50 dBm`。它们必须在普通 M3 配置读回后通过相应 `enable-output-am|fm|pm` 启用；结束时先用普通 `output off`，再用按模式 `modulation disable` 清理。FM／PM 的 WaveBench CH2 分析只记录信号存在和波形质量，不能独立测量频偏或相偏。该提升不修改普通 `rf_source.output` 的调制关闭前置条件。

Sweep arm 是 OFF-only 准备 operation，必须保持目标端口 RF OFF。Sweep fire 与 Pulse trigger 是潜在能量操作。它们必须使用独立、一次性安全决定，不能因先前的 configure、arm 或 output ON 成功而自动获得许可。core 不会隐式打开输出、触发外部端口或开启后面板辅助输出。

## Service、CLI、doctor 与 run plan

### 当前已实现的入口

`RfSourceService` 统一负责 capability、access、资源租约、session health 和类型化 snapshot；driver 只执行已冻结的设备动作，CLI 不直接生成 SCPI。当前入口按能力层次分为三组：

```text
# 生产只读：DSG830 已由 A1 提升
wavebench rf-source idn
wavebench rf-source status
rf_source.status

# A5-0 离线只读：需要声明 rf_source.trigger_snapshot 的非 production descriptor
wavebench rf-source trigger status --port PORT_ID
rf_source.trigger_status

# 生产 M1：仅在已完成 A3 的插件上，且必须同时具备 read_write、CW capability 和 OFF-only preflight
wavebench rf-source set-frequency --port PORT_ID HZ
wavebench rf-source set-power --port PORT_ID DBM
rf_source.set_frequency
rf_source.set_power_dbm

# 生产 M2：仅在已完成 A2 的插件上，且必须同时具备 read_write、output capability 和端口级 safety preflight
wavebench rf-source output --port PORT_ID on|off
rf_source.output_enable
rf_source.output_disable

# M3-MO：只限已完成对应实机证据的 production descriptor
wavebench rf-source modulation enable-output-am ...
wavebench rf-source modulation enable-output-fm ...
wavebench rf-source modulation enable-output-pm ...
rf_source.modulated_output_enable

# A5 Pulse Output：只限声明的物理 output 接口与固定 profile
wavebench rf-source pulse-output --port PORT_ID --interface INTERFACE_ID on|off
rf_source.pulse_output_enable
rf_source.pulse_output_disable
```

`rf-source status` 和 `rf_source.status` 均要求 descriptor 声明 `rf_source.snapshot`；缺少该 capability 时，Core 会在打开 transport 前拒绝请求。`rf-source trigger status` 和 `rf_source.trigger_status` 要求独立的 `rf_source.trigger_snapshot` capability，以及目标 `port_id` 的 `TRIGGER / READ` profile；它们是 `stateful_read`，不读取普通 RF snapshot、不执行 recovery、不写入或触发。DSG830 当前 production descriptor 未声明该 capability，因此该命令会在打开 session 前拒绝。`doctor` 仅新增 `rf_source` 的 `*IDN?` target；它不读取运行状态、不改变访问模式，也不打开 RF 输出。

M1 的每个 run step 都要求 `port_id` 与一个有限数值；M2 与 A5 Pulse Output 的每个 run step 都要求 `port_id`，其中 A5 还要求显式 `interface_id`，并产生脱敏的 preflight／postcondition snapshot artifact。所有步骤使用独立的 `wavebench.rf_source.operation.v1` artifact namespace。DSG830 已由 A3 声明 `rf_source.cw_configure`、由 A2 声明 `rf_source.output`、由受限 A5 证据声明 `rf_source.pulse_output`；M1 仅在 `read_write`、目标端口明确 OFF 与完整 OFF-only preflight 同时成立时可执行，M2 还要求完整端口 safety 配置和 fresh preflight。

### M1 的生产 CW 与 M2 的生产输出合同

M1 是 OFF-only CW 配置：目标端口必须明确为 OFF，调制、Pulse、Sweep 与 protection 不得冲突；每次调用只写一个频率或 dBm 字段，并用独立 snapshot 回读确认。写后结果不明时不重试。

M2 是端口级输出事务。RF ON 必须确认完整 safety 配置、实际端接与 dBm 参考阻抗一致、频率与 dBm 功率处于设备和实验室配置范围内、调制／Pulse／Sweep 都关闭，并且 protection 仅含已知的非阻断状态。RF OFF 只依赖目标输出状态，不要求频率、功率、端接或 protection 可读。ON 写入或其 readback 结果不明时，session 降为不确定状态，并且只在受 guard 的 recovery 预算内最多执行一次同端口 OFF 和 OFF 回读；OFF 写入结果不明时不重试，session 降为 poisoned。

M1 已由 A3 在真实设备上完成受控频率／功率写入、独立 readback、低功率 RF ON/OFF 环回与最终 OFF 验收，因而将 `rf_source.cw_configure` 纳入 DSG830 production descriptor。M2 已由 A2 将 `rf_source.output` 纳入同一 descriptor；人工确认的实验室端接本身仍不构成调制、Pulse、Sweep、trigger 或其它额外写入授权。

### M3、M3-MO 与 M4 的 production 入口

M3 的写入 CLI 和 run step 已进入当前 Core schema。DSG830 已由 A4 声明 `rf_source.modulation_configure`，但真实仪器使用仍由 production descriptor、`read_write`、匹配 profile 与 fresh RF-OFF preflight 共同门禁：

```text
wavebench rf-source modulation configure-am ...
wavebench rf-source modulation configure-fm ...
wavebench rf-source modulation configure-pm ...
rf_source.modulation_configure

# M3-MO：仅接受 descriptor 明确声明的 active profile
wavebench rf-source modulation enable-output-am ...
wavebench rf-source modulation enable-output-fm ...
wavebench rf-source modulation enable-output-pm ...
rf_source.modulated_output_enable

# M4 Pulse：只允许 internal／single 配置，配置后 Pulse 仍保持关闭
wavebench rf-source pulse configure --port PORT_ID --period-s SECONDS --width-s SECONDS --polarity normal|inverted
rf_source.pulse_configure

# A5 Pulse Output：只切换已声明的物理 output 状态，不启用 RF 输出
wavebench rf-source pulse-output --port PORT_ID --interface INTERFACE_ID on|off
rf_source.pulse_output_enable
rf_source.pulse_output_disable

# Pulse trigger、Sweep arm／fire／stop 与 Level Sweep 仍是目标合同，尚未进入当前 Core schema
wavebench rf-source pulse trigger ...
wavebench rf-source sweep arm ...
wavebench rf-source sweep fire ...
wavebench rf-source sweep stop ...

# M4 Step Sweep：仅配置 frequency-only、forward、linear、ramp profile，配置后 Sweep 仍保持关闭
wavebench rf-source sweep configure --port PORT_ID --start-frequency-hz START --stop-frequency-hz STOP --points COUNT --dwell-s SECONDS
rf_source.sweep_configure
```

M3 使用 `modulation_kind = "am" | "fm" | "pm"`，并且只接受与该模式匹配的 `depth_percent`、`frequency_deviation_hz` 或 `phase_deviation_rad` 之一。它要求 RF OFF、所有调制模式 disabled、Pulse／Sweep disabled 和无活动 protection condition。DSG830 production profile 为 AM `0–100 %`、FM `0.1 Hz–1 MHz`、PM 精确 `1.25 rad`，内部频率均为 `10 Hz–100 kHz`。FM／PM 的共享选择位作为调制 snapshot 的独立字段记录：preflight 可接受另一种已关闭的 FM／PM 选择，固定 driver 写入会明确选择目标类型，postcondition 则必须确认目标类型。随后以调制 snapshot 独立验证目标模式、内部 source、Sine waveform、数值、内部频率和全局状态。结果不明时不重试，且不会隐式执行 RF OFF recovery。M2 的 RF ON 仍要求调制关闭，因此这不是调制输出入口。

M3-MO 复用同一 `modulation_kind` 和数值字段，但语义相反：它要求调制已经激活且完整 profile 精确匹配，不会配置该 profile。当前 Core schema 已有三个 `enable-output-*` CLI、`rf_source.modulated_output_enable` run step，以及显式 `rf_source.modulation_disable` 清理入口。DSG830 production descriptor 声明 AM `50 %`／`1 kHz`、FM `20 kHz`／`1 kHz`、PM `1.25 rad`／`1 kHz`、最大 `-50 dBm` 的 M3-MO profile；其它 special output 请求会在仪器 I/O 前拒绝。A4-MO 的三条固定 profile 均使用 RF `1 MHz`／`-50 dBm` 和 CH2 的显式 50 Ω 观察完成受控验收。FM／PM 额外保存 WaveBench 波形摘要和 FFT 质量记录；它们不读取或控制 CH1，不把 LF OUTPUT 当作调制测量，也不从 scope 推断 dBm、频偏、相偏、调制准确度或频谱合规性。

`rf_source.pulse_configure` 已进入当前 Core schema；DSG830 已在 A4 Pulse 证据复核后声明该 capability。它只接受 period、width 和 polarity，要求 RF 输出、调制、Pulse、Sweep 均关闭且无活动 protection；写入后必须读回 internal／single、请求的 timing／polarity 和 Pulse 关闭状态。它不提供 trigger、后面板 Pulse I/O 或 RF 输出控制。

`rf_source.pulse_output` 是与 `rf_source.pulse_configure` 分开的受限物理接口 operation。DSG830 仅声明 `rf_out` 上的 `pulse_in_out`，方向固定为 output，电气 profile 固定为 `0 V`／`3.3 V`、约 `600 Ω`，Pulse profile 固定为 internal／single／normal／`1 ms`／`100 μs` 且 Pulse 调制保持关闭。启用前，Core 要求 RF 输出、调制、Pulse、Sweep 均关闭、protection 为空、接口与完整固定 profile 精确匹配；随后只执行一次 Pulse Output 状态写入，并分别回读 RF 与物理接口状态。关闭操作故意允许已知 profile 漂移，以保留关闭已启用输出的安全路径；它仍只操作该接口。任何写入或 readback 结果不明都会使 session 降为不确定状态，Core 不自动重试，也不配置接收设备、RF 输出、trigger 或示波器。

`rf_source.sweep_configure` 已进入当前 Core schema；DSG830 已在 A4 Step Sweep 证据复核后声明该 capability。请求只接受起止频率、点数和驻留时间，静态 profile 固定为 `STEP`／`FWD`／`RAMP`／`LIN`。Core 在写前和写后都要求 RF 输出、调制、Pulse、Sweep 关闭且无活动 protection；driver 配置后必须保持 Sweep disabled，并以独立 profile readback 逐字段确认。该 operation 没有 Level Sweep、arm、fire、`SWE:EXEC`、trigger、后面板接口或 RF 输出字段。Pulse trigger、Sweep arm／fire／stop 仍是目标合同。所有这些入口都必须显式指定 `port_id`，拥有独立 `OperationSpec` 与 `wavebench.rf_source.operation.v1` artifact，不得访问普通 source channel 或未声明端口。

### A5：物理接口按路径提升

外部 trigger／同步不能只在 `RfFeatureDirection.TRIGGER`、`FIRE` 或 `ARM` 已存在的前提下补充 driver 方法。每条路径都必须先定义目标物理接口、方向、电气 profile、允许的模式、可读回状态、RF 能量前置条件和失败恢复语义；这些字段不能用自由 mapping 或普通 `source` 的 channel／Vpp 模型表示。

#### A5-0：逻辑 trigger configuration 读取

A5-0 已在离线范围内增加 `RfTriggerProfile`、`RfTriggerSnapshot`、`rf_source.trigger_snapshot`、`rf-source trigger status` 与 `rf_source.trigger_status`；它固定读取逻辑 Pulse／Sweep trigger configuration，不把 `port_id` 解释为物理 trigger／sync connector。DSG830 production descriptor 仍不声明该 capability。源码 checkout 的私有 A5-0 harness 保持原始 `read_only` 配置，并已完成隔离零写诊断：22 次 query、零 write、最终 RF OFF 和健康关闭均已复核。静态预检绑定当前 production descriptor 的 capability 列表；后续 capability 变更必须通过代码审查、fake 回归和新的零写诊断更新该基线，否则工具在建立 session 前拒绝。该诊断不构成物理 A5 实机证据，也不创建外部接口默认值、不隐式触发设备，或把 `rf_out` 的端接和 CH2 输入设置用于推断 trigger／sync 端口。

#### A5 Pulse Output：已完成的单一路径

已验证的唯一路径是 DSG830「PULSE IN/OUT」的 output 方向接入 RTM2032「EXT TRIGGER INPUT」。DSG830 侧 profile 固定为 `0 V`／`3.3 V`、约 `600 Ω`，internal／single／normal、period `1 ms`、width `100 μs`；接收端仅以其 `1 MΩ`／`12 pF`／`≤ 150 Vp` 输入额定值作为本次接线的电气边界。验收 harness 在隔离 session 中先核对 scope trigger source 为 external、mode 为 auto，再短暂设为 normal、执行 single、复位为 auto；scope 只作为隔离的物理观察与单次采集执行者，不构成 RTM driver 或 production capability。

该路径在 RF 输出始终关闭的前提下完成一次「Pulse Output ON → scope single → Pulse Output OFF」序列。成功审计为 RF 主 session `97` 次 query／`8` 次完成 write、独立最终 RF 复核 session `15` 次 query／零 write、scope session `5` 次 query／`3` 次完成 write；最终 RF 输出和 Pulse Output 均独立确认关闭。source 的既有 Pulse profile 与 scope acquisition state 不属于恢复范围，scope 在完成后可能停在 `Single`。历史 harness 在 capability 提升后拒绝重跑，避免以临时 descriptor 绕过 production 边界。

因此 DSG830 production descriptor 只增加 `rf_source.pulse_output`。它不提升同一连接器的 input 方向、`TRIGGER IN`、Pulse trigger、Sweep arm／fire、sync／reference、Level Sweep 或 list；任何会使设备开始 Pulse 或 Sweep 的 trigger／fire operation 仍需独立、一次性的安全决定和另一条 A5 实机证据。

## M0–M4 与 M3-MO 里程碑

下表同时标出当前进度和交付边界。Core 与 DSG830 插件的依赖、完成条件和状态见[RF 信号源开发里程碑](rf-source-milestones.md)。

| 里程碑 | 通用核心交付 | 首个适配器离线交付 | 离线验证标准 |
| --- | --- | --- | --- |
| M0（生产只读） | `rf_source` kind、config、拓扑/profile、可观测 snapshot、Protocol、registry、doctor、只读 CLI 与 run status | 严格 snapshot parser；A1 后 production descriptor 声明只读 snapshot | descriptor 无 I/O；每个状态 query、解析和坏响应测试通过；A1 证据复核后仅提升 snapshot。 |
| M1（离线完成；DSG830 A3 已提升） | CW request／result、OFF-only transaction、CLI、run step、artifact 与端口范围检查 | 频率／dBm 功率单次写入与独立回读 | output ON、活动调制／Pulse／Sweep 或越界请求时零写拒绝；结果不明无重试；DSG830 仅在 A3 复核后声明 CW。 |
| M2（离线完成；DSG830 A2 已提升） | per-port 输出事务、安全预检、受 guard 的一次性 RF OFF recovery、CLI、run step 与 artifact | RF ON/OFF 单次写入；Core 负责独立 readback | 安全配置缺失、端接不匹配、保护异常或状态缺失时 ON 零写拒绝；ON readback 失败最多一次 OFF；DSG830 仅在 A2 复核后声明 output。 |
| M3（A4 已通过并提升） | 内部正弦 AM／FM／PM profile、typed request/result、调制 snapshot、配置 Service／CLI／run step／artifact；按模式关闭仅用于本地证据与私有恢复 | 内部 Sine 调制序列、严格 readback、单模式 RF-OFF evidence harness 与受限恢复路径 | 输出未 OFF、任一模式已开启、profile 不支持、Pulse／Sweep／protection 冲突或 postcondition 不符时零写拒绝；DSG830 已声明 `rf_source.modulation_configure`，其中 PM 固定为 `1.25 rad`。 |
| M3-MO（A4-MO 已通过并提升） | `RfModulatedOutputProfile`、special capability、严格 pre/post RF 与调制 snapshot、一次 ON、受 guard OFF recovery、CLI、run step 与 artifact | 复用已有调制 snapshot 与 `:OUTP` 映射；历史 AM 与独立 FM／PM evidence descriptor、CH2 observation、WaveBench 波形摘要／FFT 和 fake 回归 | 只在完整 active profile 精确匹配、RF OFF、Pulse／Sweep OFF、protection 清晰和端口 safety 完整时写一次 ON；绝不重试 ON。DSG830 production 声明 AM `50 %`／`1 kHz`、FM `20 kHz`／`1 kHz`、PM `1.25 rad`／`1 kHz`、最大 `-50 dBm`。scope 分析不计量频偏或相偏。 |
| M4（Pulse；DSG830 A4 已提升） | internal／single Pulse profile、typed request/result、OFF-only Service、CLI、run step 与 artifact | period／width／polarity 的固定映射；配置后强制 Pulse OFF | 输出、调制、Pulse、Sweep 或 protection 不满足时零写拒绝；写后逐字段 readback；DSG830 已声明 `rf_source.pulse_configure`。 |
| M4（Step Sweep；DSG830 A4 已提升） | frequency-only Step Sweep profile、configure、CLI、run step 与 artifact；不含 arm／fire／stop | `STEP`／`FWD`／`RAMP`／`LIN` 的严格 readback 与固定配置写入，最后保持 Sweep disabled | 输出、调制、Pulse、Sweep 或 protection 不满足时零写拒绝；不写 `SWE:EXEC`、trigger、Level Sweep 或 RF 输出。DSG830 已声明 `rf_source.sweep_configure`。 |
| A5（Pulse Output；DSG830 已提升） | `RfPulseOutputProfile`、固定物理 output request／result／snapshot、Service、CLI、run step 与 artifact | `pulse_in_out` output 的 `:PULM:OUT:STAT?`／`:PULM:OUT:STAT ON|OFF` 映射 | 仅在 RF 输出、调制、Pulse、Sweep 都关闭、protection 为空、接口和固定 profile 精确匹配时启用；关闭允许 profile 漂移以保留安全关闭路径。仅提升 `rf_source.pulse_output`。 |

M0–M4、M3-MO 与 A5 Pulse Output 的每项提升都绑定代码合同、SCPI 映射和对应实机证据。证据顺序包含：A1 只读 snapshot，A2 RF OFF/ON，A3 CW 环回，A4 调制／Pulse／Sweep，A4-MO 调制输出，以及按物理路径拆分的 A5。每项 evidence 绑定 capability、型号、固件、选件、端口、端接和最终 RF OFF 状态。

DSG830 的 A1 已使 production descriptor 声明 `rf_source.snapshot`，A2 已使其声明 `rf_source.output`，A3 已使其声明 `rf_source.cw_configure`，A4 已分别使其声明 `rf_source.modulation_configure`、`rf_source.modulation_disable`、`rf_source.pulse_configure` 和受限的 `rf_source.sweep_configure`。调制证据覆盖 AM／FM／PM 的 RF-OFF 单模式配置、严格读回与关闭恢复；PM 的 production profile 固定为 `1.25 rad`。A4-MO 已在 RF `1 MHz`／`-50 dBm`、50 Ω CH2 路径上完成 AM `50 %`／`1 kHz`、FM `20 kHz`／`1 kHz`、PM `1.25 rad`／`1 kHz` 的固定 profile 验收，因此将这三个精确 profile 的 `rf_source.modulated_output_enable` 提升到 production。FM／PM 的 WaveBench 波形摘要与 FFT 只记录质量告警，不外推为偏差计量。受限 A5 已将「PULSE IN/OUT」output 方向的 `rf_source.pulse_output` 提升到 production；剩余 A5 trigger／fire／sync 路径仍需独立实机证据。未取得对应 evidence 时不得声明或提升其它 production descriptor capability。

## 首个适配器：RIGOL DSG830

DSG830 只为通用合同提供第一组设备映射，不改变核心类型或安全规则。DSG800 Programming Guide 中可用于首轮离线实现的事实如下：

| 通用功能 | DSG830 SCPI | DSG830 静态范围或状态 |
| --- | --- | --- |
| 身份 | `*IDN?` | IDN 包含 `DSG830`。 |
| CW 频率 | `:FREQ <Hz>` / `:FREQ?` | `9 kHz–3 GHz`。 |
| CW dBm 功率 | `:LEV <dBm>` / `:LEV?` | `-110 dBm–20 dBm`；query 默认 dBm。 |
| RF 输出 | `:OUTP ON|OFF` / `:OUTP?` | 单个 `rf_out` 端口。 |
| 调制状态 | `:MOD:STAT?` | `0`／`1`；M3 还读取 AM／FM／PM enable 状态与目标内部 Sine 参数。 |
| Pulse 状态 | `:PULM:STAT?` | `0`／`1`；配置和触发进入 M4。 |
| 后面板 Pulse Output | `:PULM:OUT:STAT?` / `:PULM:OUT:STAT ON|OFF` | 仅 `pulse_in_out` 的 output 方向；`0 V`／`3.3 V`、约 `600 Ω`，internal／single／normal／`1 ms`／`100 μs`。 |
| Sweep 状态 | `:SWE:STAT?` | `OFF`、`FREQ`、`LEV` 或组合；frequency-only Step Sweep 子集进入 M4。 |
| 保护状态 | `:STAT:QUES:POW:COND?` | 位 0 ALC unlocked、位 1 output power protection、位 2 heater detector；未知高位按阻断处理。 |

手册未给出可安全采用的 error queue 查询命令。因此 DSG830 不声明 `rf_source.errors`，所有写后判断依赖独立状态回读和 condition register。

DSG830 的 production `descriptor()` 在 A1／A2／A3／A4／A4-MO／A5 Pulse Output 完成后声明 `rf_source.idn`、`rf_source.snapshot`、`rf_source.cw_configure`、`rf_source.output`、`rf_source.modulation_configure`、`rf_source.modulation_disable`、`rf_source.modulated_output_enable`、`rf_source.pulse_configure`、`rf_source.pulse_output` 与 `rf_source.sweep_configure`。`get_rf_snapshot()` 可通过只读入口观察状态，`get_rf_trigger_snapshot()` 仅以固定 query 读取逻辑 trigger configuration，且不进入 production descriptor；`configure_cw()` 只可在目标输出 OFF 的完整 preflight 后写入一个频率或功率字段，`set_rf_output()` 只可在完整 safety preflight 后切换 `rf_out`。M3 覆盖 RF-OFF 的内部正弦 AM／FM／PM 与按模式关闭；PM production profile 固定为 `1.25 rad`。M3-MO 复用 `get_rf_modulation_snapshot()` 和 `set_rf_output()`，但 production 只接受 AM `50 %`／`1 kHz`、FM `20 kHz`／`1 kHz`、PM `1.25 rad`／`1 kHz`、最大 `-50 dBm`；它未改变普通 `rf_source.output` 的调制关闭前置条件。FM／PM 的 scope 分析只记录波形质量，不能独立计量频偏或相偏。M4 Pulse 固定 internal／single、period／width／polarity，并以 `:PULM:STAT OFF` 收尾；两种极性已通过受控实机配置、读回与最终 RF-OFF 验证。A5 Pulse Output 只通过 `get_rf_pulse_output_snapshot()`／`set_rf_pulse_output()` 查询和切换 `pulse_in_out` 的 output 状态，不配置 Pulse profile、RF 输出、trigger 或接收设备。M4 Step Sweep 固定为仅配置的 `STEP`／`FWD`／`RAMP`／`LIN` 映射与严格 readback，并以 `:SWE:STAT OFF` 收尾；它不写 `:SWE:EXEC`、trigger、Level Sweep 或 RF 输出。历史 A4／A4-MO／A5 Pulse Output harness 在对应 descriptor 提升后拒绝重跑；普通 M3／M3-MO／Pulse／Pulse Output／Step Sweep 使用必须经 production descriptor、`read_write` access 与完整 preflight。既有证据不开放 Sweep fire、Pulse input、`TRIGGER IN` 或同步控制。历史 `0.1.0` 的 `source.idn` 种子已迁移为当前 `0.2.0` 的 RF 包。

## 测试与发布边界

- 所有公共 model、profile 与 request 在实现前先有边界、非有限数、布尔值、未知枚举和不匹配端口的失败测试。
- 所有 Service 写路径先有零写拒绝与写后回读失败测试，再实现最小 transaction。
- `StatefulRfTransport` 与 guarded fake transport 模拟严格 query、单次写入、忽略写入、写前异常、写后 query 异常、protection 变化、recovery 预算与 session health。
- driver 测试精确断言 SCPI 命令、值格式、query 顺序、写入次数与 postcondition；不以 fake 的调用次数代替外部可见状态断言。
- 默认测试不扫描端口、不读取本地实验室配置、不连接仪器、不执行真实 SCPI。
- 核心验证包括聚焦 pytest、完整 pytest、ruff 和 `git diff --check`；插件额外包括包级 pytest、ruff、wheel/package check 与安装 dry-run。
- production descriptor 的 capability 提升只接受对应的 A1–A5 证据，不接受「代码已实现」或 fake 测试替代。

## 实施记录与边界

- 核心开发分支：`Scaxlibur/feat/rf-source-core`。
- DSG830 插件开发分支：`Scaxlibur/feat/rf-source-dsg830`。
- Core M0 提交：`8a746fb`、`6fa9c48`、`f3ae6d7`、`55474be`、`e8ff1be`、`cf53e14`；DSG830 M0 提交：`0c5c2bf`。
- M0–M3 离线验证已完成；DSG830 A1 snapshot、A2 受控输出、A3 CW 环回与 A4 调制证据均已通过，production 已提升 snapshot、`rf_source.output`、`rf_source.cw_configure`、`rf_source.modulation_configure` 和 `rf_source.modulation_disable`。Core `5fc0e19` 保留调制 postcondition 的类型化证据，插件 `a7b3b93` 以独立 session 完成失败后的受限恢复，插件 `ebea610` 将已验证的 M3 profile 提升到 production；PM 仅为 `1.25 rad`。Core `ee790dc`／`8210299` 与插件 `e22911f`／`b3fa6c0` 增加 M4 Pulse 离线合同、控制入口和本地证据工具；两种极性通过受控实机验证后，插件 `40564a9` 将 `rf_source.pulse_configure` 加入 production descriptor。Core `d3481d8`／`8ec1733`／`e04ed60` 与插件 `851bdf5` 增加 frequency-only Step Sweep 的离线合同、固定映射、CLI、run 与 artifact；插件 `15c61e1` 增加隔离的零写诊断／受控配置 evidence harness。A4 Step Sweep 的诊断与受控配置实机序列均已通过，后者在独立 profile readback 与最终 OFF 复核后，插件 `9a6e30a` 将 `rf_source.sweep_configure` 加入 production descriptor。Core `f9c46e2`／`6d8d0af`／`3ee2697`／`188292d` 与插件 `5394f15`／`3fb3778`／`65eb611` 新增 M3-MO special capability、严格 transaction、公开调制关闭入口、历史 AM 证据 harness 与 fake 回归。A4-MO 先在 AM `50 %`／`1 kHz`、RF `1 MHz`／`-50 dBm`、CH2 50 Ω 路径上通过；随后独立 FM `20 kHz`／`1 kHz` 与 PM `1.25 rad`／`1 kHz` 受控循环也完成同样的严格源端 readback、CH2 信号存在、最终 RF OFF、调制关闭和健康关闭复核。FM／PM 使用 WaveBench 波形摘要和 FFT 记录质量告警，但不把它们外推为偏差计量；production 已提升三个精确 profile，最大 `-50 dBm`。Core `877645d` 与插件 `1b08593`／`e32e335`／`e9c3502` 增加并完成 A5 Pulse Output 合同、固定 `:PULM:OUT:STAT` 映射、隔离实机验收和 production 提升。该提升不开放 Pulse input、`TRIGGER IN`、Sweep execute／fire、sync 或 trigger capability。
