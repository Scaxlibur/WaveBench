# WaveBench RF 信号源使用指南

[RF 信号源领域设计](../design/WaveBench_RF信号源设计.md) · [RF 信号源开发里程碑](../design/WaveBench_RF信号源开发里程碑.md) · [run plan 使用指南](WaveBench_run_plan_使用指南.md)

本页说明 RF 信号源的当前使用入口、配置边界和已知限制。它面向实际使用与 plan 编写；领域模型、SCPI 映射和实机验收记录见设计文档与型号插件文档。

## 先区分两类信号源

`source` 面向函数／任意波形发生器，使用数字 channel、Vpp、offset 和波形模型。`rf_source` 面向以频率、dBm 功率等级、RF 输出和调制状态为主要对象的射频信号源。

二者不能互换：

- 不把 RF 的 dBm 换算为 `source` 的 Vpp。
- 不把 `rf_out` 当成普通 source channel。
- 不把 scope 的输入阻抗、连接器标签或型号名称当成 RF 端口的实际端接声明。
- 不通过普通 `source.*` 操作、原始 SCPI 或临时 descriptor 绕过 `rf_source` 的 capability 与 safety 门。

## 当前可用范围

| 操作 | Core 状态 | DSG830 production descriptor | 关键边界 |
| --- | --- | --- | --- |
| 身份与状态 | 已开放 | 已开放 | `read_only` 可执行。 |
| CW 频率／dBm 功率 | 已开放 | A3 后已开放 | 仅目标 RF 输出明确 OFF 时的单字段写入。 |
| RF ON/OFF | 已开放 | A2 后已开放 | ON 需要完整端口 safety 配置与 fresh preflight。 |
| 内部正弦 AM／FM／PM | 已开放 | A4 后已开放 | 只在 RF OFF 下配置。AM 为 `0–100 %`，FM 为 `0.1 Hz–1 MHz`，PM 的 production profile 精确为 `1.25 rad`；三种模式的内部频率均为 `10 Hz–100 kHz`。 |
| 按模式关闭调制 | 已开放 | A4 后已开放 | RF OFF、Pulse／Sweep disabled 且唯一目标模式活动时才写入；已一致关闭时零写返回。 |
| 受限调制输出 | A4-MO 后已开放 | A4-MO 后已开放 | 仅 AM `50 %`／内部 `1 kHz`、最大 `-50 dBm`。它要求 profile 已激活且精确匹配，不配置或关闭调制；普通 `rf_source.output on` 仍要求调制关闭。 |
| Pulse | M4 离线合同与受控 evidence 已完成 | A4 Pulse 后已开放 | 当前只限 internal／single 配置并强制保持 Pulse OFF；需要 `read_write` 与 fresh OFF-only preflight。 |
| frequency-only Step Sweep | M4 合同、CLI、run step、artifact 与 A4 证据已完成 | A4 Step Sweep 后已开放 | 仅固定 `STEP`／`FWD`／`RAMP`／`LIN`，配置后 Sweep 仍保持关闭；需要 `read_write`、匹配 profile 与 fresh OFF-only preflight。 |
| 后面板 Pulse Output | A5 合同、CLI、run step、artifact 与受控 evidence 已完成 | A5 Pulse Output 后已开放 | 仅 `rf_out` 的 `pulse_in_out` output 方向；固定 `0 V`／`3.3 V`、约 `600 Ω`、internal／single／normal／`1 ms`／`100 μs`。它不启用 RF 输出，也不配置接收设备。 |
| 逻辑 trigger configuration 读取 | A5-0 离线合同完成 | 未开放 | `rf-source trigger status`／`rf_source.trigger_status` 需要独立 capability 和 `TRIGGER / READ` profile；当前 DSG830 production descriptor 会拒绝该请求。它不读取或配置物理 trigger／sync 接口。 |
| trigger、arm／fire、Level Sweep | 未完成 | 未开放 | 不应尝试调用或绕过。 |

生产 descriptor 是否声明 capability 是实际边界。Core 中存在 CLI、run step 或 driver 方法，不等于当前仪器已经获准执行该操作。

## 配置与端接声明

RF 使用独立的 `[rf_source]` 段。日常身份查询和状态读取保持 `read_only`：

```toml
[rf_source]
driver = "rigol.dsg830"
resource = "<reviewed-resource>"
access = "read_only"
```

CW 写入或 RF 输出控制需要显式改为 `read_write`，并为每个使用的端口提供 safety 配置：

```toml
[rf_source]
driver = "rigol.dsg830"
resource = "<reviewed-resource>"
access = "read_write"

[[rf_source.safety.ports]]
port_id = "rf_out"
minimum_frequency_hz = 9000
maximum_frequency_hz = 3000000000
maximum_power_dbm = -40
actual_termination_ohm = 50
```

示例中的 `50` 只适用于已人工确认整个 RF 路径确实以 50 Ω 端接的场景。若 RF 直接接入示波器，示波器 CH2 已设为 50 Ω 只是必要信息之一；线缆、转接件、分配器和实际连接路径也必须一起核对。无法确认时，不应填写猜测值。

网络发现只能帮助定位候选设备。先使用有界扫描，例如：

```bash
wavebench net discover \
  --subnet 192.0.2.0/24 \
  --ports 5025,5555,111 \
  --timeout-ms 500 \
  --workers 16 \
  --max-hosts 256 \
  --no-idn \
  --no-visa
```

候选资源仍须通过只读身份查询、型号核对和隔离配置复核；发现结果不自动写回配置，也不构成写入授权。VXI-11 设备可能只表现为候选端口，未响应 socket `*IDN?` 不等于设备不可用。

## 当前生产操作

先使用只读入口确认状态：

```bash
wavebench rf-source idn --config wavebench.toml
wavebench rf-source status --config wavebench.toml
```

在 production descriptor 已声明对应 capability、配置为 `read_write` 且所有 preflight 条件满足时，DSG830 可以执行受限的 CW、内部正弦调制、RF 输出、Pulse、Pulse Output 和 Step Sweep 操作：

```bash
wavebench rf-source set-frequency --config wavebench.toml --port rf_out 1000000
wavebench rf-source set-power --config wavebench.toml --port rf_out -40
wavebench rf-source modulation configure-am --config wavebench.toml --port rf_out --depth-percent 25 --internal-frequency-hz 1000
wavebench rf-source modulation configure-fm --config wavebench.toml --port rf_out --frequency-deviation-hz 10000 --internal-frequency-hz 1000
wavebench rf-source modulation configure-pm --config wavebench.toml --port rf_out --phase-deviation-rad 1.25 --internal-frequency-hz 1000
wavebench rf-source modulation disable --config wavebench.toml --port rf_out --modulation-kind am
wavebench rf-source output --config wavebench.toml --port rf_out on
wavebench rf-source output --config wavebench.toml --port rf_out off
wavebench rf-source pulse configure --config wavebench.toml --port rf_out --period-s 0.001 --width-s 0.0001 --polarity normal
wavebench rf-source pulse-output --config wavebench.toml --port rf_out --interface pulse_in_out on
wavebench rf-source pulse-output --config wavebench.toml --port rf_out --interface pulse_in_out off
wavebench rf-source sweep configure --config wavebench.toml --port rf_out --start-frequency-hz 1000000 --stop-frequency-hz 2000000 --points 11 --dwell-s 0.02
```

`output on` 不是普通 setter。它会在写入前重新读取 RF 状态，确认频率、功率、实际端接、调制、Pulse、Sweep 和 protection 均满足安全合同。任何关键状态缺失或不一致都会在 ON 前拒绝；不应依赖先前一次成功查询。

调制输出必须使用单独的受限序列，不能将已激活调制交给普通 `output on`：

```bash
wavebench rf-source modulation configure-am --config wavebench.toml --port rf_out --depth-percent 50 --internal-frequency-hz 1000
wavebench rf-source modulation enable-output-am --config wavebench.toml --port rf_out --depth-percent 50 --internal-frequency-hz 1000
wavebench rf-source output --config wavebench.toml --port rf_out off
wavebench rf-source modulation disable --config wavebench.toml --port rf_out --modulation-kind am
```

DSG830 目前只声明上述精确 AM `50 %`／`1 kHz`、最大 `-50 dBm` 的 `enable-output-am`。`enable-output-fm`、`enable-output-pm` 会因 profile 不匹配而在仪器 I/O 前拒绝。成功的特殊 ON 不会自动 RF OFF 或关闭调制；结束时必须显式执行普通 `output off` 和按模式 `disable`。不得用原始 SCPI、临时替换 production descriptor 或普通 `output on` 绕过该限制。

## A5：后面板 Pulse Output（受限生产操作）

`rf_source.pulse_output` 只覆盖 DSG830 的 `pulse_in_out` output 方向。它不是 `rf_source.pulse_configure` 的别名，也不表示接口的 input 方向、`TRIGGER IN`、Pulse trigger、Sweep fire、sync／reference 或 RTM 控制能力。

固定 profile 为 `0 V`／`3.3 V`、约 `600 Ω`、internal／single／normal、period `1 ms`、width `100 μs`。`on` 只在 RF 输出、调制、Pulse、Sweep 都关闭、protection 为空且 readback 与该 profile 精确匹配时执行一次状态写入；成功不会启用 RF 输出或改变示波器。`off` 仍只操作该接口，但故意允许已知 profile 漂移，以保留关闭已启用输出的路径。写入或读回结果不明时，不重试，session 会变为不确定状态。

已验证的接线仅为 DSG830「PULSE IN/OUT」output → RTM2032「EXT TRIGGER INPUT」。日常 CLI 或 run plan 不会检测接收设备、配置 scope trigger、发起 scope single 或恢复接收设备状态；使用前必须人工核对实际接线与接收端电气额定值。该操作不适用于从相似连接器名称推导出的其它路径。

## A5-0：逻辑 trigger configuration 读取

Core 已提供下列只读入口：

```text
wavebench rf-source trigger status --port PORT_ID
rf_source.trigger_status
```

它读取 Pulse trigger mode、external trigger edge、external gate polarity、Sweep mode、Sweep period trigger 与 Sweep point trigger 的封闭类型化状态。该操作是 `stateful_read`，不读取普通 RF snapshot，不发送 setter、RF 输出、`*TRG`、`:SWE:EXEC` 或后面板配置命令。

入口仍由 `rf_source.trigger_snapshot` capability 和目标端口的 `TRIGGER / READ` profile 门控。DSG830 当前 production descriptor 不声明该 capability，因此日常配置会在建立 session 前被拒绝。它只适用于非 production 的离线测试 descriptor 或源码 checkout 中的私有零写诊断；后者保持 `read_only`、禁用读重试，成功预算为 22 次 query 与零 write。`rf_out` 表示受这些配置影响的 RF 输出，不是物理 trigger／sync connector。外部 trigger、arm／fire、后面板接口和同步仍需明确物理接线、电气边界与 A5 证据。

## run plan 中的 RF 步骤

CW 和输出步骤使用独立的 `rf_source.*` kind，并把 evidence 写入 `run.json.rf_source_operations`：

```toml
[[steps]]
kind = "rf_source.set_frequency"
port_id = "rf_out"
frequency_hz = 1000000

[[steps]]
kind = "rf_source.set_power_dbm"
port_id = "rf_out"
power_dbm = -40

[[steps]]
kind = "rf_source.output_enable"
port_id = "rf_out"

[[steps]]
kind = "rf_source.output_disable"
port_id = "rf_out"

[[steps]]
kind = "rf_source.pulse_configure"
port_id = "rf_out"
period_s = 0.001
width_s = 0.0001
polarity = "normal"

[[steps]]
kind = "rf_source.pulse_output_enable"
port_id = "rf_out"
interface_id = "pulse_in_out"

[[steps]]
kind = "rf_source.pulse_output_disable"
port_id = "rf_out"
interface_id = "pulse_in_out"
```

`rf_source.sweep_configure` 已进入 schema，并在 DSG830 的 A4 Step Sweep 证据复核后成为受限生产操作：

```toml
[[steps]]
kind = "rf_source.sweep_configure"
port_id = "rf_out"
start_frequency_hz = 1000000
stop_frequency_hz = 2000000
points = 11
dwell_s = 0.02
```

它只配置 frequency-only Step Sweep，不会 arm、fire、触发、执行 `SWE:EXEC`、切换 RF 输出或配置 Level Sweep。DSG830 使用这段 plan 时仍须为 `read_write`，并通过 capability、profile 和 fresh OFF-only preflight；配置完成后必须读回 Sweep disabled。

`rf_source.modulation_disable` 也已进入 run schema；它只需要 `port_id` 与 `modulation_kind`：

```toml
[[steps]]
kind = "rf_source.modulation_disable"
port_id = "rf_out"
modulation_kind = "am"
```

`rf_source.modulated_output_enable` 同样已进入 production schema。它使用与 `rf_source.modulation_configure` 相同的 `port_id`、`modulation_kind`、内部频率和对应数值字段；不能把配置步骤和输出步骤合并，也不能假定 run plan 会在成功后自动关闭 RF 或调制。DSG830 的 production descriptor 只接受 AM `50 %`／`1 kHz`、最大 `-50 dBm` 的 profile。

先运行 `wavebench run check`，再运行只读的 `wavebench run verify`。只有在接线、端接、输出状态和设备身份均已复核时，才执行 `wavebench run plan`。运行计划不会把普通 source 的 restore 或 Vpp safety 规则套用到 RF 端口。

## M3：内部正弦调制合同

Core 已提供三条 M3 配置命令、一个按模式关闭命令和对应 run step：

```text
wavebench rf-source modulation configure-am ...
wavebench rf-source modulation configure-fm ...
wavebench rf-source modulation configure-pm ...
wavebench rf-source modulation disable --port PORT_ID --modulation-kind am|fm|pm
rf_source.modulation_configure
rf_source.modulation_disable
```

该合同只覆盖内部 Sine：

| 模式 | CLI 值字段 | run plan 值字段 | 单位 |
| --- | --- | --- | --- |
| AM | `--depth-percent` | `depth_percent` | percent |
| FM | `--frequency-deviation-hz` | `frequency_deviation_hz` | Hz |
| PM | `--phase-deviation-rad` | `phase_deviation_rad` | rad |

三种模式都必须提供 `--internal-frequency-hz` 或 `internal_frequency_hz`。run plan 使用 `modulation_kind = "am" | "fm" | "pm"`，而不是复用步骤自身的 `kind` 键；每个步骤只能出现与该模式匹配的一个值字段。

```toml
[[steps]]
kind = "rf_source.modulation_configure"
port_id = "rf_out"
modulation_kind = "am"
depth_percent = 25
internal_frequency_hz = 1000
```

M3 事务要求目标 RF 输出 OFF、AM／FM／PM 三种模式均处于 disabled、Pulse／Sweep disabled，且没有活动 protection condition。FM 与 PM 共享设备的当前选择位：在三种模式均关闭时，preflight 可以观察到另一种 FM／PM 选择，固定写入会明确选择目标类型；postcondition 必须确认已切换到目标类型。Core 用独立调制 snapshot 验证目标模式、源、波形、数值、内部频率、全局调制开关和 RF 输出仍然 OFF。写入结果不明或 postcondition 不匹配时不重试，session 会降为不确定状态。

DSG830 production descriptor 已声明 `rf_source.modulation_configure` 与 `rf_source.modulation_disable`。它只接受 descriptor 中的内部 Sine profile：AM `0–100 %`、FM `0.1 Hz–1 MHz`、PM 精确 `1.25 rad`，内部频率均为 `10 Hz–100 kHz`。关闭操作只在 RF OFF、Pulse／Sweep disabled、protection 清晰且请求模式是唯一活动模式时写入；已一致关闭时零写。超出 production profile 或状态不清晰的请求会在仪器 I/O 前拒绝；不能把 driver 的离线映射范围当作当前设备的写入授权。

DSG830 源码 checkout 的 A4 harness 是已完成的开发验收工具，不是日常命令。它一次配置一个内部 Sine 模式，完成读回后立即执行同一模式的受限关闭事务，并在最终 snapshot 中确认 RF 输出与调制均已关闭。显式 `--recover` 只用于恢复「已明确识别的单一活动模式」，输出为私有恢复记录；两条路径都不读取 CH2、不调用 RF output，也不能改变 production capability。显式 `--diagnose` 保留原始 `read_only` 配置，只读取初始／最终 RF snapshot 与指定模式 profile，并要求 transport audit 为零写；它只生成私有诊断记录。AM／FM／PM 的 RF-OFF 序列均已通过；PM 的 production profile 因严格读回证据而固定为 `1.25 rad`。

M2 的 RF ON 合同目前要求调制 disabled。M3 已提升的配置 capability 不能据此推导「已可在调制开启时输出 RF」。

### M3-MO：受限调制输出（A4-MO 已通过并提升）

Core 已提供下列专用入口：

```text
wavebench rf-source modulation enable-output-am ...
wavebench rf-source modulation enable-output-fm ...
wavebench rf-source modulation enable-output-pm ...
rf_source.modulated_output_enable
```

它们复用 M3 的 `modulation_kind`、数值字段和内部频率字段，但不会配置调制：调用前目标 profile 必须已经完整激活并与 request 精确一致。Core 还要求 RF 当前为 OFF、Pulse／Sweep disabled、protection 清晰、端口 safety 配置完整、实际端接与 dBm 参考阻抗一致，以及特殊 `RfModulatedOutputProfile` 明确允许这一 profile 与功率。成功路径只启用一次 RF；不会自动 RF OFF 或关闭调制。任何写入或 readback 不确定时不重试 ON，只可能执行一次受 guard 的 RF OFF recovery。

DSG830 production descriptor 仅声明 AM `50 %`／`1 kHz`、最大 `-50 dBm` 的 `rf_source.modulated_output_enable`。A4-MO 使用同一固定 profile、RF `1 MHz`／`-50 dBm` 完成一次受控循环：CH2 显式为 50 Ω，scope 只观察当前 `DEF` 缓冲区是否有可见信号，随后工具明确 RF OFF、关闭 AM 和全局调制并复核最终状态。CH1 的低频输出独立于 RF 调制路径，不被读取或当作证据；scope 也不用于推断 dBm、频率或调制深度。历史 harness 在 capability 提升后拒绝重跑，日常操作只能使用 production descriptor、`read_write`、完整 safety 配置和显式清理步骤。

## M4：受控 Pulse 与 Step Sweep 配置合同

Core 已提供下列生产入口；DSG830 已声明相应 capability：

```text
wavebench rf-source pulse configure --port PORT_ID --period-s SECONDS --width-s SECONDS --polarity normal|inverted
rf_source.pulse_configure
```

DSG830 production descriptor 已声明 `rf_source.pulse_configure`。普通 CLI 或 run plan 仍要求 `read_write`、目标 RF 输出／调制／Pulse／Sweep 关闭、无活动 protection，以及 descriptor 声明的 internal／single profile；任何条件不满足都会在写入前拒绝。

源码 checkout 的 `tools/a4_pulse_evidence.py` 是已完成的受控验收工具。它只接受 internal／single、period、width 和 polarity，并在每次配置后保持 Pulse OFF；初始、写后和最终状态都要求 RF 输出、调制、Pulse、Sweep 关闭且无活动 protection。它不调用 RF output、不使用后面板 Pulse I/O、不发送 trigger、不读取 CH1／CH2。`--diagnose` 保持 `read_only` 且零写，`--execute` 才允许一次受审计的配置写入。两种 polarity 的证据均通过并经复核，DSG830 已开放该 capability；historical harness 在提升后会拒绝重跑。

### frequency-only Step Sweep

Core 已提供下列离线入口：

```text
wavebench rf-source sweep configure --port PORT_ID --start-frequency-hz START --stop-frequency-hz STOP --points COUNT --dwell-s SECONDS
rf_source.sweep_configure
```

该子集固定为 `STEP`／`FWD`／`RAMP`／`LIN`，请求只包含起止频率、点数和驻留时间。写前与写后均要求 RF 输出、调制、Pulse、Sweep 关闭且无活动 protection；写后独立读回所有 profile 字段，并要求 Sweep 仍为 disabled。DSG830 driver 固定以 `:SWE:STAT OFF` 收尾，不会发送 `:SWE:EXEC`、任意 `:TRIG:*`、Level Sweep、RF 输出或后面板接口命令。

DSG830 源码 checkout 的 `tools/a4_step_sweep_evidence.py` 与无资源 setup 模板完成了专项实机验收：`--diagnose` 保持 `read_only`，固定 25 次查询、零写入；显式 `--execute` 才允许一次受审计的配置，成功路径固定为 41 次查询、9 条 Step Sweep 配置写入。两条路径都不读取 Scope、不操作 RF output、arm、fire 或 trigger。诊断与受控配置均通过，最终独立复核 RF 输出、调制、Pulse、Sweep 关闭且无活动 protection，因此 DSG830 production descriptor 声明 `rf_source.sweep_configure`。该生产范围仍只限配置且保持 Sweep disabled；CH2 的 50 Ω 端接不改变 execute、trigger 或 RF 输出的边界。

## 上机前检查清单

1. 使用网络发现和只读身份查询确认候选设备，再在隔离配置中复核资源与型号。
2. 从 `read_only` 开始；只有本次确实需要、且 production descriptor 已声明的操作才使用 `read_write`。
3. 核对 `rf_out` 的实际端接、频率范围和功率上限。示波器的 CH2 50 Ω 输入不能替代整条路径核对。
4. 在任何写入前读取 RF snapshot，确认 RF 输出 OFF；完成后独立确认最终 RF OFF。
5. 日常 M3／M4／A5 操作不使用 raw SCPI，不执行 reset、preset、错误队列、外部调制、未声明的后面板接口、Step Sweep execute、trigger 或 scope 自动量程。Pulse 与 Step Sweep 只使用 descriptor 已声明的受限配置入口；A5 只使用已声明的 `pulse_in_out` output 路径；M3-MO 只能使用 DSG830 已声明的固定 AM profile，并在结束时显式 RF OFF 与按模式关闭调制。

需要实现新型号或提升 capability 时，继续阅读 [RF 信号源领域设计](../design/WaveBench_RF信号源设计.md)、[RF 信号源开发里程碑](../design/WaveBench_RF信号源开发里程碑.md) 和对应插件的型号级里程碑。
