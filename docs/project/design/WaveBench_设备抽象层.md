# WaveBench 设备抽象层

## 核心原则

WaveBench 的抽象层遵循一个原则：

> 对上暴露「实验动作」，对下保留「设备差异」。

WaveBench 不应成为大而全的通用仪器库，也不应把裸 SCPI 命令散落在 CLI 或业务逻辑中。当前实现以 RTM2032 示波器采集为基础，同时为信号发生器等设备保留扩展位置。

## 不做的抽象

### 不做大而全 `Instrument`

不要把示波器、信号发生器、电源、万用表都塞进同一个类。

```python
class Instrument:
    def connect(): ...
    def write(): ...
    def capture(): ...
    def set_frequency(): ...
    def screenshot(): ...
```

这种类会很快变成垃圾桶。

### Metadata 与可执行插件分层

`wavebench.drivers` V1 继续提供只读 metadata；`wavebench.instruments` V2 提供可信的可执行 driver factory。当前 Service 依赖 `ScopeDriver` / `SourceDriver` / `RfSourceDriver` / `PowerDriver` / `DmmDriver` contracts，并通过统一 registry/factory 创建内置或外部 driver。

插件只负责设备差异。核心继续掌握 resource、transport factory、安全限制、Service、run plan 和 artifact。未选中的第三方插件默认不导入；`plugin ... --load` 才会显式加载并诊断全部可执行 descriptor。

### 不让裸 SCPI 散落在业务代码里

SCPI 命令必须集中在设备驱动层。CLI、交互 shell、service、export 都不直接写 `CHAN1:DATA?` 这类命令。

## 推荐分层

```text
CLI / Shell 层
  ↓
Service 层：实验动作
  ↓
Device Driver 层：设备能力
  ↓
Transport 层：VISA 通信
```

对应目录方向：

```text
src/wavebench/
├─ cli/
│  └─ scope.py
├─ services/
│  └─ scope_capture.py
├─ devices/
│  ├─ base.py
│  ├─ scopes/
│  │  ├─ base.py
│  │  └─ rtm2032.py
│  └─ generators/
│     ├─ base.py
│     └─ rigol.py
├─ transport/
│  └─ visa.py
├─ data/
│  ├─ waveform.py
│  └─ metadata.py
└─ export/
   └─ waveform_exporter.py
```

早期实现不必一次写满所有文件，但逻辑上仍应保持这四层。

## Transport 层

Transport 只负责通信，不关心设备类型。

职责：

- open / close；
- write；
- query；
- query binary / float list；
- timeout；
- OPC 等基础同步封装。

示意：

```python
class VisaTransport:
    def __init__(self, resource: str, timeout_ms: int = 10000): ...
    def open(self) -> None: ...
    def close(self) -> None: ...
    def write(self, command: str) -> None: ...
    def query(self, command: str) -> str: ...
    def query_float_list(self, command: str) -> list[float]: ...
```

早期 scope MVP 的 Transport 只支持 LAN：

```text
TCPIP::<instrument-ip>::INSTR
```

当前主线已经允许不同设备选择不同 transport，例如 DM3000/DM3058 可保留 serial skeleton；新增设备时以驱动指南和配置文档为准。

## Device Driver 层

Device Driver 层集中保存设备 SCPI 差异。

RTM2032 示例：

```python
class RTM2032Scope:
    def idn(self) -> str:
        return self.transport.query("*IDN?")

    def clear_status(self) -> None:
        self.transport.write("*CLS")

    def read_error(self) -> str:
        return self.transport.query("SYST:ERR?")

    def set_waveform_format_real(self) -> None:
        self.transport.write("FORM REAL")
        self.transport.write("FORM:BORD LSBF")

    def set_waveform_points_display_max(self, channel: int) -> None:
        self.transport.write(f"CHAN{channel}:DATA:POIN DMAX")

    def read_waveform_header(self, channel: int) -> WaveformHeader:
        raw = self.transport.query(f"CHAN{channel}:DATA:HEAD?")
        return WaveformHeader.parse(raw)

    def read_waveform_real(self, channel: int) -> list[float]:
        return self.transport.query_float_list(f"CHAN{channel}:DATA?")
```

如果 RTM2032 实际命令不是 `CHAN1:DATA?`，只改这里，不影响 CLI / service / export。

## ScopeDevice 能力接口

保留一个轻量接口，避免 service 层知道具体设备型号。

```python
class ScopeDevice(Protocol):
    def idn(self) -> str: ...
    def clear_status(self) -> None: ...
    def read_error(self) -> str: ...
    def single(self) -> None: ...
    def wait_opc(self) -> None: ...
    def prepare_waveform_transfer(self, channel: int) -> None: ...
    def read_waveform_header(self, channel: int) -> WaveformHeader: ...
    def read_waveform(self, channel: int) -> list[float]: ...
```

当前实现保留这一类能力接口，具体 driver 由 registry / factory 根据配置创建：

```text
RTM2032Scope
```

## Service 层

Service 层表达实验动作，而不是设备命令。

示例：

```python
def capture_once(scope: ScopeDevice, channel: int, output_dir: Path) -> CaptureResult:
    idn = scope.idn()
    scope.clear_status()

    scope.prepare_waveform_transfer(channel)
    scope.single()
    scope.wait_opc()

    header = scope.read_waveform_header(channel)
    voltage = scope.read_waveform(channel)

    waveform = Waveform.from_header_and_voltage(header, voltage)
    return CaptureResult(idn=idn, waveform=waveform)
```

`capture_once()` 不直接写 SCPI。

交互式 shell 和非交互式 CLI 都调用同一个 service 函数。

## Data 层

不要让 `list[float]` 在项目里到处流动。至少定义波形对象。

```python
@dataclass
class WaveformHeader:
    x_start: float
    x_stop: float
    points: int
    segment: int | None = None

    @property
    def x_increment(self) -> float:
        return (self.x_stop - self.x_start) / (self.points - 1)
```

```python
@dataclass
class Waveform:
    channel: int
    time_s: np.ndarray
    voltage_v: np.ndarray
    header: WaveformHeader
```

## Export 层

Export 层统一负责保存。

职责：

- 保存 `.csv`；
- 保存 `.npy`；
- 保存 `.json` 元数据；
- 报告、截图和其他产物由相应的 artifact / report 模块负责，不由采集逻辑直接写入。

采集逻辑不直接写文件。

## SignalGenerator 接口

信号源通过对应的 driver 和 Service 接入，接口示意如下：

```python
class SignalGenerator(Protocol):
    def idn(self) -> str: ...
    def clear_status(self) -> None: ...
    def read_error(self) -> str: ...
    def set_sine(self, freq_hz: float, amp_vpp: float, offset_v: float = 0.0) -> None: ...
    def output_on(self, channel: int = 1) -> None: ...
    def output_off(self, channel: int = 1) -> None: ...
```

Service 层可以按以下顺序组合这些动作：

```text
设置信号 → 等待稳定 → 采集波形 → 保存数据 → 计算指标
```

## RF 信号源：当前 M0–M4 与后续阶段

上述 `SignalGenerator` 示例只描述普通函数／任意波形发生器。RF 信号源以频率、dBm 功率、RF 输出和稳定 `port_id` 为主，不能把它映射为普通 `SourceDriver` 的 Vpp、offset、数字 channel 或波形接口。

当前 Core 已实现独立 model、`RfSourceDriver` Protocol、只读 Service、OFF-only CW transaction、端口级输出 transaction、内部正弦 AM／FM／PM transaction、internal／single Pulse transaction、保持 Sweep disabled 的 Step Sweep transaction、`rf-source idn`／`rf-source status`／`rf-source set-frequency`／`rf-source set-power`／`rf-source output`／`rf-source modulation configure-*`／`rf-source pulse configure`／`rf-source sweep configure` 和对应的 run step。所有入口仍受 capability、access、资源租约与 session health 约束；descriptor 未声明所需 capability 时，会在 transport I/O 前被拒绝。

DSG830 已由 A1／A2／A3／A4 Pulse／A4 Step Sweep 将 snapshot、OFF-only `rf_source.cw_configure`、受 safety 限制的 `rf_source.output`、`rf_source.pulse_configure` 和 `rf_source.sweep_configure` 提升到 production。Pulse 与 Step Sweep 只允许已声明的配置 profile，且配置后保持 disabled；调制 capability、trigger、Sweep execute／fire 与 Level Sweep 仍待独立证据。设计与里程碑分别见[RF 信号源领域设计](WaveBench_RF信号源设计.md)和[RF 信号源开发里程碑](WaveBench_RF信号源开发里程碑.md)。

## 早期目录示意

以下目录反映早期设计，不作为当前源码树的路径清单。当前入口以代码和插件开发指南为准。

```text
src/wavebench/
├─ __init__.py
├─ cli/
│  ├─ __init__.py
│  └─ scope.py
├─ transport/
│  ├─ __init__.py
│  └─ rsinstrument.py
├─ devices/
│  ├─ __init__.py
│  ├─ scopes/
│  │  ├─ __init__.py
│  │  ├─ base.py
│  │  └─ rtm2032.py
│  └─ generators/
│     └─ __init__.py
├─ services/
│  ├─ __init__.py
│  └─ capture.py
├─ data/
│  ├─ __init__.py
│  └─ waveform.py
└─ export/
   ├─ __init__.py
   └─ waveform.py
```

## 抽象层规则

```text
1. CLI / shell 不直接写 SCPI。
2. Service 层表达实验动作，不表达设备命令。
3. Device Driver 层集中保存设备 SCPI 差异。
4. Transport 层只负责 VISA 通信。
5. Data / Export 层只负责波形对象和保存。
6. Scope、Source、Power 和 DMM 分别使用对应的能力接口。
7. 新设备通过 driver / transport 扩展，不改动已有 Service 的业务语义。
```

## 架构结论

WaveBench 的核心不是「一个示波器驱动」，而是「一组可组合的实验动作」。

当前流程可以组合示波器、信号源、电源和万用表动作：

```text
设置信号 → 等待稳定 → 采集波形 → 保存数据 → 计算指标
```

因此，抽象层应围绕「实验动作」设计，而不是围绕「SCPI 命令表」设计。


## 2026-04-29 补充：异构仪器传输路径

当前已验证两类设备不必强行共用同一种厂商库：

- **R&S RTM2032 示波器**：可继续使用面向 R&S 的 `RsInstrument` 路线。
- **RIGOL DG4202 信号发生器**：已验证可通过 **PyVISA + NI-VISA** 使用资源串 `TCPIP::<dg4202-ip>::INSTR` 通信。

因此，设备抽象层允许不同 driver 选择不同 transport 实现，而不是把所有设备都塞进同一个厂商 SDK。


### DG4202 模式状态的抽象提醒

对信号发生器这类设备，单个数值参数不能脱离模式状态解读。

例如 DG4202 已实机验证：

- `:SOURn:FREQ?` 在 `FREQ:MODE=SWE` 时不能简单理解为「固定输出频率」。
- 做离散扫点或固定频率实验前，driver/service 层应优先检查模式状态，并在必要时先切到 `FIX`。

因此 source driver 不应只暴露 `set_frequency()`，还应保留足够的 mode/status 查询能力。
