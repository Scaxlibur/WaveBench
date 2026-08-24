# WaveBench 可执行仪器插件开发指南

本文说明如何把独立 Python 包接入 WaveBench 的真实仪器执行路径。字段、capability 和生命周期的精确定义见[可执行仪器插件 API 约定](../reference/plugins/WaveBench_可执行仪器插件API.md)。

## 先选扩展类型

| 目标 | 扩展方式 | 能否执行仪器操作 |
| --- | --- | --- |
| 展示型号、能力和配置字段 | `wavebench.drivers` / Instrument API V1 metadata | 否 |
| 校验受限 SCPI 描述并显式执行只读 IDN probe | [声明式 SCPI 插件](../reference/plugins/WaveBench_声明式SCPI插件.md) | 仅 IDN probe |
| 提供真实 driver、transport 接入和 capability | `wavebench.instruments` / Instrument API V2 | 是 |

三条路径相互独立。V1 metadata 或声明式 TOML 不会自动进入 Service、CLI 和 run-plan 执行路径；V2 capability 也不会自动生成新的上层命令。

> [!IMPORTANT]
> Instrument API V2 从 `v0.8.0` 起提供。当前 `0.8.x` 插件应同时声明 wheel 依赖 `wavebench>=0.8,<0.9`，以及 descriptor 兼容区间 `>=0.8.0,<0.9.0`。两处版本门必须一致。

## 职责边界

| 插件负责 | WaveBench 核心负责 |
| --- | --- |
| 厂商协议、命令拼接和响应解析 | resource、backend 和 timeout 选择 |
| capability 对应的 driver 方法 | transport 创建和命令日志 |
| 写后只读核对和仪器错误队列 | Service、CLI、run plan 和 artifact |
| `close()` 和插件私有资源清理 | safety limit、session 编排和恢复流程 |
| fake transport 单测和脱敏实机证据 | 插件包检查、受管安装和事务恢复 |

插件不得读取完整 WaveBench 配置、绕过核心另开连接、直接写 run artifact，或从 driver 内部隐式打开输出、reset、autoscale 和 trigger。

## 最小目录

每个 wheel 只提供一个 canonical driver：

```text
wavebench-example-scope/
├── LICENSE
├── README.md
├── pyproject.toml
├── src/
│   └── wavebench_example_scope/
│       ├── __init__.py
│       ├── descriptor.py
│       └── driver.py
└── tests/
    ├── test_driver.py
    └── test_wheel.py
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

entry point 名是 canonical `driver_id`，两者必须完全一致。外置 V2 插件不接受 alias。

## 实现 descriptor

descriptor 模块只声明 metadata 和 factory。导入模块不得连接仪器、发送命令、扫描端口、创建文件或修改全局状态。

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
        wavebench_min_version="0.8.0",
        wavebench_max_version="0.9.0",
        scope_coupling_policy="unknown",
    )
```

`permissions` 当前是展示信息，不是沙箱权限；`config_fields` 当前也是 metadata，不会改变配置解析或授权。能够影响运行时的字段、自动校验范围和未自动校验项见 [API 约定](../reference/plugins/WaveBench_可执行仪器插件API.md)。

## 实现 driver

driver 采用结构化接口，不必继承 Protocol。实现范围由 descriptor 的 capability 决定：

- 始终实现 `close()`；
- 只声明已实现并测试的 capability；
- 方法签名和返回 model 与 `wavebench.instruments` 中的 Protocol 保持一致；
- 通过 `context.open_transport()` 取得唯一 transport；
- 用公共 model 返回数据，不返回自定义 dict 代替 model；
- 用 `DataError` 表达无效响应，用 `InstrumentError` 表达设备状态或写后核对失败；
- 不隐藏写操作，不重试 output、trigger 或其他状态修改。

核心只检查方法是否可调用，不检查参数和返回类型。Protocol 一致性必须由插件测试保证。

## 选择 capability

capability 名必须与 `kind` 同前缀。例如 scope 只能声明 `scope.*`。完整 capability 到方法的映射见 [API 约定](../reference/plugins/WaveBench_可执行仪器插件API.md#capability-契约)。

不要为了让插件显得完整而声明尚未实现的能力。capability 会参与 Service 和 run-plan 预检；声明过多会把运行期错误伪装成已支持功能，声明过少则会在 transport 打开前被明确拒绝。

多通道 scope 如声明 `scope.capture_waveforms`，必须先配置全部通道，只执行一次 acquisition 和 OPC 等待，再逐通道读取。不得退化为每个通道独立触发。

### 采用 scope R1.3 扩展

准备实现截图、采集控制或 typed trace 时，先阅读
[scope 通用扩展接口 RFC](../rfcs/WaveBench_scope通用扩展接口RFC.md)和
[核心实施说明](../rfcs/WaveBench_scope通用扩展接口RFC_核心实施说明.md)。采用条件如下：

- wheel 依赖和 descriptor 均要求 WaveBench `0.8.23` 或更高的 `0.8.x` 版本；
- 从 `wavebench.instruments` 导入公共 Protocol、model 和 `ScopeDescriptorExtensions`；
- descriptor 提供 capability 对应的静态 profile；
- driver 实现 snapshot、baseline、restore 和 fresh verify，不把 session token 暴露给插件代码；
- `CHDR`、`CORD`、`WFSU` 等临时 transfer 设置逐字段映射、恢复和核对；
- binary framing 与具体 resource/backend 的 EOM 能力一致，不能用短读、换行或 timeout 猜测边界；
- fake conformance、包检查和实机验收分别通过后，再修改正式 descriptor。

本段的 `0.8.23`／`0.8.24` 只表示开发线中的静态合同下限。正式核心发行物出现前，插件可在
开发树进行离线验证，但不得发布提高后的 wheel 依赖、`wavebench_min_version` 或新增 scope
capability。发布时必须使用第一个实际包含完整合同的核心版本；同一版本号若存在不含合同的既有
artifact，应改用可区分的更高版本。

`scope.channel_input_state_v2` 是独立的只读输入状态 capability，不需要 `ScopeDescriptorExtensions`
profile。它只报告 coupling、termination 和可解释的阻抗缺席状态；不设置终端，也不改变旧
`scope.channel_coupling` 或标准 capture 的高阻前置检查。

`scope.digital_status_v2` 是独立的只读数字状态 capability，不需要 profile。driver 必须区分逐通道、
POD 与 shared 状态；设备返回但无法映射的 token 使用 `"unknown"`，没有查询合同的字段使用
`None` 和精确 `unavailable_fields`。它不证明数字 waveform 的逻辑编码、字节序或同次采集关系，
因此不能同时声明 `scope.digital_waveform`，除非另有该 capability 的完整证据。

未采用新增 capability 的旧插件不需要提高核心版本下限。旧 `scope capture --screenshot` 不承载
新 `scope.screenshot_v2`；新插件应使用独立截图 Service 或 `wavebench scope screenshot capture`。

### 采用 Source V2 扩展

准备提供完整信号源状态快照时，先阅读
[Source V2 能力、状态与复合输出安全 RFC](../rfcs/WaveBench_source能力状态与复合输出安全RFC.md)。
只读 snapshot 采用条件如下：

- wheel 依赖和 descriptor 均要求 WaveBench `0.8.24` 或更高的 `0.8.x` 版本；
- descriptor 追加 `source_extensions`，显式声明 topology、read feature profile 和 pure-read query contract；
- driver 实现 `execute_source_query_plan_v2(plan)`，返回 `SourceQueryExecutionRecord`；
- 插件负责具体协议、合法查询顺序和解析，核心负责 semantic plan、availability、runtime narrowing 和 consistency；
- fake transport 覆盖组合响应、标量查询、activation、语义缺字段、query limit、deadline 和传输异常；
- descriptor 导入、snapshot 和 capability 校验均不得写入仪器、触发、切换输出或消费状态。

`source_extensions` 不能单独启用能力，必须同时声明 `source.snapshot_v2`。未声明该能力的旧插件
继续走 Source V1。

基础写能力使用 `source.basic_configure_v2` 与 `source.output_v2`；它们的写入 contract、fresh snapshot、
回读与失败恢复都由核心 Service 组织。Harmonic 预设配置使用独立的
`source.harmonics_configure_v2`，driver 必须实现 `configure_source_harmonics_v2(request)`，且 descriptor 必须
声明 Harmonic `READ`／`CONFIGURE`、可配置 order 区间、允许预设，以及 configured order、preset 与输出状态
可读。该 capability 只覆盖 `all`、`even`、`odd` 预设，不得借此支持 USER mask、逐分量幅度／相位或隐式输出 ON。

Harmonic 关闭使用独立的 `source.harmonics_disable_v2`，driver 必须实现
`disable_source_harmonics_v2(request)`。descriptor 必须声明同一通道的 Harmonic `READ`／`DISABLE` 与 output
state readback；request 只有 channel，结果和独立回读都必须证明 Harmonic、输出均为 OFF。它不能作为隐式
basic reset，也不要求 order 或 preset readback。

内部 AM 使用独立的 `source.modulation_configure_v2`，driver 必须实现
`configure_source_modulation_v2(request)`。descriptor 必须声明 Modulation `READ`／`CONFIGURE`、`am`、
`internal`、`depth_percent` 和 `configuration_readable = true`，并能回读同一 channel 的 output state。
该 capability 只覆盖输出 OFF 时的内部正弦 AM：深度为 `[0, 100]`，内部频率为有限正值；不得借此支持
disable、外部调制源、内部波形选择、FM／PM／PWM 或隐式输出 ON。

内部 PM 使用独立的 `source.modulation_pm_configure_v2`，driver 必须实现
`configure_source_pm_modulation_v2(request)`。descriptor 必须声明 Modulation `READ`／`CONFIGURE`、`pm`、
`internal`、`phase_deviation_deg` 和 `configuration_readable = true`，并能回读同一 channel 的 output state。
该 capability 只覆盖输出 OFF 时的内部正弦 PM：相位偏差为 `[0, 360]`，内部频率为有限正值；不得借此支持
disable、外部调制源、内部波形选择、AM／FM／PWM 或隐式输出 ON。

内部 FM 使用独立的 `source.modulation_fm_configure_v2`，driver 必须实现
`configure_source_fm_modulation_v2(request)`。descriptor 必须声明 Modulation `READ`／`CONFIGURE`、`fm`、
`internal`、`frequency_deviation_hz` 和 `configuration_readable = true`，并能回读同一 channel 的 output state。
该 capability 只覆盖输出 OFF 时的内部正弦 FM：frequency deviation 与内部频率均为有限正值；不得借此支持
disable、外部调制源、内部波形选择、AM／PM／PWM 或隐式输出 ON。

内部 PWM 使用独立的 `source.modulation_pwm_configure_v2`，driver 必须实现
`configure_source_pwm_modulation_v2(request)`。descriptor 必须声明 Modulation `READ`／`CONFIGURE`、`pwm`、
`internal`、`configuration_readable = true`，且 `parameter_kinds` 至少包含 `duty_deviation_percent` 或
`width_deviation_s` 之一，并能回读同一 channel 的 output state。该 capability 只覆盖输出 OFF 时的内部正弦 PWM：
request 包含有限正值的内部频率，以及恰好一个分支——`[0, 50]` 的 DUTY deviation 或 `[0, 500000]` 秒的 WIDTH
deviation；不得借此支持 disable、外部调制源、内部波形选择、AM／FM／PM 或隐式输出 ON。

内部 Sweep 使用独立的 `source.sweep_configure_v2`，driver 必须实现
`configure_source_sweep_v2(request)`。descriptor 必须声明 Sweep `READ`／`CONFIGURE`、至少一个 spacing、internal
trigger、timing／marker 与 `configuration_readable = true`；同一 channel 的 Basic `READ` 必须声明 `sweep` frequency
mode，Output `READ` 也必须能够回读 output state。该 capability 只覆盖输出 OFF 时的内建 Sweep：request 包含 start/stop、一个已声明的
spacing、`[2, 2048]` 的 steps 和 `[0.001, 300]` 秒的 sweep time；不得借此支持 center/span、marker、外部／手动 trigger、
arm、fire、输出 ON 或返回固定频率。

内部 Triggered Burst 使用独立的 `source.burst_configure_v2`，driver 必须实现
`configure_source_burst_v2(request)`。descriptor 必须声明 Burst `READ`／`CONFIGURE`、`triggered`、`internal`、
`timing_readable = true` 和 `triggered_internal_configuration_readable = true`，并能回读同一 channel 的 output state。
该 capability 只覆盖输出 OFF 时的 enabled internal Triggered Burst：request 包含 cycles、phase、internal period 与 delay；
不得借此支持 Gated、Infinity、外部／手动 trigger、arm、fire、输出 ON 或隐式恢复。

WIDTH Pulse 使用独立的 `source.pulse_configure_v2`，driver 必须实现
`configure_source_pulse_v2(request)`。descriptor 必须声明 Pulse `READ`／`CONFIGURE`、WIDTH hold、
delay 与 transition 可读，以及 `width_configuration_readable = true`，并能回读同一 channel 的 output state。
该 capability 只覆盖输出 OFF 时的完整 WIDTH 形状：width 不小于 `4 ns`，delay 为有限非负值，两个 transition
为有限正值且各自不超过 width 的 `0.625` 倍；不得借此支持 DUTY hold、partial patch、trigger、输出 ON 或隐式波形切换。

ARB storage 与 selection 分别使用 `source.arbitrary_storage_v2` 和 `source.arbitrary_select_v2`。storage driver
必须同时实现 `read_source_arbitrary_storage_v2(channel, slot_id)` 与
`mutate_source_arbitrary_storage_v2(request, payload)`；前者返回指定命名槽位的 exists、SHA-256 与大小，后者必须在
设备侧以 create-only 或 compare-and-replace 语义执行单次 mutation。payload 只作为独立 `bytes` 参数传递，不能进入
request、descriptor evidence 或 operation artifact。声明 storage capability 的 ARB profile 必须显式给出
`storage_slot_metadata_readable`、`storage_write_modes` 与 `storage_max_payload_bytes`，并能回读 selection 与 output state。
storage 不选择波形、不改变输出，也不要求其它独立端口关闭。

selection driver 实现 `select_source_arbitrary_v2(request)`。descriptor 必须声明 ARB `READ`／`CONFIGURE`、可读的
selection 与 storage digest、允许 playback mode；true-ARB 还要求 sample rate 可读，Basic 必须支持可读的
`arbitrary` waveform，Output 必须能回读 state。DDS 只接受 playback frequency，true-ARB 只接受 sample rate；
selection 只允许目标输出 OFF，完成后仍为 OFF，不会隐式 ON。声明任一 ARB V2 capability 后，V1
`upload_arbitrary_waveform` 会在本地文件读取和仪器 I/O 前被拒绝，不能把混合 upload／selection／ON 的旧 route 部分映射。

跨通道 Combine、Coupling、Tracking 和相位关系分别使用 `source.combine_configure_v2`、
`source.coupling_configure_v2`、`source.tracking_configure_v2` 与 `source.phase_relation_configure_v2`。每项都使用
独立 driver method，request 只包含递增且唯一的 channel set 与 enabled state。descriptor 必须为该 relation 的
channel set 声明 `READ`／`CONFIGURE`，在 `SourceCrossChannelCapabilityProfile` 中列出 relation kind、支持的 channel set 和
`configuration_readable = true`；同 feature 还必须有 instrument-scope 的纯读 relation graph facet。
核心只要求 graph 展开后实际受影响的端口 OFF，未连通的独立端口可以继续 ON。driver result 必须回读 relation 与每个
受影响端口的 OFF 状态；无法证明 graph、端口范围或 readback 时不得声明此 capability。声明 Coupling V2 后，V1
`configure_coupling` 在 I/O 前拒绝；声明任一这四项 capability 后，V1 restore 也在 I/O 前拒绝。

实际插件声明任一 Source V2 写 capability 前，至少完成该 capability 的 A0 离线 fixture；声明的方向、profile、
方法与版本门必须同时通过核心校验。没有实机证据时，不得把核心合同注册描述为已完成设备写能力验收。

## 配置 options

插件私有配置放在对应的 `[<kind>.options]` 表中，并为每个键定义 `OptionSpec`。适合 `OptionSpec` 的内容包括分块点数、插件专用超时和明确枚举；resource、backend、通用 timeout、安全限制和输出状态仍由核心配置管理。

`OptionSpec` 名称应唯一并使用小写 snake_case。默认值也会接受类型和范围校验；`required=True` 不应与默认值同时使用。

## ID、版本和覆盖

- canonical ID 使用稳定的小写 dotted 名称，例如 `example.scope`。
- 外置插件必须使用 `aliases=()`。
- 当前 WaveBench 版本必须同时落在 wheel 依赖和 descriptor 半开区间内。
- 外部插件不能覆盖内置 canonical ID 或 alias，除非核心已为指定 distribution 和 canonical ID 建立覆盖白名单。
- 覆盖槽位不改变短 alias；短 alias 始终选择内置实现，卸载外置包后 canonical ID 恢复内置实现。

当前受限覆盖绑定见[可安装仪器插件用户指南](../guides/WaveBench_可安装仪器插件.md#查看与诊断)。插件不能自行申请或扩大白名单。

## 开发顺序

建议按以下顺序推进：

1. 冻结 canonical ID、kind、支持型号和只读 IDN 样本。
2. 先实现 `idn()`、`close()` 和一个最小只读 capability。
3. 用 fake transport 固定命令、终止符、响应解析、timeout 和错误映射。
4. 增加写 capability，每项都补写前条件、写后核对和失败语义。
5. 补 `OptionSpec`、公共 model 和 capability 一致性测试。
6. 构建 wheel，执行包检查和临时 venv 生命周期验证。
7. 通过离线门禁后，再单独申请实机验收。

## 测试门槛

至少覆盖：

- descriptor 导入不产生 I/O；
- entry point 名、canonical ID、kind、版本和 alias；
- capability 前缀、方法签名和公共返回 model；
- `OptionSpec` 默认值、required、类型、范围、choices、未知键和重名；
- fake transport 下的正常响应、短响应、坏数据、超时、错误队列和 close；
- 只读方法不写入，危险写操作不隐式重试；
- factory 失败和 capability 不完整时释放 transport；
- wheel 包检查、安装后 descriptor 加载、卸载和 fallback。

独立插件仓库的日常离线检查：

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
python3 scripts/dev_env.py check
```

从 WaveBench 仓库检查真实 wheel 或源码目录：

```bash
.venv/bin/python -m wavebench plugin package check <plugin-path>
.venv/bin/python -m wavebench plugin install <plugin-path> --dry-run
.venv/bin/python -m wavebench plugin doctor --load
```

源码目录检查会执行受信任的 build backend。若要求检查过程不执行构建代码，应先从可信流程取得 wheel，再直接检查 wheel。

## 发布边界

插件发布前应固定 WaveBench 兼容范围、构建 wheel、记录 SHA-256，并在一次性 venv 中完成安装、加载和卸载。不要在同一环境中混用 editable 安装和 WaveBench 受管插件账本。

默认测试不得扫描端口、连接仪器或发送 SCPI。实机测试必须单独授权，并使用脱敏 resource、可恢复状态和明确的输出关闭检查。

## 相关文档

- [可执行仪器插件 API 约定](../reference/plugins/WaveBench_可执行仪器插件API.md)
- [可安装仪器插件用户指南](../guides/WaveBench_可安装仪器插件.md)
- [新增仪器驱动指南](WaveBench_新增仪器驱动指南.md)
- [错误处理和日志策略](../reference/WaveBench_错误处理和日志策略.md)
- [插件注册表](../reference/plugins/WaveBench_插件注册表.md)
