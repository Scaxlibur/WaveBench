# WaveBench RF 信号源开发里程碑

[领域设计](WaveBench_RF信号源设计.md) · [项目文档分类](../README.md)

本文把 RF 信号源领域设计拆成可交付、可验证的双仓库工作项。它不替代当前程序事实源，也不将离线合同写成真实仪器能力。

## 当前基线

| 范围 | 当前状态 | 说明 |
| --- | --- | --- |
| Core `0.8.25` 开发线 | M0–M4、M3-MO 与 A5-0 的离线合同完成；已提升的范围由插件 descriptor 决定 | 已有 `rf_source` kind、配置、只读路径、OFF-only CW、端口输出、内部正弦 AM／FM／PM、按模式调制关闭、profile-bound 调制输出、internal／single Pulse、frequency-only Step Sweep，以及逻辑 trigger configuration 的只读类型、Service、CLI、run 和 artifact。 |
| DSG830 包 `0.2.0` | M0–M3、M4 Pulse 与 Step Sweep、M3-MO 的 A4/A4-MO 均已通过并提升；A5-0 映射已完成，物理 A5 未开始 | 已迁移为 `kind="rf_source"`，含 `rf_out` topology、严格 snapshot parser、`:FREQ`／`:LEV`／`:OUTP`、内部正弦 AM／FM／PM、internal／single Pulse、frequency-only Step Sweep 与六条固定 trigger configuration query 映射；production descriptor 声明 `rf_source.idn`、`rf_source.snapshot`、`rf_source.cw_configure`、`rf_source.output`、`rf_source.modulation_configure`、`rf_source.modulation_disable`、固定 AM `50 %`／`1 kHz`、最大 `-50 dBm` 的 `rf_source.modulated_output_enable`、`rf_source.pulse_configure` 和 `rf_source.sweep_configure`，不声明 `rf_source.trigger_snapshot`。 |
| 真实仪器证据 | A1、A2、A3、A4 调制／Pulse／Step Sweep 和 A4-MO 已完成；物理 A5 未开始 | 真实设备能力不能由 fake transport 替代；A1 提升 snapshot，A2 提升端口级 output，A3 提升 OFF-only CW，A4 分别提升 RF-OFF 调制、调制关闭、OFF-only Pulse 与保持 Sweep disabled 的 Step Sweep 配置。A4-MO 只提升固定 AM 调制输出 profile。A5-0 不产生 production capability。 |

## 双仓库交付规则

| 规则 | 要求 |
| --- | --- |
| Core 优先 | 新 kind、descriptor extension、capability registry、配置、Service、CLI、run 和 artifact 先在 Core 开发线完成；正式 wheel 验收还需要已发布的 Core。 |
| 插件跟随 | DSG830 已迁移为 `kind="rf_source"`，并把 `Requires-Dist: wavebench` 与 descriptor 版本门同步为 `>=0.8.25,<0.9`；当前双仓库开发依赖匹配的 Core checkout／版本范围。 |
| 测试隔离 | 后续 capability 可以只出现在 fake descriptor 中，用于离线测试；production descriptor 不得提前声明。 |
| 证据提升 | capability 进入 production descriptor 前，必须有对应 A 级实机证据，记录型号、固件、选件、端口、端接和最终 RF OFF 状态。 |
| 失败语义 | 不确定写入不重试；只有 session health 允许时，M2 才可最多执行一次目标端口 RF OFF recovery。M3 配置失败后只允许在新的、独立预检 session 中使用按模式调制关闭事务恢复已知状态。 |

## 里程碑总览

| 阶段 | 状态 | Core 交付 | DSG830 交付 | 完成条件 |
| --- | --- | --- | --- | --- |
| Seed | 历史完成 | 无 RF Core 改动 | `0.1.0` 的旧 `source.idn` 种子、无 I/O descriptor、包装与 fake 测试 | 已由 M0 迁移取代，不代表 RF 支持。 |
| M0 | 离线完成；A1 已完成 | `rf_source` 只读领域 | `rf_out` topology 与严格 snapshot parser | A1 复核后，生产包声明 `rf_source.idn` 和 `rf_source.snapshot`。 |
| M1 | 离线完成；A3 已完成 | OFF-only CW 配置 | `:FREQ`／`:LEV` 映射与独立回读 | typed request／result、Service、CLI、run step、artifact、fake 测试与受控 A3 证据已完成；DSG830 production 已开放 `rf_source.cw_configure`。 |
| M2 | 离线完成；A2 已完成 | RF 输出安全事务 | `:OUTP` ON/OFF 的单次映射；Core 独立 readback | 安全配置／端接／protection 不满足时 ON 零写拒绝；失败最多一次受 guard 的 OFF recovery；DSG830 production 已开放 `rf_source.output`。 |
| M3 | A4 已通过并提升 | 声明式内部正弦 AM／FM／PM profile、配置与按模式关闭事务、CLI、run 与 artifact | 手册范围内的内部 Sine AM／FM／PM 映射、严格 readback、单模式 RF-OFF evidence harness 与私有恢复路径 | 配置只在 RF OFF、所有调制模式 disabled、profile 匹配且 postcondition 成立时写入；关闭只在 RF OFF、唯一目标模式活动时写入。DSG830 production 已开放 `rf_source.modulation_configure` 和 `rf_source.modulation_disable`。PM 的 production profile 固定为 `1.25 rad`。 |
| M3-MO | A4-MO 已通过并提升 | `RfModulatedOutputProfile`、严格 pre/post RF 与调制 snapshot、一次 RF ON、受 guard OFF recovery、CLI、run 与 artifact | 复用既有 `:OUTP`／调制 snapshot 映射；固定 AM descriptor、CH2-only evidence harness 与 fake 回归 | 只接受已激活且精确匹配的内部 Sine profile；不配置调制，不重试 ON。普通 `rf_source.output` 仍要求调制关闭。DSG830 production 仅开放 AM `50 %`／`1 kHz`、最大 `-50 dBm`。 |
| M4（Pulse） | 离线完成；A4 Pulse 已通过 | internal／single Pulse profile、OFF-only 配置事务、CLI、run 与 artifact | `:PULM:SOUR INT`、`:PULM:MODE SING`、period／width／polarity，固定以 `:PULM:STAT OFF` 收尾 | 初始或写后 RF 输出、调制、Pulse、Sweep、protection 不满足时拒绝；不触发、不使用后面板 Pulse I/O；DSG830 production 已开放 `rf_source.pulse_configure`。 |
| M4（Step Sweep） | A4 已完成并提升 | frequency-only Step Sweep 合同、CLI、run、artifact 与本地 evidence harness | `:SWE:TYPE STEP`、`:SWE:DIR FWD`、`RAMP`／`LIN`、起止频率、点数、驻留时间，固定以 `:SWE:STAT OFF` 收尾 | 初始或写后 RF 输出、调制、Pulse、Sweep、protection 不满足时拒绝；不写 `:SWE:EXEC`、trigger、Level Sweep 或 RF 输出；DSG830 production 已开放 `rf_source.sweep_configure`。 |
| A5-0 | 离线完成；不属于物理 A5 证据 | `RfTriggerProfile`、`RfTriggerSnapshot`、`rf_source.trigger_snapshot`、只读 Service／CLI／run／artifact | `:PULM:TRIG:MODE?`、external edge／gate query、Sweep mode／period／point trigger query 与严格 enum parser | 只使用 `TRIGGER / READ` profile 和非 production descriptor；固定 query 顺序、零 write、未知值失败关闭。它不定义物理 connector，不发送 trigger，也不提升 production capability。 |

## Seed：历史种子包

DSG830 `0.1.0` 种子包只包含 `*IDN?`、`close()`、无 I/O descriptor、包元数据与离线测试。它使用普通 `source` 名称空间，是 Core M0 前维持打包与开发环境接入的过渡实现；当前包已迁移，不应作为现状描述。

不允许把下列内容从 Seed 推导出来：

- 历史 `source.idn` 种子本身可证明当前 `rf_source` 的能力；
- 频率、dBm 功率、RF 输出、调制、Pulse 或 Sweep 已可用；
- `source.idn` 种子可进入普通 source 的 Vpp、channel 或 run plan 工作流。

## M0：只读领域与迁移（离线完成）

### Core

- 已添加 `rf_source` plugin kind 和 append-only `InstrumentDescriptor.rf_source_extensions`。
- 已冻结 topology、`port_id`、`RfObserved`、snapshot、protection policy、Protocol 与 descriptor validator 的最小类型合同。
- 已添加 `rf_source.idn`、`rf_source.snapshot` capability 与 OperationSpec；复用 access policy、resource lease、guarded transport 和 session health。
- 已添加独立 `[rf_source]` 与按 `port_id` 配置的安全字段；不复用 `SourceConfig`、`source.terminations` 或 Vpp 限制。
- 已添加只读 `rf-source idn`／`rf-source status`、doctor IDN target，以及 `rf_source.status` 的 run schema、check、verify、intent、lifecycle、dispatch 和独立 artifact namespace。

### DSG830

- 已将种子 package 迁移到 `kind="rf_source"`、`rf_source.*` 和 `[rf_source]`。
- 已声明一个稳定端口 `rf_out`、手册范围和设备 dBm 参考阻抗。
- 已实现严格 snapshot parser，分别覆盖正常响应、未知响应、坏响应与 protection condition；A1 后可由 production 的只读状态入口消费。
- A1 已提升 `rf_source.snapshot`；A2 已将 M2 的 `rf_source.output` 提升到 DSG830 production descriptor；A3 已将 M1 的 `rf_source.cw_configure` 提升到同一 descriptor；A4 调制、Pulse 与 Step Sweep 已分别将 `rf_source.modulation_configure`、`rf_source.modulation_disable`、`rf_source.pulse_configure`、`rf_source.sweep_configure` 提升到同一 descriptor；A4-MO 已将固定 AM `50 %`／`1 kHz`、最大 `-50 dBm` 的 `rf_source.modulated_output_enable` 提升到同一 descriptor。

### 离线完成条件

- descriptor 导入和静态校验没有 I/O；每个状态 query、解析分支和坏响应都有测试。
- Core 的 registry、配置、CLI、doctor、run check／verify／intent 与 artifact 测试通过。
- 插件开发依赖区间与 descriptor 版本门均为 `>=0.8.25,<0.9`；正式 wheel 验收等待已发布的 Core 版本。

## M1：OFF-only CW 配置

Core 已在离线开发中加入单字段 `RfCwRequest`／result、`rf_source.set_frequency`／`rf_source.set_power_dbm` OperationSpec、端口范围检查、OFF-only Service 事务、CLI、run step 与带 preflight／postcondition snapshot 的 artifact。所有 CW 写入前必须确认目标 RF 输出为 OFF，且调制、Pulse、Sweep 与 protection 状态没有冲突。离线 fake／guarded transport 验收和 A3 受控实机证据均已完成；DSG830 production 已开放 CW capability。

DSG830 driver 已在离线测试中实现已冻结的 `:FREQ` 与 `:LEV` 单次写映射；Core 负责独立 snapshot 回读。输出 ON、越界、缺失安全关键状态或 readback 不确定时，必须零写拒绝或停止后续写入。A3 的实机证据已通过并复核，production descriptor 现在声明 CW write capability；Sweep execute／fire、trigger 与 Level Sweep 仍继续关闭。

## M2：RF 输出安全事务

Core 已在离线环境中完成 `RfOutputRequest`／result、`rf_source.output_enable`／`rf_source.output_disable` OperationSpec、descriptor 输出 profile 校验、每端口 safety 预检、CLI、run schema／intent／dispatch 与带 preflight／postcondition snapshot 的 artifact。RF ON 必须确认完整 safety 配置、端接匹配、频率与 dBm 功率范围、已关闭的调制／Pulse／Sweep，以及只含已知非阻断项的 protection。RF OFF 不依赖频率、功率、端接或 protection readback。

ON 结果不明、写后 readback 失败或 protection 变化时，Core 不重试 ON，而是将 session 降为不确定状态；仅在受 guard 的 recovery 预算内最多发送一次同端口 OFF，并独立回读 OFF。OFF 写入或其 readback 结果不明时不重试，session 降为 poisoned。DSG830 driver 使用单次 `:OUTP ON|OFF` 映射，Core 负责所有 snapshot readback 与 recovery；A2 已将 `rf_source.output` 加入 production descriptor，后续 A3 单独提升 CW，不提升 M3／M4 或其它 capability。

## M3：内部正弦调制（A4 已通过并提升）

Core 已冻结 `RfModulationModeProfile`、typed request／result、调制 snapshot、
`rf_source.modulation_configure` OperationSpec、Service、CLI、run step 与 artifact。M3 只描述内部
Sine AM／FM／PM：AM 使用 percent 深度，FM 使用 Hz 频偏，PM 使用 rad 相偏；每种模式都有独立的内部频率和静态范围。
run plan 使用 `modulation_kind` 表示 AM／FM／PM，避免与步骤自身的 `kind` 键冲突，并且只能提供与该模式匹配的一个数值字段。

DSG830 driver 已实现固定且无重试的内部正弦写入序列与严格 readback：先读取全局调制状态、三种模式的 enable 状态，只有写后才读取目标 profile 的 source／waveform／数值／内部频率；这样不会因未启用的外部 profile 阻塞安全 preflight。
FM／PM 的共享 mode type 会与被查询 profile 分开记录。当前类型与目标 FM／PM 不同但三种模式均 disabled 时，preflight 可继续，
固定写入显式选择目标类型；postcondition 必须核对目标类型。M3 preflight 要求目标 RF 输出 OFF、AM／FM／PM
均 disabled、Pulse／Sweep disabled 且无活动 protection condition；postcondition 要求 RF 仍 OFF、仅目标模式 enabled、全局调制
开启且所有目标字段精确匹配。写入或 postcondition 结果不明时不重试，session 降为不确定状态。

`rf_source.modulation_disable` 单独关闭一个已明确识别的 AM／FM／PM 模式和全局调制开关。它要求 RF OFF、Pulse／Sweep disabled、无活动 protection，且调制状态只包含请求模式；写后必须重新确认所有模式和全局调制均关闭。已一致关闭的状态不写入；混合、未知或矛盾状态在写入前拒绝。A4 调制与 A4-MO 清理均已验证此事务，因此 DSG830 production descriptor 已开放它，并由 `wavebench rf-source modulation disable --port PORT_ID --modulation-kind am|fm|pm` 和 `rf_source.modulation_disable` 提供日常入口。

DSG830 的 A4 调制证据已将 `rf_source.modulation_configure` 加入 production descriptor。production profile 为 AM `0–100 %`、FM `0.1 Hz–1 MHz`，以及 PM 精确 `1.25 rad`；三种模式的内部频率均为 `10 Hz–100 kHz`。driver 的离线 PM 映射范围不自动扩大 production profile。
当前 M2 的 RF ON 合同仍要求调制 disabled，因此 M3 capability 不授权在调制开启时输出 RF；M3-MO 为固定 profile 提供专门的输出安全合同，且已由独立 A4-MO 实机证据提升。

DSG830 源码 checkout 提供 A4 的独立本地 harness 和资源无关 setup 模板。一次运行只配置一个内部 Sine AM／FM／PM profile；成功路径在配置读回后执行同一模式的受限关闭事务，最终 snapshot 必须同时确认 RF OFF 和调制关闭。三种模式的 RF-OFF 配置、严格读回与关闭恢复均已通过。为使生产 profile 与 PM 的严格读回证据完全一致，PM 仅开放 `1.25 rad`，不将离线映射的其它值外推到 production。`--recover` 只用于把已知的单一活动模式恢复为关闭状态，输出为私有恢复记录，不是新的 capability 提升证据。两条路径都不读取 scope、不调用 RF output，也不做 output recovery。该证据不能外推为调制输出、CH2 信号、Pulse、Sweep 或 trigger 证据。

### M3-MO：受限调制输出（A4-MO 已通过并提升）

Core 将调制开启时的 RF 输出建模为独立的 `rf_source.modulated_output_enable`，而不是放宽通用 `rf_source.output`。request 包含一个内部 Sine AM／FM／PM profile；preflight 要求 RF OFF、全局调制开启、唯一目标模式开启且完整 profile 与 request 精确相同，Pulse／Sweep disabled、protection 清晰、频率／功率／实际端接满足端口 safety 配置，并且 request 被 `RfModulatedOutputProfile` 的窄范围接受。它读取 RF snapshot 和完整调制 snapshot，单次 ON 后再次读取二者；不会配置调制，也不会在成功后自动 RF OFF 或关闭调制。

任何 ON 结果不明、RF readback 或调制 readback 不符时，都不重试 ON。只有 session health 允许时，Core 才复用 M2 的一次受 guard RF OFF recovery；恢复后不推断调制状态。普通 `rf_source.output` 的 ON preflight 不变，仍要求调制关闭。

DSG830 源码 checkout 提供 `tools/a4_modulated_output_evidence.py` 与无资源 setup 模板。它使用仅在内存中创建的 descriptor，固定为 AM `50 %`／内部 `1 kHz`、RF `1 MHz`／`-50 dBm`，并只读取 CH2 的当前 `DEF` 缓冲区。CH2 必须由 setup 显式确认 50 Ω；scope 只判定是否有可见信号，不计算 dBm、频率或调制深度。工具不读取或控制 CH1，不把 LF OUTPUT 解释为调制测量，不使用 trigger／sync／后面板 Pulse I/O。受控序列已通过：154 次 RF query、12 次完成 write、CH2 信号存在、最终 RF OFF／调制关闭、两个 session 健康关闭，脱敏记录为 `0600`。因此 production descriptor 仅声明相同 AM `50 %`／`1 kHz`、最大 `-50 dBm` 的 capability；historical harness 会拒绝重跑。

## M4：Pulse 与 Step Sweep

M4 当前先完成 Pulse，再处理 frequency-only Step Sweep。`RfPortSnapshot` 中的 Pulse、Sweep 状态必须可区分，不能将外部 trigger、后面板辅助输出或设备私有模式默认为安全。

Pulse 只覆盖 `rf_out` 的 internal／single 子集。request 只包含 period、width 和 polarity；Core 在写入前要求 RF 输出、调制、Pulse、Sweep 均关闭且无活动 protection，写后独立读回 internal／single、全部请求字段和 Pulse 关闭状态。driver 的固定 sequence 以 `:PULM:STAT OFF` 收尾。它不控制 `:PULM:OUT`、RF 输出或任何 trigger，失败时不重试、不追加恢复 setter。

源码 checkout 的 `tools/a4_pulse_evidence.py` 与资源无关的 setup 模板完成了 A4 Pulse 受控验收。静态预检要求独立 `read_only` RF 配置、关闭读重试、精确的 production descriptor 和 50 Ω 端接声明；显式 `--execute` 才在内存中建立临时 `read_write` descriptor。两种已声明 polarity 都通过初始 snapshot、一次 Pulse 配置、独立配置读回和最终 snapshot，均为 38 次 query、6 次配置 write，并确认 RF 与 Pulse 仍关闭。`--diagnose` 保持 `read_only`，固定 22 次 query、零 write。两种模式都不读取 scope、不使用 CH1／CH2、不发送 trigger，证据以 `0600` 保存且不包含资源或原始响应。证据复核后，DSG830 production descriptor 已声明 `rf_source.pulse_configure`；historical harness 现在会拒绝重跑。

frequency-only Step Sweep 的生产子集只接受起止频率、点数和驻留时间，静态 profile 固定为 `STEP`／`FWD`／`RAMP`／`LIN`；Service 在写前和写后要求 RF 输出、调制、Pulse、Sweep 均关闭且无活动 protection，写后独立读取完整 Sweep profile 并要求状态仍为 disabled。DSG830 driver 只查询／写入 Step Sweep profile，并固定以 `:SWE:STAT OFF` 收尾；不发送 `:SWE:EXEC`、`*TRG`、任意 `:TRIG:*`、`:SWE:STAT FREQ`、Level Sweep、list、RF 输出或后面板接口命令。A4 Step Sweep 证据通过后，DSG830 production descriptor 声明 `rf_source.sweep_configure`；普通 CLI 和 run step 仍要求 `read_write`、profile 匹配和 fresh OFF-only preflight。

源码 checkout 的 `tools/a4_step_sweep_evidence.py` 与资源无关 setup 模板已完成离线回归和实机验收。静态预检要求独立 `read_only` RF 配置、关闭读重试、精确 production descriptor 和人工确认的 50 Ω 端接。`--diagnose` 保持 `read_only`，读取初始／最终 RF snapshot 与完整 Step Sweep profile，成功路径固定为 25 次 query、零 write；显式 `--execute` 才在内存中建立受限 `read_write` descriptor，成功路径固定为初始 snapshot、一次配置、独立 profile readback、最终 snapshot，共 41 次 query、9 条配置 write。两条路径都不读取 scope、不调用 RF output、不执行 arm／fire／trigger，且证据以 `0600` 保存。诊断与受控配置序列均通过，最终独立复核 RF 输出、调制、Pulse、Sweep 关闭且无活动 protection；historical harness 在 capability 提升后拒绝重跑。

Pulse trigger、Sweep arm／fire／stop、外部 trigger、后面板辅助输出、参考时钟和同步仍未实现。fake descriptor 可以覆盖后续 trigger／fire 事务；production descriptor 只有在 A4 或 A5 对应证据具备后才能声明相关 capability。

## A1–A5：实机证据门

| 证据 | 范围 | 可以提升的 production capability |
| --- | --- | --- |
| A1 | 只读 snapshot | `rf_source.snapshot` |
| A2 | RF OFF/ON、readback 与最终 OFF | `rf_source.output` |
| A3 | CW 环回、频率与 dBm 功率 | `rf_source.cw_configure` |
| A4 | RF-OFF 调制、Pulse、Step Sweep | 对应 M3／M4 capability |
| A4-MO | 固定调制 profile 的 RF ON、CH2 存在性观察与最终清理 | `rf_source.modulated_output_enable` |
| A5 | 外部 trigger 或同步接线 | trigger／fire／同步相关 capability |

每次证据记录必须独立于代码提交，且不能包含真实资源地址、序列号、原始响应或实验室专用配置。未恢复或无法确认最终 RF OFF 的验收不能用于提升 capability。

### A5-0：逻辑 trigger configuration 读取（离线完成）

A5-0 是物理 A5 之前的只读基础，不验证外部 trigger／同步接线。Core 已增加 `RfTriggerProfile` 和 `RfTriggerSnapshot`，以封闭 enum 表示 Pulse trigger mode、external trigger edge、external gate polarity、Sweep mode、Sweep period trigger 与 Sweep point trigger。`rf_source.trigger_snapshot`、`wavebench rf-source trigger status --port PORT_ID` 与 `rf_source.trigger_status` 都要求目标端口的 `TRIGGER / READ` profile；操作为 `stateful_read`，不读取普通 RF snapshot、不写入、不触发且不做 recovery。

DSG830 driver 已用六条固定 query 读取该逻辑 configuration，并对别名和未知响应执行严格解析。production descriptor 仍不声明 `rf_source.trigger_snapshot` 或 `TRIGGER` feature；因此普通 DSG830 配置会在 session 建立前拒绝该入口。`port_id` 只表示这些设置影响的 RF 输出，不表示物理 trigger／sync connector，也不从 CH2 的 50 Ω 或 `rf_out` 的 dBm 参考推导电气条件。

### A5：外部 trigger／同步的进入条件（物理验收未开始）

A5 从已核对的物理接口开始，不从某条 SCPI 命令或已有 `rf_out` 证据开始。一次验收只能覆盖一个明确目标，例如 Pulse 的 external trigger、Sweep period trigger 或 Sweep point trigger；不能把其中一项外推为其它 trigger、fire、同步或后面板辅助输出能力。

| 项目 | 进入前必须明确的事实 |
| --- | --- |
| 目标行为 | 本次只验证的 trigger／sync 模式、目标仪器状态和成功判据。 |
| 物理接线 | 每根线的源端设备／接口、目标设备／接口、线缆与转接件；必须明确是 trigger input、trigger output、sync/reference input、sync/reference output 还是 `rf_out`。 |
| 电气兼容 | 信号类型、方向、逻辑或模拟属性、幅度／阈值、极性、脉宽、频率／时序、源／负载阻抗和终端方式，均以已核对的设备资料和实际接线为准。 |
| 初始与恢复状态 | 初始 RF 输出、调制、Pulse、Sweep、protection 与后面板配置；失败后的恢复方式和最终 RF OFF 独立确认方式。 |
| 观察方式 | 如使用示波器，只能作为补充观察；必须核对其输入与接线，且不能替代仪器端读回。CH2 的 50 Ω 声明仅适用于已确认的 RF 路径。 |

在上述事实未明确前，不写入后面板配置，不发送 `*TRG`、`:TRIG:*`、`:SWE:EXEC` 或 `:PULM:OUT`，不切换 RF 输出，也不把外部接口视为安全。A5 的推荐开发顺序为：先完成 A5-0 的逻辑 configuration readback、Core profile／artifact、DSG830 严格 parser、fake transport 零写回归，以及保持原始 `read_only` 配置的私有零写 harness；隔离诊断已完成 22 次 query、零 write、最终 RF OFF 和健康关闭复核；最后在已确认接线和电气边界下，对一个明确的物理路径设计独立受控证据。production descriptor 仍须等待该证据逐项提升。

### A1：已完成的只读 snapshot 验收

A1 已使用一次性、非 production 的本地 evidence harness 完成并经复核。当时没有临时将
`rf_source.snapshot` 加入 production descriptor，也没有通过 `rf-source status` 绕过 capability 门禁。验收期间，production descriptor 始终保持仅 `rf_source.idn`。

1. 使用独立的本地 TOML 副本，不修改现有 `wavebench.toml`。副本中的 `[rf_source]` 必须指定已核对的
   canonical driver、资源和 `access = "read_only"`；不得使用 `read_write` 或资源猜测。若需网络发现，必须在 harness 之外以有界、单独授权的流程完成，人工复核后才写入副本，且发现结果不进入证据。
2. harness 通过受 guard 的 transport 和独占 resource lease 创建单个 session，只调用手册已审计的 snapshot
   query，且不添加 query 重试：`*IDN?`、`:FREQ?`、`:LEV?`、`:OUTP?`、`:MOD:STAT?`、`:PULM:STAT?`、
   `:SWE:STAT?`、`:STAT:QUES:POW:COND?`。
3. 禁止 `*RST`、错误队列、RF OFF/ON、频率／功率／调制／Pulse／Sweep setter、trigger、capture 和任何
   `write`／`write_bytes`。A1 失败时不在该只读流程中尝试 recovery OFF。
4. 本地证据只保存 A1 标签、时间、Core／插件版本、canonical driver、脱敏的型号／固件／选件信息、`rf_out`
   的类型化 snapshot、已人工确认的实际端接、session 结果和 guard audit 摘要。隔离 TOML 的
   `[a1_evidence]` 必须显式记录端口、有限正数端接和已确认的选件列表；firmware 从同一次 `*IDN?` 的
   受限字段提取，不新增查询。不得从连接器标签、scope coupling 或型号名称推导端接，也不得保存资源、
   序列号、完整 IDN、原始响应或命令日志。
5. 成功条件为：parser 完整成功、目标 RF 输出明确为 OFF、session 健康且关闭成功、audit 显示
   `access=read_only`、query 数量与预期一致、所有写计数和 `instrument_mutation_writes` 均为零。输出为 ON、
   状态未知、保护／解析异常、session 异常或关闭失败均为未通过，不能提升 capability。

本次证据已由人工复核，并在对应插件补丁中把 `rf_source.snapshot` 加入 DSG830 production descriptor。该提升不包含任何 RF 写 capability。

### A2：已完成的受控 RF 输出验收

A2 使用一次性、非 production 的本地 evidence harness 完成并经复核。验收前使用有界网络发现确定候选资源，随后只在隔离 TOML 中使用已复核的 RF 和 scope 资源；发现结果、资源地址、序列号、原始响应、命令与波形均不进入证据。RF 配置与 scope 配置在静态预检阶段均保持 `read_only`，读重试关闭；只有显式执行阶段才在内存中创建受限的 write session。

主序列确认初始 RF OFF、一次 RF ON、独立 readback、一次 RF OFF 和独立 readback。启用前的 fresh snapshot 同时验证端口安全配置、实际端接、频率、功率、调制、Pulse、Sweep 和 protection。若 ON 的结果不明，Core 最多执行一次受授权的 OFF recovery；若 OFF transaction 已开始但结果不明，不重试。验收记录确认最终 RF OFF、关闭成功和无结果不明的 guard audit。scope 对 CH1／CH2 的当前缓冲区观察作为补充，CH2 的 50 Ω 输入由独立配置明确确认，不替代 RF readback。

该证据已将 DSG830 的 `rf_source.output` 加入 production descriptor。普通 CLI 和 run step 仍必须使用 `read_write`、完整端口 safety 配置和 fresh preflight；A2 本身不提升 CW，后者由后续 A3 单独验证。

### A3：已完成的 CW 环回验收

DSG830 插件源码 checkout 保留本地 `tools/a3_cw_evidence.py`、回归测试和不含资源地址的 setup 模板。它不进入
wheel 或 sdist。受控序列已通过并经复核，production descriptor 现在声明 `rf_source.cw_configure`；historical harness 会以
`production_cw_gate_changed` 拒绝重跑。

静态预检要求 RF 与 scope 配置保持 `read_only`、读重试关闭且资源不同。显式 `--execute` 后，harness 才在内存中建立
只包含一个频点、一个低功率上限和实际端接声明的 write 配置。主序列为：初始 RF OFF snapshot、一次频率写入及独立
readback、一次功率写入及独立 readback、一次由已验证 M2 能力执行的 RF ON/OFF、CH2 当前 `DEF` 缓冲区读取，以及最终
RF OFF 的独立 readback。未确认最终 OFF、任一 CW readback 不符或 CH2 未观察到可见信号都会使 A3 失败。

CH2 的 50 Ω 端接是在 setup 中明确声明的电气安全前提。scope 只提供「可见信号」补充证据，不进行 dBm 与 Vpp 换算，
也不代替源端频率／功率回读。CH1 接入的低频辅助输出是独立端口，A3 未读取、未控制，也未从其观测推断 RF 输出状态或
通过条件。本次证据确认两项 CW 源端回读、CH2 可见信号、4 次完成写入、72 次查询、健康关闭和最终 RF OFF；频率与功率保留为 setup 指定的测试值。

## 推荐实施顺序

1. 已完成 Core M0 的 kind、descriptor、配置、只读 Service／CLI 与 run status 全链路。
2. 已在匹配的 Core `0.8.25` 开发线上迁移 DSG830 的 descriptor、依赖区间、topology 与 snapshot parser；正式 wheel 验收等待 Core 发布版本。
3. 已取得并复核 A1 的只读 snapshot 证据；DSG830 parser 已仅作为 `rf_source.snapshot` 暴露为 production capability。
4. 已完成 M1／M2 的 fake descriptor 零写拒绝、postcondition 测试、guarded OFF recovery、Core CLI／run 路由和 DSG830 离线 SCPI 映射；A2 已通过并仅提升 `rf_source.output`。
5. A3 已完成并将 `rf_source.cw_configure` 加入 production descriptor。M3 的 Core 合同、DSG830 映射、CLI、run、artifact 与 A4 证据均已完成，`rf_source.modulation_configure` 已提升；M4 继续保持独立工作。
6. A4 的 AM／FM／PM RF-OFF 单模式配置、严格读回与关闭恢复证据均已通过。PM production profile 固定为 `1.25 rad`，以避免将更宽的离线映射当作实机覆盖范围。源码 checkout 的 `--diagnose` 模式保留 `read_only` 配置，只读取初始／最终 RF snapshot 与指定模式 profile，并以零写审计保存私有诊断记录。该记录不构成新的 capability 提升证据；任何允许调制开启时 RF 输出的安全合同仍须单独设计和验证，CH2 可见信号也不能替代该证据。
7. 已完成 M4 frequency-only Step Sweep 的 Core／DSG830 合同、固定 SCPI 映射、CLI、run、artifact、fake 回归和独立 A4 证据。零写诊断与一次受控配置均已通过，production descriptor 已提升 `rf_source.sweep_configure`；后续只讨论未开放的 execute／fire、trigger、Level Sweep、list 或调制输出等独立范围。
8. 已完成 M3-MO 的 Core special capability、严格 transaction、CLI／run／artifact、公开按模式关闭入口、DSG830 固定 profile descriptor、CH2-only harness 与 fake 回归。已使用 WaveBench 有界网络发现确认候选设备，并完成只读诊断与固定 AM `50 %`／`1 kHz`、RF `1 MHz`／`-50 dBm` 的一次受控 A4-MO；CH2 信号存在、最终 RF OFF、调制关闭和健康关闭均已通过，production descriptor 已提升。CH1 的低频输出不属于该证据路径。后续进入 A5 前仍须确认唯一的物理接口、接线和电气边界。
