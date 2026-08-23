# WaveBench 可执行仪器插件 API 约定

本文定义 `wavebench.instrument.v2` 的包结构、descriptor、factory、capability 和运行时边界。开发流程见[插件开发指南](../../contributing/WaveBench_插件开发指南.md)，安装与事务边界见[可安装仪器插件用户指南](../../guides/WaveBench_可安装仪器插件.md)。

文中的约束分为三类：

- 「核心强制」：当前 WaveBench 会在包检查、descriptor 加载或 driver 创建时拒绝不符合要求的插件。
- 「接口约定」：插件必须遵守，但 Python 运行时无法完整验证；插件测试负责兜底。
- 「建议」：用于降低兼容和维护风险，不作为当前加载门槛。

## 适用范围

WaveBench 有两套不同的 entry point：

| entry point | API | 作用 | 是否提供仪器执行能力 |
| --- | --- | --- | --- |
| `wavebench.drivers` | `wavebench.instrument.v1` | 只读 metadata | 否 |
| `wavebench.instruments` | `wavebench.instrument.v2` | descriptor、factory 和 driver | 是 |

V2 capability 只声明 driver 已实现的能力。它不会自动生成 CLI 命令、Service 或 run-plan step；核心没有对应消费路径时，声明 capability 也不会让该功能自动出现。

V2 插件是可信 Python 代码，不是沙箱。导入 descriptor、构建源码 wheel 和安装包都可能执行插件代码。只应处理来源、版本和哈希均已确认的插件包。

## 包和 entry point

受管插件包必须满足以下条件：

| 项目 | 要求 | 校验位置 |
| --- | --- | --- |
| 分发 | 一个纯 Python wheel | 包检查 |
| Python 版本 | `Requires-Python` 如有声明，必须包含当前解释器版本 | 包检查 |
| WaveBench 依赖 | 必须恰好有一个当前环境中生效的 `wavebench` 依赖，并包含当前版本 | 包检查 |
| entry point 数量 | 必须恰好有一个 `wavebench.instruments` entry point | 包检查 |
| entry point 名 | 非空、无首尾空白、内部无空白 | 包检查 |
| entry point 目标 | 使用 `module:object` 形式 | 包检查 |
| driver 身份 | entry point 名必须与 descriptor 的 `driver_id` 完全一致 | 安装后检查和运行时 registry |

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

entry point 可以直接导出 `InstrumentDescriptor`，也可以导出返回 `InstrumentDescriptor` 的无参同步函数。其他返回类型会被拒绝。

descriptor 模块导入时不得连接仪器、调用 `open_transport()`、发送命令、扫描端口、创建文件或修改进程级全局状态。这是接口约定，当前加载器不会尝试撤销导入副作用。

## 导入边界

插件应从 `wavebench.instruments` 使用公开 descriptor、Protocol 和 model，从 `wavebench.errors` 使用公共异常类型：

```python
from wavebench.errors import DataError, InstrumentError
from wavebench.instruments import InstrumentDescriptor, OptionSpec, WaveformData
```

`wavebench.instruments.__all__` 是当前公开名称清单。插件不应依赖以下实现模块：

- `wavebench.instruments.registry` 和 `wavebench.instruments.factory`；
- `wavebench.services`、`wavebench.cli`、`wavebench.config` 和 `wavebench.tui`；
- 内置 driver、受管安装账本或私有辅助函数。

Protocol 使用结构化接口。driver 不必继承 Protocol；方法名、参数、返回类型和行为符合约定即可。

## Descriptor

最小 descriptor：

```python
from wavebench.instruments import InstrumentDescriptor, OptionSpec


def _open_driver(context):
    from .driver import ExampleScope

    return ExampleScope(
        transport=context.open_transport(),
        check_errors=bool(context.settings["check_errors"]),
        block_points=int(context.options["block_points"]),
    )


def descriptor() -> InstrumentDescriptor:
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
        option_specs=(
            OptionSpec("block_points", int, default=250_000, minimum=1),
        ),
        permissions=("instrument.io", "configured-resource-only"),
        factory=_open_driver,
        summary="Example oscilloscope driver.",
        wavebench_min_version="0.8.0",
        wavebench_max_version="0.9.0",
        scope_coupling_policy="unknown",
    )
```

### 字段约定

| 字段 | 核心强制 | 接口约定和用途 |
| --- | --- | --- |
| `driver_id` | 非空、无首尾空白；外置插件必须与 entry point 名一致 | 使用小写 ASCII canonical ID，推荐 `vendor.model` 形式；发布后不得复用给其他设备族 |
| `kind` | 配置解析时必须与目标槽位一致 | 只能是 `scope`、`source`、`power`、`dmm` 或 `sweep_analyzer` |
| `display_name` | 仅要求构造参数存在 | 面向用户的简短名称，不承担型号匹配 |
| `manufacturer` | 仅要求构造参数存在 | 使用厂商正式名称 |
| `models` | 至少包含一项 | 每项应为非空型号名称；不要把营销系列名当作已验证型号 |
| `aliases` | 外置 V2 插件必须为空 | alias 由核心维护，插件只能使用 canonical ID |
| `capabilities` | 非空；每项必须出现在核心 capability 表中 | 每项必须以当前 `kind` 为前缀，只声明已经实现并测试的能力 |
| `idn_patterns` | 当前不校验内容 | `doctor` 会删除非字母数字字符后做不区分大小写的子串匹配；它不是正则表达式，也不是设备身份认证 |
| `backends` | 至少包含一项 | 只使用核心支持的 backend token，见下文「Transport 选择」 |
| `resource_schemes` | 必须是去重的小写 token；空 tuple 表示不限制 | LAN-only 插件通常声明 `("tcpip",)`；该字段只检查 resource 前缀，不探测设备 |
| `option_specs` | 配置值按 `OptionSpec` 校验 | 名称必须唯一并使用小写 snake_case；当前核心不自动检查名称重复 |
| `permissions` | 当前只展示，不参与授权判断 | 常规仪器插件声明 `instrument.io` 和 `configured-resource-only`；不得把该字段描述成沙箱权限 |
| `factory` | 必须可调用 | 接收一个 `DriverContext`，同步返回 driver 对象 |
| `summary` | 当前不校验 | 一句话说明设备族和已实现范围 |
| `api_version` | 必须精确等于 `wavebench.instrument.v2` | 不按语义版本范围匹配；通常保留默认值 |
| `wavebench_min_version` | 当前核心版本不得低于该值 | 与 `wavebench_max_version` 组成半开区间 |
| `wavebench_max_version` | 当前核心版本必须低于该值 | `0.8.x` 插件通常使用 `0.9.0` |
| `distribution`、`version`、`source`、`origin` | entry point 加载后由 registry 按已安装分发覆盖 | 不得用于插件内部授权、信任或功能分支 |
| `scope_coupling_policy` | 值由类型约定为三种策略 | scope 必须准确声明；无法证明时使用 `unknown`，核心会默认拒绝无法确认高阻的采集 |
| `config_fields` | 当前只展示；为空时由 `option_specs` 推导 `options.<name>` | 只列出用户实际可配置的字段，不代表核心会按此字段授权 |
| `scope_extensions` | 仅允许 scope descriptor 使用，类型必须为 `ScopeDescriptorExtensions` | 为 R1.3 capability 提供静态截图、采集控制和 trace profile；旧插件保持 `None` |
| `source_extensions` | 仅允许 source descriptor 使用，类型必须为 `SourceDescriptorExtensions` | 为 `source.snapshot_v2` 及各已声明的 Source V2 写 capability 提供 topology、feature profile 和查询合同；旧插件保持 `None` |

### `scope_coupling_policy`

| 值 | 语义 |
| --- | --- |
| `fixed-high-impedance` | 仪器的 `AC`、`DC`、`GND` 等 coupling 不切换到低阻终端 |
| `switchable-termination` | coupling 可能代表高阻或 50 Ω；核心按查询值和显式放行判断 |
| `unknown` | 无法证明输入阻抗语义；默认 fail closed |

## `OptionSpec`

`OptionSpec` 只处理一层标量配置，不是通用 schema 系统。

| 字段 | 语义 |
| --- | --- |
| `name` | `[<kind>.options]` 下的键名 |
| `value_type` | 使用 `isinstance(value, value_type)` 做运行时检查 |
| `default` | 非 `None` 时，在配置缺失后写入 `context.options`，并执行同样的校验 |
| `required` | 为 `True` 且配置缺失时拒绝创建 driver |
| `minimum`、`maximum` | 将值转换为 `float` 后检查闭区间边界 |
| `choices` | 使用 Python 相等比较检查允许值 |

校验顺序如下：

1. 拒绝所有未在 `option_specs` 中声明的键。
2. 校验显式配置值。
3. 对缺失的 required option 报错。
4. 对其余缺失项应用非 `None` 默认值。

插件还必须遵守以下约定：

- `OptionSpec.name` 不得重复；当前实现会按名称建立映射，但不会主动报告重复项。
- 不要同时设置 `required=True` 和非 `None` 默认值；缺失时 required 检查优先。
- `default=None` 表示「不向 `context.options` 写入该键」，不能表示显式传入 `None`。
- `minimum` 和 `maximum` 只用于可安全转换为浮点数的数值类型。
- 容器、联合类型和字段间约束应使用公共 model 或在 factory/driver 中显式检查。

## `DriverContext`

`DriverContext` 是 frozen dataclass。`settings` 和 `options` 会复制为只读 mapping；只读是浅层的，插件不应在其中存放或修改可变对象。

| 属性 | 语义 |
| --- | --- |
| `driver_id` | 已解析的 canonical ID |
| `kind` | 已验证的仪器类型 |
| `resource` | 配置中的单个 resource 原值 |
| `backend` | 核心完成 alias 和优先级选择后的 backend token |
| `timeout_ms` | 普通连接和查询超时 |
| `opc_timeout_ms` | OPC 等待超时 |
| `logger` | 当前操作的 `CommandLogger` |
| `settings` | 核心设置；当前 scope、source 和 power 提供 `check_errors`，DMM 不保证该键存在 |
| `options` | 经过 `OptionSpec` 校验并补齐默认值的插件选项 |
| `open_transport()` | 打开核心配置和记录的 transport |

插件不得读取完整 `WaveBenchConfig`，也不得从环境变量或私有配置文件另选 resource。resource、backend、timeout 和日志必须由核心传入。

一次 driver 创建期间最多允许一个 transport 成功打开。第二次调用 `open_transport()` 会抛出 `ConfigError`。当前实现允许 factory 不打开 transport，但真实仪器 driver 应在 factory 中打开并接管唯一 transport，不应保存整个 context 供后台延迟建连。

## Transport 选择

当前核心可以创建以下 backend：

| token | transport |
| --- | --- |
| `serial` | `SerialTransport` |
| `pyvisa` | `PyVisaTransport` |
| `rsinstrument` | `RsInstrumentTransport` 的兼容选择路径 |
| `rsinstrument-socket` | RsInstrument SocketIO |
| `rsinstrument-rsvisa` | RsVisa |
| `rsinstrument-pyvisa-py` | pyvisa-py |

`lan`、`visa` 和 `pyvisa` 配置通常归一化为 `pyvisa`。当 descriptor 的全部 backend 都属于 RsInstrument 家族且配置为 `lan` 时，核心选择 descriptor 中的第一项。因此，RsInstrument backend 的顺序具有运行时意义。

`resource_schemes` 非空时，核心提取 resource 开头连续的 ASCII 字母并转为小写后匹配。例如 `TCPIP0::...` 的 scheme 为 `tcpip`。路径形式的串口 resource 不以字母开头，通常不应配合非空 `resource_schemes`。

插件不得自行绕过 `context.open_transport()` 创建 PyVISA、串口或 socket 会话，也不得在部分读取、写入或触发失败后静默切换 backend 并重放操作。

## Factory 和资源所有权

factory 的调用顺序为：

1. 核心解析 descriptor，并检查 API 版本、kind、WaveBench 兼容区间和 capability 名。
2. 核心选择 backend，检查 resource scheme，并校验 options。
3. 核心构造 `DriverContext` 并调用 factory。
4. 核心检查 `close()`，以及每个已声明 capability 对应的方法是否可调用。
5. Service 使用 driver；one-shot 调用或 run session 结束时调用 `close()`。

factory 返回 driver 后，transport 的所有权转移给 driver。`close()` 必须释放 transport 和插件创建的后台资源；建议实现为幂等操作。若 factory 或 capability 检查失败，核心优先调用已返回 driver 的 `close()`；只有 `close()` 不存在或失败时，核心才直接关闭已记录的 transport。

factory 必须同步返回，不得返回 coroutine、context manager 或 `(driver, transport)` tuple。factory 也不得启动无法由 `close()` 停止的线程或子进程。

## Capability 契约

核心在执行操作前检查所需 capability，缺失时不会打开 transport。driver 创建后，核心只检查对应属性是否可调用，不检查参数签名、返回类型、异常类型或操作语义。这些内容由 Protocol、model 和插件测试保证。

capability 必须与 descriptor 的 `kind` 使用相同前缀。当前 V2 loader 会拒绝未知 capability，但不会单独拒绝「已知但前缀属于其他 kind」的组合；插件测试必须覆盖该项。

旧接口的方法签名以 [`contracts.py`](../../../../src/wavebench/instruments/contracts.py) 为准，返回
对象以 [`models.py`](../../../../src/wavebench/instruments/models.py) 为准。Scope R1.3 的 Protocol
和 model 由 [`scope_extensions.py`](../../../../src/wavebench/instruments/scope_extensions.py) 定义，
并从 `wavebench.instruments` 导出。当前 capability 到方法的映射如下。

### Scope

| capability | 必须可调用的方法 |
| --- | --- |
| `scope.idn` | `idn` |
| `scope.errors` | `errors` |
| `scope.autoscale` | `autoscale` |
| `scope.fetch_waveform` | `fetch_waveform` |
| `scope.capture_waveform` | `capture_waveform` |
| `scope.capture_waveforms` | `capture_waveforms` |
| `scope.screenshot` | `screenshot_png` |
| `scope.channel_coupling` | `channel_coupling` |
| `scope.snapshot` | `get_snapshot` |
| `scope.acquisition_status` | `get_acquisition_status` |
| `scope.capture_average` | `capture_average` |
| `scope.digital_status` | `get_digital_status` |
| `scope.digital_waveform` | `get_digital_waveform` |
| `scope.history_timestamps` | `get_history_timestamps` |
| `scope.measurement_statistics` | `get_measurement_statistics` |
| `scope.math_metadata` | `get_math_waveform_metadata` |
| `scope.fft_status` | `get_fft_status` |
| `scope.reference_metadata` | `get_reference_waveform_metadata` |
| `scope.cursor_readout` | `get_cursor_readout` |
| `scope.screenshot_profile` | `get_screenshot_profile` |
| `scope.screenshot_v2` | `get_screenshot_profile`、`capture_screenshot`、`snapshot_screenshot_state`、`restore_screenshot_state`、`verify_screenshot_state_restored` |
| `scope.acquisition_run_state` | `get_acquisition_run_state` |
| `scope.acquisition_control` | `get_acquisition_run_state`、`start_continuous`、`stop_acquisition`、`acquire_single`、`snapshot_acquisition_control`、`restore_acquisition_control`、`verify_acquisition_control_restored` |
| `scope.trace_metadata` | `get_trace_metadata` |
| `scope.fetch_trace` | `get_trace_metadata`、`fetch_trace`、`snapshot_trace_transfer_state`、`restore_trace_transfer_state`、`verify_trace_transfer_state_restored` |
| `scope.error_drain_v1` | `drain_errors` |

`scope.capture_waveforms` 的固定语义是：先配置全部目标通道，只执行一次 acquisition 和 OPC 等待，再逐通道读取。不得静默退回逐通道重复触发。回调、失败时部分结果和返回字典的签名以 `MultiChannelScopeDriver` 为准。

### Scope R1.3 扩展

R1.3 的 Protocol 和 model 从 `wavebench.instruments` 导出。声明任一新增 capability 时，wheel
依赖和 descriptor 的 `wavebench_min_version` 都必须为 `0.8.23` 或更高的 `0.8.x` 版本。

profile 依赖如下：

| capability | 必需 descriptor profile |
| --- | --- |
| `scope.screenshot_profile`、`scope.screenshot_v2` | `scope_extensions.screenshot_profile` |
| `scope.acquisition_control` | `scope_extensions.acquisition_control_profile` |
| `scope.trace_metadata`、`scope.fetch_trace` | `scope_extensions.trace_profile` |

`scope.acquisition_control` 还必须同时声明 `scope.acquisition_run_state`。缺少 profile、方法或核心
版本门时，核心会拒绝 descriptor；只实现方法而不声明 capability，不会产生隐式能力。

公共调用入口为 `ScopeService` 和以下 CLI：

```text
wavebench scope screenshot profile
wavebench scope screenshot capture
wavebench scope acquisition status|start|single|stop
wavebench scope trace metadata|fetch
```

旧 `scope capture --screenshot` 继续服务声明 `scope.screenshot` 的插件。插件同时声明旧能力
和 `scope.screenshot_v2` 时，旧命令仍走 legacy 路径；只有 v2、没有旧能力时，核心会在仪器
I/O 前拒绝嵌入请求。需要 v2 截图时使用独立的 `scope screenshot capture`。父 capture 字段
闭包在后续实现前不得由插件自行模拟。

### Source

| capability | 必须可调用的方法 |
| --- | --- |
| `source.idn` | `idn` |
| `source.errors` | `errors`、`assert_no_errors` |
| `source.status` | `get_status` |
| `source.channel_profile` | `get_channel_profile` |
| `source.coupling_profile` | `get_coupling_profile` |
| `source.coupling_configure` | `configure_coupling` |
| `source.harmonic_profile` | `get_harmonic_profile` |
| `source.harmonic_configure` | `configure_harmonics` |
| `source.modulation_am_profile` | `get_am_modulation_profile` |
| `source.modulation_am_configure` | `configure_am_modulation` |
| `source.modulation_fm_profile` | `get_fm_modulation_profile` |
| `source.modulation_fm_configure` | `configure_fm_modulation` |
| `source.modulation_pm_profile` | `get_pm_modulation_profile` |
| `source.modulation_pm_configure` | `configure_pm_modulation` |
| `source.modulation_pwm_profile` | `get_pwm_modulation_profile` |
| `source.modulation_pwm_configure` | `configure_pwm_modulation` |
| `source.pulse_profile` | `get_pulse_profile` |
| `source.pulse_configure` | `configure_pulse` |
| `source.burst_profile` | `get_burst_profile` |
| `source.burst_configure` | `configure_burst` |
| `source.burst_trigger` | `trigger_burst` |
| `source.sweep_profile` | `get_sweep_profile` |
| `source.sweep_configure` | `configure_sweep` |
| `source.sweep_trigger` | `trigger_sweep` |
| `source.counter_profile` | `get_counter_profile` |
| `source.set_frequency` | `set_frequency` |
| `source.set_function` | `set_function` |
| `source.set_amplitude_vpp` | `set_amplitude_vpp` |
| `source.set_square_duty_cycle` | `set_square_duty_cycle` |
| `source.output` | `set_output` |
| `source.arbitrary_probe` | `probe_arbitrary_queries` |
| `source.arbitrary_upload` | `upload_dg4000_dac14_block` |
| `source.snapshot_v2` | `execute_source_query_plan_v2` |
| `source.basic_configure_v2` | `configure_source_basic_v2` |
| `source.output_v2` | `set_source_output_v2` |
| `source.harmonics_configure_v2` | `configure_source_harmonics_v2` |
| `source.modulation_configure_v2` | `configure_source_modulation_v2` |
| `source.pulse_configure_v2` | `configure_source_pulse_v2` |

### Source V2 扩展

`source.snapshot_v2`、`source.basic_configure_v2`、`source.output_v2`、
`source.harmonics_configure_v2`、`source.modulation_configure_v2` 和 `source.pulse_configure_v2` 从核心 `0.8.24` 开始提供，仍使用
`wavebench.instrument.v2`。采用任一 Source V2 capability 的
wheel 依赖和 descriptor `wavebench_min_version` 都必须为 `0.8.24` 或更高的 `0.8.x` 版本。
`source_extensions` 位于 descriptor 末尾且默认值为 `None`，因此未声明该能力的 V1 插件不需要
修改 descriptor 或提高版本下限。

插件从 `wavebench.instruments` 导入 `SourceDescriptorExtensions`、`SourceSnapshotV2Driver`、
`SourceBasicConfigureV2Driver`、`SourceOutputV2Driver`、`SourceHarmonicConfigureV2Driver`、
`SourceModulationConfigureV2Driver`、`SourcePulseConfigureV2Driver`、query
plan／execution record 和各类 typed profile。核心签发 semantic query plan；snapshot driver 只负责将
item 转成合法的厂商协议查询并返回类型化执行记录。插件不得返回完整 `SourceSnapshotV2`，也不得自行判定 `UNSUPPORTED`、
`NOT_APPLICABLE`、runtime profile 或 snapshot consistency。

snapshot query contract 只接受 `PURE_READ`。每个受支持的 read feature 必须有同 scope 的 query contract；
identity 必须是唯一、required 的 instrument-scope facet。声明为 `UNSUPPORTED` 或 `UNKNOWN` 的
feature 不得进入查询计划。查询项、effect、字段覆盖、query count 和 deadline 由核心复核；不符合
合同的执行记录不会生成 snapshot。

只读公共调用入口为 `SourceService.snapshot_v2()` 和：

```text
wavebench source snapshot-v2
```

基础写入入口由核心提供，不由插件拼接：

```text
SourceService.configure_basic_v2(request, *, correlation_id=None)
SourceService.set_output_v2(request, *, correlation_id=None)
SourceService.configure_harmonics_v2(request, *, correlation_id=None)
SourceService.configure_modulation_v2(request, *, correlation_id=None)
SourceService.configure_pulse_v2(request, *, correlation_id=None)
wavebench source basic-configure-v2 --channel N ...
wavebench source output-v2 --channel N on|off
wavebench source harmonics-configure-v2 --channel N --order N --preset all|even|odd
wavebench source modulation-configure-v2 --channel N --depth-percent PERCENT --internal-frequency-hz HZ
wavebench source pulse-configure-v2 --channel N --width-s S --delay-s S --leading-transition-s S --trailing-transition-s S
```

五个 Service 方法分别返回 `(typed_result, operation_artifact)`。`operation_artifact` 使用
`wavebench.source.operation.v1`，不得包含 raw SCPI、完整响应、资源地址、序列号、授权 token 或 nonce。

`source.harmonics_configure_v2` 只允许单通道的 `all`、`even`、`odd` 预设。descriptor 必须同时声明
Harmonic `READ`／`CONFIGURE`、允许的 order 区间和预设，以及 configured order、preset 与输出状态的可读回。
请求中的 `order` 必须落在运行时 profile 范围内；配置前后目标输出都必须回读为 OFF。该 operation 不提供
USER mask、逐分量幅度或相位、默认值重置，也不会隐式开启输出。

`source.modulation_configure_v2` 只允许单通道、输出 OFF 时的内部正弦 AM。descriptor 必须同时声明
Modulation `READ`／`CONFIGURE`、`am`、`internal`、`depth_percent` 与 `configuration_readable = true`，并能回读
同一 channel 的 output state。请求中的深度必须位于 `[0, 100]`，内部频率必须为有限正值；该 operation 不提供
disable、外部调制源、内部波形选择、FM／PM／PWM 或隐式输出 ON。

`source.pulse_configure_v2` 只允许单通道、输出 OFF 时的 WIDTH 脉冲形状。descriptor 必须同时声明
Pulse `READ`／`CONFIGURE`、WIDTH hold、delay、transition 与 `width_configuration_readable = true`，并能回读
同一 channel 的 output state。请求中的 width 不得小于 `4 ns`，delay 必须为有限非负值，两个 transition 必须为
有限正值且各自不超过 width 的 `0.625` 倍；该 operation 不提供 DUTY hold、partial patch、trigger、输出 ON 或隐式波形切换。

run plan 接受 `source.basic_configure_v2`、`source.output_enable_v2`、`source.output_disable_v2`、
`source.harmonics_configure_v2`、`source.modulation_configure_v2` 与 `source.pulse_configure_v2` 六个 Source V2 step；它们的 artifact 只在实际执行时写入
`run.json.source_operations`。

旧 `source.*` setter、output、trigger 和 ARB 路径继续保留。双合同插件上，四个 basic setter 与
`set_output` 会进入相应 V2 transaction；restore、ARB upload 和 V2 output 重叠的 trigger 在仪器 I/O 前
拒绝。双合同插件声明 `source.harmonics_configure_v2` 时，V1 `configure_harmonics` 和 basic restore
在仪器 I/O 前拒绝；声明 `source.modulation_configure_v2` 时，V1 `configure_am_modulation` 和 basic restore
也在仪器 I/O 前拒绝；声明 `source.pulse_configure_v2` 时，V1 `configure_pulse` 和 basic restore 也在仪器 I/O 前拒绝。
FM／PM／PWM 等尚无对应 V2 capability 的高级配置保持 V1。插件不得把 capability 注册视为
自行发起写操作的许可，也不得通过已有 V1 方法绕过核心路由。

### Power、DMM 和 sweep analyzer

| capability | 必须可调用的方法 |
| --- | --- |
| `power.idn` | `idn` |
| `power.status` | `get_status` |
| `power.measurement` | `get_measurement` |
| `power.set_voltage_current_limit` | `set_voltage_current_limit` |
| `power.output` | `set_output` |
| `power.protection` | `get_protection_status`、`set_protection` |
| `dmm.idn` | `idn` |
| `dmm.read` | `read` |
| `dmm.function_status` | `function_status` |
| `dmm.set_function` | `set_function` |
| `dmm.measurement_profile` | `measurement_profile` |
| `dmm.trigger_status` | `trigger_status` |
| `dmm.calculation_status` | `calculation_status` |
| `dmm.calculation_statistics` | `calculation_statistics` |
| `dmm.system_interface_status` | `system_interface_status` |
| `dmm.set_voltage_range` | `set_voltage_range` |
| `dmm.set_dcv_impedance` | `set_dcv_impedance` |
| `sweep_analyzer.idn` | `idn` |
| `sweep_analyzer.status` | `get_snapshot` |
| `sweep_analyzer.trace` | `fetch_frequency_response` |
| `sweep_analyzer.configure` | `apply_sweep_plan` |
| `sweep_analyzer.trigger` | `trigger_single` |
| `sweep_analyzer.output` | `set_source_output` |
| `sweep_analyzer.marker` | `read_markers` |
| `sweep_analyzer.analysis` | `read_measurements` |

`DmmDriver` 还定义了 `apply_function()`。当前 capability 校验只要求 `dmm.set_function` 对应的 `set_function()`；TUI 的复用 session 路径会调用 `apply_function()`。需要支持 TUI 的 DMM 插件必须同时实现这两个方法，并为这一差异添加测试。

## 参数和返回 model

公共 model 用于跨插件保持单位、字段和序列化语义一致。driver 不得返回自定义 dict 代替 Protocol 规定的 model，也不得复制一套同名 dataclass。

| kind | 常用公共返回类型 |
| --- | --- |
| scope | `WaveformHeader`、`WaveformData`、`ScopeSnapshot`、`ScopeAcquisitionStatus` 及各分析 model |
| source | `SourceStatus`、`SourceChannelProfile`、各配置和 profile model；Source V2 使用 `SourceQueryExecutionRecord`，最终 snapshot 由核心构造 |
| power | `PowerStatus`、`PowerMeasurement`、`PowerProtectionStatus` |
| dmm | `DmmReading`、`DmmMeasurementProfile`、各状态和配置 model |
| sweep analyzer | `SweepPlan`、`SweepAnalyzerSnapshot`、`FrequencyResponseTrace`、`TraceIntegrity`、`MarkerReading`、`InstrumentMeasurementResult` |

部分 model 会在 `__post_init__` 中检查有限值、数组维度、字段组合和枚举，另一些只是 frozen 数据容器。插件测试仍需检查以下内容：

- 数值单位与字段名符合公共 model；
- 波形和 trace 数组是一维、有限值，点数与 header 或 integrity 一致；
- 时间戳包含时区；
- 状态查询不隐式修改仪器；
- 写操作返回写后只读核对得到的状态，而不是直接回显请求参数。

## 异常、日志、重试和 session 合同

异常分类见[错误处理和日志策略](../WaveBench_错误处理和日志策略.md)。插件侧约定如下：

- 无效调用参数或无法解释的仪器响应使用 `DataError`。
- 仪器错误队列、写后核对失败或设备状态不满足操作条件使用 `InstrumentError`。
- 连接和超时异常由核心 transport 保留其结构化类型；插件不得把 `TransportIOError` 或 `SessionHealthError` 包装成 `OperationTimeout`、`StateDriftError` 或普通 `RuntimeError`。
- 配置、descriptor 和 factory 创建失败由核心转换为 `ConfigError`；运行中的设备数据错误不应伪装成配置错误。
- 所有 I/O 通过核心 transport 执行，并为每个 query 显式传入 `replay=ReplayPolicy.NO_REPLAY`、
  `SAFE_TO_REPLAY` 或 `READ_CONTINUATION_ONLY`。第三方旧调用不需要改语法，但核心默认按
  `no_replay` 处理；插件不能依赖 `read_retry_attempts` 隐式重放。
- 不得自动重试写命令、输出切换、手动 trigger 或已经开始消费响应的数据传输。
- `uncertain` 或 `poisoned` session 上，普通 driver 方法必须在 transport I/O 前失败；
  `on_failure = "continue"` 不能触发第二次恢复或验证写入。
- 恢复/验证授权、字段证据记录和 session 健康回转属于核心内部合同，插件只能实现已冻结的
  有界动作，不能构造授权 token、提交任意字段闭包或开放 raw SCPI 通道。
- capture/fetch 临时修改的厂商状态必须映射到核心字段 ID，不得把厂商命令名注册成新的公共字段。
  query 响应头、波形字节序和传输窗口分别使用 `scope.query_response_header`、
  `scope.waveform_byte_order` 和 `scope.waveform_transfer_window`。其中 transfer window 是完整的
  原子选择状态，包含后端支持的稀疏率、点数、首点和分段选择，不能只核对其中一部分。
- `verification_fields` 只定义恢复后必须取得的证据范围。恢复写入成功不等于验证成功；插件必须
  通过独立 readback 和规范化比较提供结果，核心 coordinator 才能记录字段证据。缺少任何必验字段
  时保持 fail closed，插件不得自行把 session 改回 `healthy`。
- driver 不得吞掉错误后返回伪造的成功状态。

核心 R1 当前已在开发分支实现但尚未发布。插件 wheel 的 `Requires-Dist`、descriptor
`wavebench_min_version` 和 `api_version` 在包含 R1 的核心版本发布前不得提高；升级前还要
补齐结构化异常优先级、恢复失败后零二次 I/O 和发送次数断言。

## 当前不会自动校验的项目

以下内容属于插件发布门槛，但当前核心不会完整验证：

| 项目 | 插件侧验证方式 |
| --- | --- |
| `driver_id` 的小写 dotted 命名 | descriptor 单测 |
| capability 前缀与 `kind` 一致 | 集合断言 |
| `OptionSpec.name` 唯一 | descriptor 单测 |
| 方法签名和返回类型符合 Protocol | fake transport 单测和静态类型检查 |
| `permissions` 对应真实行为 | 人工审查；该字段当前不授权 |
| `config_fields` 与配置文档一致 | 文档测试或快照测试 |
| `idn_patterns` 能区分设备族 | 脱敏 IDN 样本测试 |
| `close()` 真正释放 transport | 失败注入和 close 次数测试 |
| 导入 descriptor 无 I/O 副作用 | 子进程导入测试 |

## 兼容性

插件包有两层版本门：

1. wheel metadata 中的 `Requires-Dist: wavebench...`；
2. descriptor 的 `wavebench_min_version` 和 `wavebench_max_version`。

两层都必须包含当前 WaveBench 版本。descriptor 区间为左闭右开；`api_version` 则要求精确字符串相等。

以下变更应视为不兼容：

- 修改现有 capability 的方法名、参数或返回 model；
- 改变字段单位、枚举含义或写操作的副作用；
- 让原本只读的方法开始写入或触发；
- 更换 canonical `driver_id` 指向的设备族；
- 缩小已发布 model 的有效输入范围，导致旧插件对象无法构造。

新增独立 capability、model 的可选字段或 descriptor 展示字段，通常可以保持现有插件兼容。每次提高最低 WaveBench 版本时，应同步修改 wheel 依赖、descriptor 区间和插件测试矩阵。

## 最小验证矩阵

发布前至少覆盖：

- descriptor 子进程导入无 I/O；
- entry point 名、canonical ID、kind、API 版本和兼容区间；
- capability 前缀、方法存在性、签名和返回 model；
- `OptionSpec` 的默认值、required、类型、范围、choices、未知键和重名；
- fake transport 下的正常、超时、短响应、坏数据、错误队列和 close；
- factory 抛错、capability 不完整、`close()` 抛错和第二次 transport 请求；
- 只读方法无写命令，写操作无隐藏 output 或 trigger；
- wheel 包检查、一次性 venv 安装、`plugin doctor --load` 和卸载；
- 如使用受限覆盖槽位，验证 canonical ID 选择外置实现、短 alias 保留内置实现、卸载后回退。

示例命令：

```bash
python -m pytest -q
python -m ruff check .
python -m wavebench plugin package check ./dist/plugin.whl
python -m wavebench plugin install ./dist/plugin.whl --dry-run
python -m wavebench plugin doctor --load
```

默认测试不得扫描端口、连接仪器或发送 SCPI。实机验收应单独授权，并使用脱敏配置和可恢复的状态检查。
