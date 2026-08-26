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
| 内部正弦 AM／FM／PM | M3 离线合同与受限恢复路径已完成 | 未开放 | A4 尚无覆盖三种模式的完整合格证据前，DSG830 会在 transport I/O 前拒绝该 capability。 |
| Pulse | M4 离线合同与受控 evidence 已完成 | A4 Pulse 后已开放 | 当前只限 internal／single 配置并强制保持 Pulse OFF；需要 `read_write` 与 fresh OFF-only preflight。 |
| frequency-only Step Sweep | M4 离线合同、CLI、run step 与 artifact 已完成 | 未开放 | 仅固定 `STEP`／`FWD`／`RAMP`／`LIN`，配置后 Sweep 仍保持关闭；当前 DSG830 会在 transport I/O 前拒绝该 capability。 |
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

在 production descriptor 已声明对应 capability、配置为 `read_write` 且所有 preflight 条件满足时，DSG830 可以执行受限的 CW 和 RF 输出操作：

```bash
wavebench rf-source set-frequency --config wavebench.toml --port rf_out 1000000
wavebench rf-source set-power --config wavebench.toml --port rf_out -40
wavebench rf-source output --config wavebench.toml --port rf_out on
wavebench rf-source output --config wavebench.toml --port rf_out off
wavebench rf-source pulse configure --config wavebench.toml --port rf_out --period-s 0.001 --width-s 0.0001 --polarity normal
```

`output on` 不是普通 setter。它会在写入前重新读取 RF 状态，确认频率、功率、实际端接、调制、Pulse、Sweep 和 protection 均满足安全合同。任何关键状态缺失或不一致都会在 ON 前拒绝；不应依赖先前一次成功查询。

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
```

`rf_source.sweep_configure` 已进入 schema，但当前只用于离线 fake descriptor、开发验证或未来取得专项证据的插件：

```toml
[[steps]]
kind = "rf_source.sweep_configure"
port_id = "rf_out"
start_frequency_hz = 1000000
stop_frequency_hz = 2000000
points = 11
dwell_s = 0.02
```

它只配置 frequency-only Step Sweep，不会 arm、fire、触发、执行 `SWE:EXEC`、切换 RF 输出或配置 Level Sweep。当前 DSG830 production descriptor 未声明 `rf_source.sweep_configure`，因此不应将这段 plan 用于该设备的日常上机操作。

先运行 `wavebench run check`，再运行只读的 `wavebench run verify`。只有在接线、端接、输出状态和设备身份均已复核时，才执行 `wavebench run plan`。运行计划不会把普通 source 的 restore 或 Vpp safety 规则套用到 RF 端口。

## M3：内部正弦调制合同

Core 已提供三条 M3 命令和一个 run step：

```text
wavebench rf-source modulation configure-am ...
wavebench rf-source modulation configure-fm ...
wavebench rf-source modulation configure-pm ...
rf_source.modulation_configure
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

截至当前，DSG830 production descriptor 不声明 `rf_source.modulation_configure`。因此上述命令和 step 仅用于离线 fake descriptor、开发验证或未来已取得 A4 证据的插件；对当前 production DSG830 会在打开 transport 前被 capability 门拒绝。

DSG830 源码 checkout 的 A4 harness 是开发验证工具，不是日常命令。它一次配置一个内部 Sine 模式，完成读回后立即执行同一模式的受限关闭事务，并在最终 snapshot 中确认 RF 输出与调制均已关闭。显式 `--recover` 只用于恢复「已明确识别的单一活动模式」，输出为私有恢复记录；两条路径都不读取 CH2、不调用 RF output，也不能改变 production capability。显式 `--diagnose` 保留原始 `read_only` 配置，只读取初始／最终 RF snapshot 与指定模式 profile，并要求 transport audit 为零写；它只生成私有诊断记录。A4 的 AM、FM RF-OFF 序列已通过；PM 仍有严格读回不匹配，故整体调制 capability 尚未提升。

M2 的 RF ON 合同目前要求调制 disabled。即使未来 A4 仅提升 M3 配置 capability，也不能据此推导「已可在调制开启时输出 RF」。允许调制输出需要单独调整输出 safety 合同并取得相应实机证据；不得通过关闭门禁或原始 SCPI 先行绕过。

## M4：受控 Pulse 与 Step Sweep 配置合同

Core 已提供下列离线入口：

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

这不是 production 使用授权。DSG830 源码 checkout 已提供 `tools/a4_step_sweep_evidence.py` 和无资源 setup 模板：`--diagnose` 保持 `read_only`，固定 25 次查询、零写入；显式 `--execute` 才允许一次受审计的配置，成功路径固定为 41 次查询、9 条 Step Sweep 配置写入。两条路径都不读取 Scope、不操作 RF output、arm、fire 或 trigger。该工具已通过 fake 回归，但尚无实机证据。DSG830 production descriptor 仍未声明 `rf_source.sweep_configure`，当前 CLI 和 run plan 对该设备会在打开 transport 前被 capability 门拒绝；除专项 evidence harness 的明示授权外，Step Sweep 不通过普通 CLI 或 run plan 上机。CH2 的 50 Ω 端接不改变这一边界。

## 上机前检查清单

1. 使用网络发现和只读身份查询确认候选设备，再在隔离配置中复核资源与型号。
2. 从 `read_only` 开始；只有本次确实需要、且 production descriptor 已声明的操作才使用 `read_write`。
3. 核对 `rf_out` 的实际端接、频率范围和功率上限。示波器的 CH2 50 Ω 输入不能替代整条路径核对。
4. 在任何写入前读取 RF snapshot，确认 RF 输出 OFF；完成后独立确认最终 RF OFF。
5. 日常 M3／M4 操作不使用 raw SCPI，不执行 reset、preset、错误队列、外部调制、后面板 Pulse I/O、未获证据的 Step Sweep、trigger 或 scope 自动量程。A4 Pulse 仅可通过专用受控 harness 执行。

需要实现新型号或提升 capability 时，继续阅读 [RF 信号源领域设计](../design/WaveBench_RF信号源设计.md)、[RF 信号源开发里程碑](../design/WaveBench_RF信号源开发里程碑.md) 和对应插件的型号级里程碑。
