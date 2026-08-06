# WaveBench 可执行仪器插件开发指南

WaveBench 支持两条互不替代的插件路径：

- `wavebench.drivers` / `wavebench.instrument.v1`：只读 metadata，保留向后兼容。
- `wavebench.instruments` / `wavebench.instrument.v2`：可信 Python 包提供真实仪器 driver factory。

V2 插件只替换设备差异层。transport 创建、resource、timeout、命令日志、安全限制、Service、run plan 和 artifact 仍由 WaveBench 核心掌握。声明式 SCPI TOML 仍只用于校验和显式只读 IDN probe，不能进入真实执行路径。

> [!IMPORTANT]
> Instrument API V2 与受管插件生命周期从 `v0.8.0` 起正式提供；配套插件必须声明 `wavebench>=0.8,<0.9`，不能与 `v0.7.0` 配套运行。开发和发布插件时仍应使用同一核心版本完成离线 wheel 与生命周期门禁。

## 最小目录

独立插件源码仓库中的 DS1000Z 包可作为完整模板；WaveBench 核心仓库不再复制厂商插件源码：

```text
packages/wavebench-rigol-ds1000z/
├── pyproject.toml
└── src/wavebench_rigol_ds1000z/
    ├── __init__.py
    ├── descriptor.py
    └── driver.py
```

最小 `pyproject.toml`：

```toml
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "wavebench-example-scope"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["wavebench>=0.8,<0.9"]

[project.entry-points."wavebench.instruments"]
"example.scope" = "wavebench_example_scope:descriptor"
```

entry point 名必须等于 canonical `driver_id`。普通 metadata 命令不导入 V2 插件；只有配置真正选中该 ID、运行 `plugin info <driver_id> --load`，或运行 `plugin list/doctor --load` 时才导入 descriptor。V1 metadata entry point 仍由独立的 `--include-entry-points` 开关控制。

## Descriptor 与 factory

插件导出一个 `InstrumentDescriptor` 或返回它的无参函数：

```python
from wavebench.instruments.api import InstrumentDescriptor, OptionSpec


def _open_driver(context):
    from .driver import ExampleScope

    return ExampleScope(
        transport=context.open_transport(),
        check_errors=bool(context.settings["check_errors"]),
        block_points=int(context.options["block_points"]),
    )


def descriptor():
    return InstrumentDescriptor(
        driver_id="example.scope",
        kind="scope",
        display_name="Example Scope",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=("scope.idn", "scope.capture_waveform"),
        idn_patterns=("EXAMPLE,EX1",),
        backends=("pyvisa",),
        resource_schemes=("tcpip",),
        option_specs=(OptionSpec("block_points", int, default=250000, minimum=1),),
        permissions=("instrument.io", "configured-resource-only"),
        factory=_open_driver,
        wavebench_min_version="0.8.0",
        wavebench_max_version="0.9.0",
        scope_coupling_policy="unknown",
    )
```

descriptor 至少声明 canonical ID、kind、API/兼容版本、厂商/型号、capability、IDN pattern、backend、受限选项、权限提示和 factory。仅允许特定 VISA 接口类型的插件还应声明 `resource_schemes`；例如 LAN-only 插件使用 `("tcpip",)`，核心会在打开 transport 前拒绝 `ASRL`、`USB`、`GPIB` 等资源。空 tuple 表示不限制，适用于必须保留多种连接方式的内置兼容实现。当前 V2 外置插件必须使用 `aliases=()`；scope 插件还应准确声明 coupling policy，无法证明输入安全语义时保留 `unknown`，核心会默认拒绝采集。

`backends` 的顺序是 `connection.backend = "lan"` 的首选顺序，但只在该 descriptor
声明的后端全部属于 RsInstrument 家族时生效。当前 RsInstrument 后端 token 为
`rsinstrument-socket`、`rsinstrument`、`rsinstrument-rsvisa` 和
`rsinstrument-pyvisa-py`；其中 `rsinstrument` 保留 RsInstrument 自身的实现选择和既有
pyvisa-py 兼容回退。插件不得在已开始的有状态查询或波形传输失败后静默切换后端并重放；
需要兼容路径时应由配置显式选择并重新打开会话。

核心只对 `rsinstrument-socket` 做受限 resource 规范化：简单的
`TCPIP::<host>::INSTR` 映射到 `TCPIP::<host>::5025::SOCKET`，已显式给出端口的
`TCPIP::<host>::<port>::SOCKET` 保留其端口。其他 TCPIP 形式 fail closed，插件不得自行
猜测端口或设备名。

RsInstrument SocketIO 会话固定启用 `AddTermCharToWriteBinBlock=True` 和
`DataChunkSize=512`，确保 definite binary block 具有消息终止符并限制 raw socket 单次发送
长度；显式 VISA 后端保持各自原有 binary write 语义。

需要支持重复 `--channel` 的 scope 插件还必须声明 `scope.capture_waveforms` 并实现同名方法。该方法的语义固定为：先配置全部目标通道，只执行一次 acquisition / OPC 等待，再逐通道读取；不得静默退回逐通道重复触发。不声明该能力的插件仍可执行单通道 `scope.capture_waveform`，多通道操作会在打开 transport 前明确拒绝。

插件模块导入时不得连接仪器、发送 SCPI、创建文件或修改全局状态。factory 才能调用 `context.open_transport()`，且每次 factory 调用最多只能成功打开一个 transport。

## DriverContext

核心传入的 `DriverContext` 只包含当前插件所需的窄上下文：

- 已解析的 canonical `driver_id` 与 `kind`；
- 配置中选中的单个 `resource`；
- 核心选定的 backend、timeout 与 OPC timeout；
- `CommandLogger`；
- 只读 core settings 与经过 `OptionSpec` 校验的插件 options；
- 受控 transport factory。

插件不应读取完整 WaveBench 配置，也不应持有 Service、TUI、RunService 或 artifact writer。

## Contracts 与 models

`capabilities` 是执行契约，不只是展示 metadata。核心会拒绝未知 capability；factory
返回后，每个已声明 capability 必须能映射到对应的 callable 方法。Service 和 run-plan
预检还会在 transport 打开前检查当前操作所需 capability，缺失时给出 canonical driver
ID、操作名和缺失项。

按 `kind` 可参考对应 Protocol 的方法签名：

- `ScopeDriver`
- `SourceDriver`
- `PowerDriver`
- `DmmDriver`
- `SweepAnalyzerDriver`

公共返回类型来自 `wavebench.instruments.models`，包括 `WaveformHeader`、`WaveformData`、`SourceStatus`、`PowerStatus`、`PowerMeasurement`、`PowerProtectionStatus`、`DmmReading`，以及扫频分析仪使用的 `SweepPlan`、`SweepAnalyzerSnapshot`、`FrequencyResponseTrace`、`TraceIntegrity`、`MarkerReading` 和 `InstrumentMeasurementResult`。不要复制另一套不兼容的数据模型。插件只需实现其声明能力对应的方法以及 `close()`，不必为了通过加载而实现整个 kind 的所有方法。字段和不变量以当前源码中的 models 与 Protocol 定义为准。

DG4000 系列 source 插件还可从 `wavebench.instruments` 导入稳定的 `DG4000DacBlock` 与 `DG4000ByteOrder`。核心继续负责 CSV/NPY 加载、归一化、DAC14 编码、CLI、Service、安全限制和 artifact；插件只接收已校验的 binary-block 对象并负责厂商协议上传。不要从 `wavebench.arbitrary` 复制或导入核心工作流实现。

factory 返回对象缺少已声明 capability 对应的方法时，核心会拒绝启用该插件并尝试关闭已创建资源。

## ID、alias 与兼容

- 当前 V2 外置插件只支持 canonical ID，不接受 alias；内置 driver 可继续保留兼容 alias。
- 除核心显式声明的可选覆盖槽位外，外部插件不能覆盖内置 canonical ID 或 alias；源码中的历史名称 `migration slot` 不表示内置驱动将被移除。
- entry point 名与 descriptor `driver_id` 必须一致。
- `kind` 必须与配置槽位一致。
- 当前 WaveBench 版本必须落在插件声明的半开兼容区间内。
- 第一阶段不解决同一 Python 环境中互斥 vendor SDK 依赖；出现真实需求后再评估独立进程或 RPC。

WaveBench 主包长期预装 RTM2000、DS1000Z、DG4000、DP800 和 DM3000 五个仪器族。DS1000Z 外置包使用独立 canonical `rigol.ds1000z`；配置 alias `ds1104` / `ds1000z` 继续选择内置实现。这避免外部包覆盖内置 alias，也便于卸载后安全恢复。

可选覆盖槽位是核心内置的窄白名单，不是插件可自行请求的权限。当前共享 canonical ID 的绑定为：`wavebench-rigol-dg4000` / `rigol.dg4202`、`wavebench-rigol-dm3000` / `rigol.dm3000`、`wavebench-rigol-dp800` / `rigol.dp800`、`wavebench-rohde-schwarz-rtm2000` / `rohde-schwarz.rtm2032`。对应短 alias `dg4202`、`dm3000` / `dm3058`、`dp800`、`rtm2032` 始终选择内置基线；卸载外置包后 canonical ID 恢复到内置实现。distribution、canonical ID 或 alias 任一不匹配都会按普通冲突拒绝。

## 测试门槛

至少覆盖：

- descriptor 构建不产生 I/O；
- canonical ID、alias、kind、版本和冲突校验；
- OptionSpec 默认值、类型、范围和未知字段；
- capability 到 callable 的映射，以及缺 capability 时不打开 transport；
- fake transport 下的核心读写能力、错误队列和 close；
- timeout、短 bin-block、坏 preamble、截图失败；
- 坏插件不影响其他内置驱动；
- wheel 在临时 venv 中安装、entry point 发现、重装和卸载；
- `plugin package check` 拒绝无效 `RECORD`、重复成员、路径越界、`.pth`、`.data` 重定位、核心 `wavebench/` 覆盖及超出资源上限的 wheel；
- 受管 install / upgrade / downgrade / remove 在临时 venv 中完成事务、回滚和 recover 测试；
- 卸载后缺失提示或内置 fallback 行为。

从 WaveBench 仓库根目录运行以下示例：

```bash
python -m pytest -q packages/wavebench-rigol-ds1000z/tests
python -m ruff check packages/wavebench-rigol-ds1000z
python -m wavebench plugin package check ../wavebench-instrument-plugins/packages/wavebench-rigol-ds1000z
python -m wavebench plugin install ../wavebench-instrument-plugins/packages/wavebench-rigol-ds1000z --dry-run
python -m wavebench plugin doctor --load
```

## 信任与发布边界

Python entry point 是可信代码扩展，不是安全沙箱。安装插件等价于允许该包及其 build backend 在当前 Python 用户权限下执行代码。WaveBench 提供用户显式指定本地源码/wheel 的受管安装事务，但不提供在线商店、自动下载、自动依赖安装、插件评分或每插件进程隔离。

建议每套 WaveBench 使用独立 venv，固定 WaveBench/插件版本，保存 lockfile，并在分发 wheel 时校验 hash。公开示例 resource 使用 RFC 5737 保留地址，例如 `TCPIP::192.0.2.20::INSTR`，不得提交真实实验室地址、序列号、凭据、原始波形或命令日志。
