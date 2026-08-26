# WaveBench RF 信号源领域设计

## 文档定位

本文定义独立 `rf_source` 领域合同，说明它为什么不能复用普通函数发生器的 `source` 合同，以及 Core 与仪器插件应如何分阶段实现。Core `0.8.25` 开发线已具备 M0 只读、M1 OFF-only CW、M2 端口输出、M3 内部正弦调制，以及 M4 Pulse 和 frequency-only Step Sweep 配置的合同与控制入口；DSG830 已凭 A1／A2／A3 和 A4 Pulse 证据开放 snapshot、OFF-only CW、受 safety 限制的 output 与 RF-OFF Pulse 配置。Step Sweep 仅完成离线实现，M3 仍须通过覆盖 PM 的实机证据门，二者都不能由离线代码替代。

阅读顺序如下：

1. 本文界定领域模型、安全规则和 production capability 的证据门槛。
2. [RF 信号源开发里程碑](WaveBench_RF信号源开发里程碑.md) 说明 Core 与 DSG830 插件的交付顺序。
3. [设备抽象层](WaveBench_设备抽象层.md) 和 [多仪器流程设计](WaveBench_多仪器协同流程设计.md)说明当前通用分层与 run plan 边界。
4. 面向使用者的配置与操作顺序见 [RF 信号源使用指南](../guides/WaveBench_RF信号源使用指南.md)；当前可执行命令、配置字段和 step kind 仍以 `wavebench --help`、`wavebench run schema`、`wavebench.example.toml` 与参考文档为准。

## 当前状态

| 范围 | 当前状态 | 边界 |
| --- | --- | --- |
| Core `0.8.25` 开发线 | 已实现 `rf_source` kind、append-only descriptor extension、`[rf_source]`、M0 只读路径、M1 OFF-only CW、M2 端口输出、M3 内部正弦 AM／FM／PM、仅用于受控恢复的调制关闭事务，以及 M4 Pulse／frequency-only Step Sweep 配置的 Service／CLI／run／artifact。 | production capability 仍由各插件的实机证据逐项决定。 |
| DSG830 包 `0.2.0` | 已迁移为 `kind="rf_source"`，提供 `rf_out` 静态 topology、严格 snapshot parser、`:FREQ`／`:LEV`／`:OUTP`、内部正弦 AM／FM／PM、internal／single Pulse 与 frequency-only Step Sweep 配置映射；A1／A2／A3／A4 Pulse 证据已经完成。 | production descriptor 声明 `rf_source.idn`、`rf_source.snapshot`、OFF-only `rf_source.cw_configure`、受 safety 限制的 `rf_source.output` 和 RF-OFF `rf_source.pulse_configure`；M3 与 Step Sweep 写 capability 仍关闭。 |
| 实机证据 | A1、A2、A3 和 A4 Pulse 已完成；A4 的 AM、FM RF-OFF 序列已通过，PM 仍有严格读回不匹配；Step Sweep 尚无专项证据，A5 未开始。 | M3 capability 覆盖三种模式；DSG830 production descriptor 已开放 RF-OFF Pulse 配置，Step Sweep 仍等待专项证据。 |

普通 `source` 仍是面向函数／任意波形发生器的 Vpp、offset、数字 channel 与波形模型。它不是 RF 领域的兼容别名。

除明确标为「生产只读」「A2 已提升」「A3 已提升」「A4 Pulse 已提升」或「离线已完成」的内容外，本文中的其它 M4 子项、production 写 capability 与 A4–A5 均为目标合同或证据门。M1 仅在已取得 A3 证据的插件上开放 OFF-only CW；M2 仅在已取得 A2 证据的插件上开放端口级 output；M4 Pulse 已由 A4 提升，M3 仍未覆盖 PM。

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

本文覆盖 M0 的当前只读实现、已完成 A1／A2／A3 的提升边界，以及 M1、M3–M4 的离线开发和 fake transport 验证边界。M3 已完成 Core 与 DSG830 的离线实现；A4–A5 实机验收、其它 production 写 capability 声明和发行包推广仍另行处理，离线代码不能替代这些证据。

## 范围与非目标

### 目标交付

- M0 已提供 `rf_source` plugin kind、配置、capability、model、driver Protocol、只读 Service／CLI／doctor、run status 和 artifact namespace。
- M1 已提供 OFF-only CW 的 typed request／result、单次写入、独立 snapshot 回读、CLI、run step 和 artifact；DSG830 已由 A3 将其提升到 production。
- M2 已提供端口级 RF ON/OFF 事务、ON safety preflight、一次性 OFF recovery、CLI、run step 和 artifact；DSG830 的 A2 已将这一 capability 提升到 production。
- 定义多 RF 输出端口的通用模型；首个 DSG830 适配器只声明一个端口。
- 定义 CW 频率／dBm 功率配置、RF 输出控制、AM／FM／PM、Pulse、Step Sweep、arm／fire／stop 的标准 operation 合同；M4 当前完成 internal／single Pulse 与保持 Sweep disabled 的 frequency-only Step Sweep 配置子集。
- 为每条写路径定义输入校验、RF OFF 配置前置条件、独立回读、状态异常失败关闭、fake transport 故障注入和包装测试要求。

### 明确不做

- 默认测试不访问、查询或写入已联网的真实仪器；实机 I/O 只能在单项 A 级证据流程中执行。
- 不发送 `*RST`、preset、memory、IQ、correction、任意波、list 上传或仪器文件系统命令。
- 不将 `dBm` 换算为 Vpp，也不从连接器铭文、仪器显示或型号名推断实际端接。
- 不将设备专用 ALC、衰减器、参考时钟、同步、外部触发或保护复位抽象为未定义的通用字段。
- 不将未完成实机验收的 snapshot 或写 driver 方法暴露为 production descriptor capability；A2 只授权已验收插件的 `rf_source.output`，A3 只授权已验收插件的 `rf_source.cw_configure`。

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
    PULSE = "pulse"
    SWEEP = "sweep"


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

`RfFeatureProfile` 是 `RfCwProfile`、`RfOutputProfile`、`RfModulationProfile`、`RfPulseProfile` 或 `RfSweepProfile` 的封闭联合。每种 profile 只描述所属 feature 的模式、方向、可读回字段和数值范围；不能用自由 mapping、SCPI 字符串或厂商回调扩展安全语义。

每个 `RfFeatureCapability` 必须指定 feature、direction、适用端口、静态限制和可读回字段。静态 profile 只能收紧设备支持范围，不能授权未声明的 operation。`rf_source.pulse_trigger` 对应 `PULSE / TRIGGER`；`rf_source.sweep_fire` 对应 `SWEEP / FIRE`；其他 operation 也必须在 M0–M4 的 descriptor validator 中有唯一映射。

Core 在调用目标 driver operation 前校验 request、access、descriptor 静态 schema、capability 名称、profile、版本和配置。factory 返回 driver 后再校验 capability 所需方法；现有 factory 可以在构造 driver 时打开已配置 transport，因此不承诺「方法校验发生在建立连接之前」。descriptor 导入和静态校验不得进行 I/O，且任何 SCPI operation 都不得绕过上述校验。

标准 capability 与 driver 方法如下：

| capability | driver 方法 | 作用 |
| --- | --- | --- |
| `rf_source.idn` | `idn()` | 身份查询 |
| `rf_source.snapshot` | `get_rf_snapshot()` | 只读完整快照 |
| `rf_source.cw_configure` | `configure_cw(request)` | 端口频率与 dBm 功率配置 |
| `rf_source.output` | `set_rf_output(request)` | 单端口 RF ON/OFF |
| `rf_source.modulation_configure` | `configure_rf_modulation(request)` | 已声明的 AM／FM／PM 配置 |
| `rf_source.modulation_disable` | `disable_rf_modulation(request)` | 关闭一个已明确识别的调制模式与全局调制开关 |
| `rf_source.pulse_configure` | `configure_rf_pulse(request)` | 已声明的 Pulse 配置 |
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

每个会执行 RF ON、fire 或其它可能增加 RF 端口能量的 operation 都要求目标端口拥有完整安全配置。配置范围只能收紧 descriptor 的设备范围。`actual_termination_ohm` 必须是有限正数，并绑定当前端口；M0–M4 仅在它与 descriptor 的 `power_reference_impedance_ohm` 精确相等时允许使用 dBm 输出安全判断，不进行阻抗或电压换算。

### Operation 顺序

CW、调制、Pulse 与 Sweep 配置必须按以下顺序执行：

1. 在目标 operation 的 SCPI I/O 前校验 request、access、capability 与 descriptor profile；只有可能增加端口能量的 operation 才同时校验完整安全配置；
2. 获得独占资源租约，创建一次受管 driver session；
3. 读取 fresh snapshot，确认目标 RF 输出为 OFF，且无与 operation 冲突的活动 feature；
4. 调用一次对应 driver 配置方法；
5. 读取独立 postcondition，逐字段比较请求值、端口状态和隐式变化；
6. 成功后返回类型化结果与脱敏 artifact。

主写开始后遇到结果不明、写后 readback 失败或保护状态变化时，不重试同一写入。M1 CW 与 M3 调制配置不执行 RF OFF recovery，而是将 session 保持在更保守状态；只有 M2 的 RF ON 事务可在 session health 允许时最多执行一次目标端口 RF OFF 并独立回读。

### 调制关闭与恢复

`rf_source.modulation_disable` 是一个独立的、按模式寻址的写事务，不是 reset，也不等同于 RF 输出关闭。它只在目标 RF 输出为 OFF、Pulse/Sweep 已关闭、protection 清晰，并且状态明确表明仅请求的 AM、FM 或 PM 模式已启用时才发送关闭写入；随后必须用 RF snapshot 和调制状态回读确认全局调制及所有模式均已关闭。

已一致关闭的状态以零写方式返回。混合模式、状态矛盾、未知状态或写后结果不明都会拒绝或使 session 降为不确定状态，不能改用宽泛关闭命令重试。该 operation 当前用于受控本地证据和恢复流程；生产 DSG830 descriptor 不因此新增 capability，也没有面向日常使用的 CLI 或 run step。

RF ON 是独立 operation。其 preflight 必须确认：

- `access = "read_write"`；
- target port 的频率、dBm 功率、输出状态、调制、Pulse、Sweep 和 protection 全部为 `VALUE`；
- frequency 与 power 同时在端口安全配置及 descriptor profile 范围内；
- 实际端接与设备 dBm 参考阻抗相等；
- 没有活动的 blocking protection condition；
- 没有尚未获得专项输出安全规则的调制、Pulse 或 Sweep 状态。

RF OFF 不依赖频率、功率、端接或 protection readback；它仍受 access、session health 和单次写入规则限制。

Sweep arm 是 OFF-only 准备 operation，必须保持目标端口 RF OFF。Sweep fire 与 Pulse trigger 是潜在能量操作。它们必须使用独立、一次性安全决定，不能因先前的 configure、arm 或 output ON 成功而自动获得许可。core 不会隐式打开输出、触发外部端口或开启后面板辅助输出。

## Service、CLI、doctor 与 run plan

### 当前已实现的入口

`RfSourceService` 统一负责 capability、access、资源租约、session health 和类型化 snapshot；driver 只执行已冻结的设备动作，CLI 不直接生成 SCPI。当前入口按能力层次分为三组：

```text
# 生产只读：DSG830 已由 A1 提升
wavebench rf-source idn
wavebench rf-source status
rf_source.status

# 生产 M1：仅在已完成 A3 的插件上，且必须同时具备 read_write、CW capability 和 OFF-only preflight
wavebench rf-source set-frequency --port PORT_ID HZ
wavebench rf-source set-power --port PORT_ID DBM
rf_source.set_frequency
rf_source.set_power_dbm

# 生产 M2：仅在已完成 A2 的插件上，且必须同时具备 read_write、output capability 和端口级 safety preflight
wavebench rf-source output --port PORT_ID on|off
rf_source.output_enable
rf_source.output_disable
```

`rf-source status` 和 `rf_source.status` 均要求 descriptor 声明 `rf_source.snapshot`；缺少该 capability 时，Core 会在打开 transport 前拒绝请求。`doctor` 仅新增 `rf_source` 的 `*IDN?` target；它不读取运行状态、不改变访问模式，也不打开 RF 输出。

M1 的每个 run step 都要求 `port_id` 与一个有限数值；M2 的每个 run step 都要求 `port_id`，并产生脱敏的 preflight／postcondition snapshot artifact。所有三类 step 使用独立的 `wavebench.rf_source.operation.v1` artifact namespace。DSG830 已由 A3 声明 `rf_source.cw_configure`，并由 A2 声明 `rf_source.output`；M1 仅在 `read_write`、目标端口明确 OFF 与完整 OFF-only preflight 同时成立时可执行，M2 还要求完整端口 safety 配置和 fresh preflight。

### M1 的生产 CW 与 M2 的生产输出合同

M1 是 OFF-only CW 配置：目标端口必须明确为 OFF，调制、Pulse、Sweep 与 protection 不得冲突；每次调用只写一个频率或 dBm 字段，并用独立 snapshot 回读确认。写后结果不明时不重试。

M2 是端口级输出事务。RF ON 必须确认完整 safety 配置、实际端接与 dBm 参考阻抗一致、频率与 dBm 功率处于设备和实验室配置范围内、调制／Pulse／Sweep 都关闭，并且 protection 仅含已知的非阻断状态。RF OFF 只依赖目标输出状态，不要求频率、功率、端接或 protection 可读。ON 写入或其 readback 结果不明时，session 降为不确定状态，并且只在受 guard 的 recovery 预算内最多执行一次同端口 OFF 和 OFF 回读；OFF 写入结果不明时不重试，session 降为 poisoned。

M1 已由 A3 在真实设备上完成受控频率／功率写入、独立 readback、低功率 RF ON/OFF 环回与最终 OFF 验收，因而将 `rf_source.cw_configure` 纳入 DSG830 production descriptor。M2 已由 A2 将 `rf_source.output` 纳入同一 descriptor；人工确认的实验室端接本身仍不构成调制、Pulse、Sweep、trigger 或其它额外写入授权。

### M3 与 M4 的离线入口

M3 的写入 CLI 和 run step 已进入当前 Core schema，但其真实仪器使用仍由 production descriptor 的 A4 capability 门决定：

```text
wavebench rf-source modulation configure-am ...
wavebench rf-source modulation configure-fm ...
wavebench rf-source modulation configure-pm ...
rf_source.modulation_configure

# M4 Pulse：只允许 internal／single 配置，配置后 Pulse 仍保持关闭
wavebench rf-source pulse configure --port PORT_ID --period-s SECONDS --width-s SECONDS --polarity normal|inverted
rf_source.pulse_configure

# Pulse trigger、Sweep arm／fire／stop 与 Level Sweep 仍是目标合同，尚未进入当前 Core schema
wavebench rf-source pulse trigger ...
wavebench rf-source sweep arm ...
wavebench rf-source sweep fire ...
wavebench rf-source sweep stop ...

# M4 Step Sweep：仅配置 frequency-only、forward、linear、ramp profile，配置后 Sweep 仍保持关闭
wavebench rf-source sweep configure --port PORT_ID --start-frequency-hz START --stop-frequency-hz STOP --points COUNT --dwell-s SECONDS
rf_source.sweep_configure
```

M3 使用 `modulation_kind = "am" | "fm" | "pm"`，并且只接受与该模式匹配的 `depth_percent`、`frequency_deviation_hz` 或 `phase_deviation_rad` 之一。它要求 RF OFF、所有调制模式 disabled、Pulse／Sweep disabled 和无活动 protection condition。FM／PM 的共享选择位作为调制 snapshot 的独立字段记录：preflight 可接受另一种已关闭的 FM／PM 选择，固定 driver 写入会明确选择目标类型，postcondition 则必须确认目标类型。随后以调制 snapshot 独立验证目标模式、内部 source、Sine waveform、数值、内部频率和全局状态。结果不明时不重试，且不会隐式执行 RF OFF recovery。

`rf_source.pulse_configure` 已进入当前 Core schema；DSG830 已在 A4 Pulse 证据复核后声明该 capability。它只接受 period、width 和 polarity，要求 RF 输出、调制、Pulse、Sweep 均关闭且无活动 protection；写入后必须读回 internal／single、请求的 timing／polarity 和 Pulse 关闭状态。它不提供 trigger、后面板 Pulse I/O 或 RF 输出控制。

`rf_source.sweep_configure` 也已进入当前 Core schema，但仅表示离线合同：请求只接受起止频率、点数和驻留时间，静态 profile 固定为 `STEP`／`FWD`／`RAMP`／`LIN`。Core 在写前和写后都要求 RF 输出、调制、Pulse、Sweep 关闭且无活动 protection；driver 配置后必须保持 Sweep disabled，并以独立 profile readback 逐字段确认。该 operation 没有 Level Sweep、arm、fire、`SWE:EXEC`、trigger、后面板接口或 RF 输出字段。当前 DSG830 production descriptor 不声明该 capability，因此普通 CLI 或 run plan 会在 transport I/O 前拒绝。Pulse trigger、Sweep arm／fire／stop 仍是目标合同。所有这些入口都必须显式指定 `port_id`，拥有独立 `OperationSpec` 与 `wavebench.rf_source.operation.v1` artifact，不得访问普通 source channel 或未声明端口。

## M0–M4 里程碑

下表同时标出当前进度和交付边界。Core 与 DSG830 插件的依赖、完成条件和状态见[RF 信号源开发里程碑](WaveBench_RF信号源开发里程碑.md)。

| 里程碑 | 通用核心交付 | 首个适配器离线交付 | 离线验证标准 |
| --- | --- | --- | --- |
| M0（生产只读） | `rf_source` kind、config、拓扑/profile、可观测 snapshot、Protocol、registry、doctor、只读 CLI 与 run status | 严格 snapshot parser；A1 后 production descriptor 声明只读 snapshot | descriptor 无 I/O；每个状态 query、解析和坏响应测试通过；A1 证据复核后仅提升 snapshot。 |
| M1（离线完成；DSG830 A3 已提升） | CW request／result、OFF-only transaction、CLI、run step、artifact 与端口范围检查 | 频率／dBm 功率单次写入与独立回读 | output ON、活动调制／Pulse／Sweep 或越界请求时零写拒绝；结果不明无重试；DSG830 仅在 A3 复核后声明 CW。 |
| M2（离线完成；DSG830 A2 已提升） | per-port 输出事务、安全预检、受 guard 的一次性 RF OFF recovery、CLI、run step 与 artifact | RF ON/OFF 单次写入；Core 负责独立 readback | 安全配置缺失、端接不匹配、保护异常或状态缺失时 ON 零写拒绝；ON readback 失败最多一次 OFF；DSG830 仅在 A2 复核后声明 output。 |
| M3（离线完成；A4 的 AM、FM 已通过，PM 待定位） | 内部正弦 AM／FM／PM profile、typed request/result、调制 snapshot、配置 Service／CLI／run step／artifact；按模式关闭仅用于本地证据与私有恢复 | 内部 Sine 调制序列、严格 readback、单模式 RF-OFF evidence harness 与受限恢复路径 | 输出未 OFF、任一模式已开启、profile 不支持、Pulse／Sweep／protection 冲突或 postcondition 不符时零写拒绝；production capability 等待完整 A4。 |
| M4（Pulse；DSG830 A4 已提升） | internal／single Pulse profile、typed request/result、OFF-only Service、CLI、run step 与 artifact | period／width／polarity 的固定映射；配置后强制 Pulse OFF | 输出、调制、Pulse、Sweep 或 protection 不满足时零写拒绝；写后逐字段 readback；DSG830 已声明 `rf_source.pulse_configure`。 |
| M4（Step Sweep；离线完成） | frequency-only Step Sweep profile、configure、CLI、run step 与 artifact；不含 arm／fire／stop | `STEP`／`FWD`／`RAMP`／`LIN` 的严格 readback 与固定配置写入，最后保持 Sweep disabled | 输出、调制、Pulse、Sweep 或 protection 不满足时零写拒绝；不写 `SWE:EXEC`、trigger、Level Sweep 或 RF 输出。production capability 等待专项 A4 证据。 |

M0–M4 只证明代码合同和 SCPI 映射。后续证据顺序固定为：A1 只读 snapshot，A2 RF OFF/ON，A3 CW 环回，A4 调制／Pulse／Sweep，A5 外部触发或同步接线。每项 evidence 绑定 capability、型号、固件、选件、端口、端接和最终 RF OFF 状态。

DSG830 的 A1 已使 production descriptor 声明 `rf_source.snapshot`，A2 已使其声明 `rf_source.output`，A3 已使其声明 `rf_source.cw_configure`。A4 的 AM、FM RF-OFF 单模式验证已通过并完成关闭恢复；PM 仍有严格读回不匹配，因此尚未形成可提升整体调制 capability 的合格证据。A4、A5 仍分别是调制／Pulse／Sweep、外部 trigger／同步 capability 的实机提升门槛。未取得对应 evidence 时不得声明或提升其它 production descriptor capability。

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
| Sweep 状态 | `:SWE:STAT?` | `OFF`、`FREQ`、`LEV` 或组合；frequency-only Step Sweep 子集进入 M4。 |
| 保护状态 | `:STAT:QUES:POW:COND?` | 位 0 ALC unlocked、位 1 output power protection、位 2 heater detector；未知高位按阻断处理。 |

手册未给出可安全采用的 error queue 查询命令。因此 DSG830 不声明 `rf_source.errors`，所有写后判断依赖独立状态回读和 condition register。

DSG830 的 production `descriptor()` 在 A1／A2／A3／A4 Pulse 完成后声明 `rf_source.idn`、`rf_source.snapshot`、`rf_source.cw_configure`、`rf_source.output` 与 `rf_source.pulse_configure`。`get_rf_snapshot()` 可通过只读入口观察状态，`configure_cw()` 只可在目标输出 OFF 的完整 preflight 后写入一个频率或功率字段，`set_rf_output()` 只可在完整 safety preflight 后切换 `rf_out`。driver 已实现 M3 内部正弦 AM／FM／PM 的离线映射、严格 readback 和按模式关闭；A4 harness 在配置读回后执行受限调制关闭，或以显式恢复模式将一个已知模式还原为关闭状态。M4 Pulse 固定 internal／single、period／width／polarity，并以 `:PULM:STAT OFF` 收尾；两种极性已通过受控实机配置、读回与最终 RF-OFF 验证。M4 Step Sweep 已实现仅配置的 `STEP`／`FWD`／`RAMP`／`LIN` 映射与严格 readback，并固定以 `:SWE:STAT OFF` 收尾；它不写 `:SWE:EXEC`、trigger、Level Sweep 或 RF 输出，且尚无 production capability。历史 A4 Pulse harness 在 descriptor 提升后拒绝重跑，普通 Pulse 使用必须经 production descriptor、`read_write` access 与完整 OFF-only preflight。A4 尚未提升 `rf_source.modulation_configure`、`rf_source.modulation_disable`，严格 parser 与既有证据也不开放 Step Sweep、fire 或 trigger 控制。历史 `0.1.0` 的 `source.idn` 种子已迁移为当前 `0.2.0` 的 RF 包。

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
- M0–M3 离线验证已完成；DSG830 A1 snapshot、A2 受控输出与 A3 CW 环回证据已通过，production 已提升 snapshot、`rf_source.output` 和 `rf_source.cw_configure`。Core `ab4de10` 与插件 `36e1e8e` 增加按模式调制关闭与私有恢复路径；A4 的 AM、FM RF-OFF 序列通过，PM 尚有严格读回不匹配，故整体调制 capability 仍关闭。Core `ee790dc`／`8210299` 与插件 `e22911f`／`b3fa6c0` 增加 M4 Pulse 离线合同、控制入口和本地证据工具；两种极性通过受控实机验证后，插件 `40564a9` 将 `rf_source.pulse_configure` 加入 production descriptor。Core `d3481d8`／`8ec1733`／`e04ed60` 与插件 `851bdf5` 增加 frequency-only Step Sweep 的离线合同、固定映射、CLI、run 与 artifact；插件 `15c61e1` 增加隔离的零写诊断／受控配置 evidence harness。Step Sweep 尚无实机证据，production descriptor 未改变。A4–A5 仍不能据此提升调制、Sweep 或 trigger capability。
- `tool-of-rei/` 是本地恢复上下文，已忽略；面向项目的设计文档保存在 `docs/project/design/`。
