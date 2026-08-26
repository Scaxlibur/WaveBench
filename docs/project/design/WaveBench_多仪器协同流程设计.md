# WaveBench 多仪器流程设计

## 背景

WaveBench 现在已经有三类仪器的显式命令：

```text
RTM2032 scope: capture/fetch/idn/errors/auto
DG4202 source: idn/status/set-freq/set-func/set-vpp/output
DP800 power : idn/status/set/output
```

下一步自然会想到自动实验流程，例如：

```text
设置电源电压 -> 设置信号源 -> 示波器采集 -> 写 summary -> 下一个条件
```

流程设计需要保持克制。多仪器流程不是把几个命令简单拼接起来：电源输出、示波器输入阻抗和信号源输出状态都会影响真实硬件。因此，流程层必须显式记录每个会改变实验台状态的动作。

## 目标

第一版流程层只做一件事：**把已经存在的显式 CLI/service 能力组合成可复现实验步骤**。

它应该：

- 明确每一步会读什么、写什么；
- 每个会改变仪器状态的动作都要在流程中出现；
- 每次采集继续生成普通 acquisition package；
- 额外生成一个流程级 summary；
- 出错时记录已完成步骤，不伪装成完整成功。

它不应该：

- 默认打开电源输出；
- 默认切换示波器输入阻抗；
- 默认 `*RST`；
- 自动恢复一堆没有正式设计过的状态；
- 变成通用 LabVIEW 替代品。

## CLI 形态

建议第一版命令：

```text
wavebench run plan --config wavebench.toml --plan plans/dp800_scope_probe_voltage_steps.toml
```

可选命令：

```text
wavebench run check  --config wavebench.toml --plan plans/dp800_scope_probe_voltage_steps.toml
wavebench run verify --config wavebench.toml --plan plans/dp800_scope_probe_voltage_steps.toml
```

其中：

- `run check`：只解析计划并打印摘要，不连接仪器；
- `run verify`：只读查询计划涉及的仪器资源、`*IDN?` 和显式 safety guard，用于执行前预检；
- `run plan`：执行计划。

`run` 是独立 domain，不塞进 `scope` / `source` / `power`。

## 资源租约

`run plan` 在打开任何仪器 session 前，会为计划涉及的 `scope`、`source`、`rf_source`、`power` 和
`dmm` 资源按规范化资源键排序，并一次性取得本机独占租约。任一资源已被
其他进程占用时，后续 transport 不会打开，已取得的前置租约会全部释放，并返回稳定错误码
`resource_busy`。

租约使用统一的跨平台文件锁后端：Linux / WSL 使用 POSIX `flock`，原生 Windows 使用
`portalocker[win32]` 的 Win32 `LockFileEx`。锁目录优先读取环境变量 `WAVEBENCH_LEASE_DIR`；
Linux / WSL 其次使用 `XDG_RUNTIME_DIR` 或用户缓存目录，Windows 默认使用
`%LOCALAPPDATA%\WaveBench\resource-leases-v1`。锁文件名只包含资源身份的哈希，不写入 IP
或串口路径；holder 信息保存在同名 JSON sidecar 中。正常释放只删除当前 lease 的 sidecar，
不删除 `.lock` 文件。进程崩溃后，内核负责释放锁；残留 sidecar 只能在再次取得非阻塞锁后
清理，不能根据 PID 删除活动锁。

`run check`、离线报告和比较命令不会取得资源租约。run 使用 factory 打开的 transport、独立
Service 的一次性 session、`doctor`、网络 discovery 和声明式 SCPI probe 均在实际 I/O 前取得同一
类本地独占租约；离线命令仍不接触仪器。租约只覆盖当前运行环境、本机可信文件系统上的
WaveBench 文件锁，不能替代外部程序或真实设备内部的互斥机制。原生 Windows 与 WSL 之间
不承诺锁互操作；同一台仪器应固定由一种运行环境访问。

可以使用 `lock status <resource>` 查询锁是否被持有、是否存在残留 sidecar；该命令只读取锁状态，
不会取得租约，也不会连接仪器。

## run 内状态守卫

`run plan` 对 SourceService 和 PowerService 的基础控制写入启用 compare-before-write。每个受保护
通道先读取一次结构化状态，与当前 run 保存的 expected state 按字段比较；发现外部或仪器自身的
状态变化时，写入操作立即失败，底层 setter 不会被调用。

当前保护范围包括：

- source：`source.set_freq`、`source.set_func`、`source.set_vpp`、`source.set_duty`、`source.output`
  以及 Service 层的任意波形上传；
- power：`power.set`、`power.output`。

比较字段只包含控制状态，例如输出状态、函数、频率、幅度、偏置、相位、模式和设定值。实时
测量值不参与比较。写入成功后，expected state 使用写后回读的结构化状态更新；首次观察某个通道
时只建立基线，不凭空判定漂移。数值字段使用固定容差，避免仪器分辨率造成误报。

状态不一致会使用稳定错误码 `state_drift`，并在 run 工件的 `error.details` 中保存
`expected`、`actual` 和 `diff`。`run.json.provenance.state_guard` 同时保存最终 expected state，
便于复核执行过程中记录的控制状态。

授权的 OFF 操作可以从漂移状态收敛，用于安全门和恢复路径；该操作仍受 `access` 策略、能力检查
和资源租约约束，并记录实际回读结果。状态守卫是查询后写入的保护，不是硬件原子
compare-and-swap；前面板或其他进程仍可能在查询与写入之间改变状态，因此文档和报告不能将其
描述为原子事务。当前实现只覆盖上述 Source/Power 基础控制，不代表调制、扫描、保护参数、触发
等全部 Service 写操作都已纳入状态守卫。

## scope status 与能力解释

`scope status` 默认优先返回完整 `scope.snapshot`。当驱动没有该能力时，命令仍可使用基础只读
能力返回 `status=partial`，并列出缺少的 capability；当前内建 RTM2032 路径至少提供 `*IDN?` 和
通道耦合信息。需要完整字段时使用 `scope status --strict`，缺少 `scope.snapshot` 会以非零状态
失败，不使用伪造的默认值。

`capability explain <operation>` 只读取本地 registry、驱动 descriptor 和配置，不打开 transport。
结果会说明操作的 effect、租约模式、风险字段、缺少的 capability、访问策略是否拒绝，以及可用的
安全替代操作。例如：

```text
wavebench capability explain source.output --driver dg4202
wavebench --json capability explain source.output --config wavebench.toml
```

本地候选只用于解释和过滤，不会触发插件安装、网络下载或仪器连接。

## execution intent

可在打开仪器前生成规范化执行意图：

```text
wavebench run intent --config wavebench.toml --plan plans/example.toml \
  --output data/intents/example.json
wavebench run plan --config wavebench.toml --plan plans/example.toml \
  --intent data/intents/example.json
```

意图文件使用 `wavebench.execution_intent.v1`，保存 plan digest、脱敏后的 config digest、任意波形
payload 的 SHA-256 和每个 step 对应的 `OperationSpec`。`run plan --intent` 会在取得资源租约前重新
计算摘要；计划、配置或 payload 改变时返回 `execution_intent_mismatch`，不会打开仪器 session。没有
外部意图文件时，run 仍会生成同一意图并写入 `run.json.provenance.execution_intent`。

非交互命令可在命令行任意位置使用 `--json`，输出一个 `wavebench.cli.result.v1` 结果；错误使用
`wavebench.error.v1`，诊断文本写入 stderr。TUI 和 HTTP MCP 服务不套用这一 one-shot JSON 包装。

## 计划文件格式

第一版建议用 TOML，原因是项目已经使用 TOML，用户不用再学一种新格式。

示例：

```toml
[experiment]
name = "dp800_scope_probe_voltage_capture"
label = "dp800_scope_probe_voltage_capture"

[safety]
require_scope_coupling_not = ["DC"]
scope_guard_channel = 2

[[steps]]
kind = "power.status"
channel = 1

[[steps]]
kind = "scope.capture"
channel = 2
label = "before"
points = "def"
time_range_s = 0.01
save_csv = false

[[steps]]
kind = "power.set"
channel = 1
voltage_v = 3.3
current_limit_a = 0.1

[[steps]]
kind = "scope.capture"
channel = 2
label = "v3v3"
points = "def"
time_range_s = 0.01
save_csv = false

[[steps]]
kind = "power.set"
channel = 1
voltage_v = 5.0
current_limit_a = 0.1

[[steps]]
kind = "scope.capture"
channel = 2
label = "restore_5v"
points = "def"
time_range_s = 0.01
save_csv = false
```

注意：计划里没有 `power.output`。如果实验需要开关输出，必须显式写：

```toml
[[steps]]
kind = "power.output"
channel = 1
state = "off"
```

这不是省略项。

## 第一版 step kind 边界

计划解析层可以识别这些显式动作：

```text
scope.auto
scope.capture
source.status
source.set_freq
source.set_func
source.set_vpp
source.output
source.set_duty
rf_source.status
rf_source.output_enable
rf_source.output_disable
power.status
power.set
power.output
sleep
```

当前执行器已实装的动作如下；对具体仪器执行仍取决于 descriptor capability、access 和各自的安全条件：

```text
power.status
power.set
scope.auto
scope.capture
source.status
source.set_freq
source.set_func
source.set_vpp
source.set_duty
source.output
rf_source.status
rf_source.output_enable
rf_source.output_disable
sleep
```

`source.set_duty` 对 DG4202 使用 `:SOUR<n>:FUNC:SQU:DCYC <percent>`，参数单位是百分比，范围限制为 `0 < duty_percent < 100`。

RF 信号源不属于本节的 `source.*` step，也不能借用其中的 channel、Vpp、restore 或 safety gate 语义。当前 Core 已在 `run schema` 中提供 `rf_source.status`、CW 频率／功率步骤、`rf_source.output_enable`／`rf_source.output_disable` 和 M3 的 `rf_source.modulation_configure`；它们使用独立的类型化 RF artifact。DSG830 已完成 A1／A2／A3，production descriptor 声明 `rf_source.snapshot`、`rf_source.cw_configure` 和受 safety 限制的 `rf_source.output`，因此 status 可在 `read_only` session 中读取快照，CW 与输出步骤仅在 `read_write`、相应 capability 和 fresh preflight 同时成立时执行。M3 已完成离线合同但 production capability 仍等待 A4；Pulse、Sweep 和 trigger 仍未开放。详见[RF 信号源使用指南](../guides/WaveBench_RF信号源使用指南.md)、[RF 信号源领域设计](WaveBench_RF信号源设计.md)和[RF 信号源开发里程碑](WaveBench_RF信号源开发里程碑.md)。

`scope.capture` 可以额外声明：

```toml
quality_gate = true
auto_recover = true
```

`quality_gate` 会把采集摘要里的质量状态和 warning 写入流程级 `run.json` / `summary.csv`。`auto_recover` 只在出现 warning 时工作；它会按 `wavebench.toml` 的 `[quality].auto_recover_attempts` 执行多次 `scope.auto` + `<label>_auto_retryN` 重采。初次采集包不会丢，会记录在 `quality_recovery` 里。

相关配置：

```toml
[quality]
auto_recover_attempts = 2
consistency_required_captures = 2
frequency_consistency_ratio = 0.02
voltage_vpp_consistency_ratio = 0.05
voltage_mean_consistency_v = 0.05
duty_consistency = 0.03
```

如果多次 warning 采集的频率、Vpp、均值、duty 等可比较指标差别不大，最终采集会标记为 `ok_by_consistency`。这表示「单次质量规则仍有 warning，但重复测量稳定，结果可以采用」。


## Run plan schema quick reference

更偏使用手册的说明见 [`WaveBench_run_plan_使用指南.md`](../guides/WaveBench_run_plan_使用指南.md)。这一节保留设计层面的速查。


Use this command to print the authoritative step schema from the current code:

```powershell
python -m wavebench run schema
```

Top-level tables:

| Table | Fields | Notes |
|---|---|---|
| `[experiment]` | `name`, `label` | Optional metadata; defaults to the plan filename. |
| `[safety]` | `scope_guard_channel`, `require_scope_coupling_not`, `safety_gate`, `off_source_channels`, `off_power_channels` | Optional read-only guard and explicit failure OFF policy. The OFF policy is inactive unless `safety_gate = true`. |
| `[restore]` | `source_state`, `source_channel` | Optional source snapshot/restore. Restore is attempted on success and failure. |
| `[[steps]]` | `kind` plus kind-specific fields | Steps execute in order. |

Supported `[[steps]]` kinds:

| kind | Required fields | Optional fields |
|---|---|---|
| `power.status` | - | `channel` |
| `power.set` | `voltage_v`, `current_limit_a` | `channel` |
| `power.output` | `state` | `channel` |
| `source.status` | - | `channel` |
| `source.set_func` | `function` | `channel` |
| `source.set_vpp` | `value_vpp` | `channel` |
| `source.set_freq` | `frequency_hz` | `channel` |
| `source.set_duty` | `duty_percent` | `channel` |
| `source.output` | `state` | `channel` |
| `rf_source.status` | - | `on_failure` |
| `rf_source.output_enable` | `port_id` | `on_failure` |
| `rf_source.output_disable` | `port_id` | `on_failure` |
| `scope.auto` | - | `on_failure`, `safety_gate` |
| `scope.capture` | - | `channel`, `label`, `points`, `time_range_s`, `window_frequency_hz`, `target_cycles`, `expect_frequency_hz`, `frequency_tolerance`, `save_csv`, `save_npy`, `quality_gate`, `auto_recover`, `on_failure`, `safety_gate`, `[steps.expect]` |
| `sleep` | `duration_s` | `on_failure`, `safety_gate` |

`[steps.expect]` belongs under a `scope.capture` step. Each metric accepts `{ min = ..., max = ... }`. Common metrics are `frequency_estimate_hz`, `frequency_error_ratio`, `voltage_vpp_v`, `voltage_mean_v`, and `duty_cycle`.

## 实验指标断言

`scope.capture` step 可以用 `[steps.expect]` 对采集摘要指标做 min/max 检查：

```toml
[[steps]]
kind = "scope.capture"
channel = 1
label = "duty_50"
expect_frequency_hz = 10000
frequency_tolerance = 0.05
quality_gate = true
auto_recover = true

[steps.expect]
frequency_estimate_hz = { min = 9500, max = 10500 }
duty_cycle = { min = 0.45, max = 0.55 }
voltage_vpp_v = { min = 2.8, max = 3.8 }
```

断言读取的是流程 artifact 中的 `quality` 指标，也就是采集包 `metadata.json` 里的 `waveform.summary` 提炼结果。指标缺失、非数字、低于 `min` 或高于 `max` 都会让该 step 标记为 `failed`，随后整个 run 标记为 `failed`。

失败不会抛掉证据：采集包、step record、`run.json` 与 `summary.csv` 都会保留。这样比较适合电赛调试——先留下波形，再判断实验是否通过。

暂不支持：

```text
条件分支
循环
表达式
并行执行
隐式自动状态恢复
跨步骤表达式和复杂 pass/fail 判定
```

循环很诱人，但第一版先不要。可以用多个显式 `[[steps]]` 写清楚，等格式稳定后再考虑 `matrix`。

## 安全 guard

guard 不是所有流程的标准步骤。只有计划显式声明了某个 guard 时，执行器才应该检查它。

第一版可以支持这个特殊 guard：

```toml
[safety]
require_scope_coupling_not = ["DC"]
scope_guard_channel = 2
```

含义：

```text
查询 `CHAN<scope_guard_channel>:COUP?`，例如 `scope_guard_channel = 1` 时查询 `CHAN1:COUP?`，`scope_guard_channel = 2` 时查询 `CHAN2:COUP?`
如果返回 DC，则拒绝执行
```

这是为「将 DP800 输出直接接到示波器探头，验证电源控制是否生效」准备的特殊接线，不是普通电源控制流程的默认要求。RTM2032 的 `DC` 是 50 Ω 直连，在这种接线下测量电源可能损坏输入。

注意：guard 只能查询，不能自动修改示波器输入阻抗。

计划还可以声明失败时的安全门：

```toml
[safety]
safety_gate = true
off_source_channels = [1]
off_power_channels = [1]
```

安全门与 `on_failure` 分开判断。失败或质量 gate warning 触发后，执行器先对授权通道执行 OFF，再停止 run；若启用 source restore，恢复配置后会再次确认授权通道为 OFF。OFF 结果写入 step artifact 和 `run.json.error`。`on_failure = "continue"` 只适用于未触发安全门的普通失败；未配置 OFF 目标的安全门会以配置失败结束，不会自动选择通道。

## 输出目录

流程级输出建议：

```text
data/runs/YYYYMMDD_HHMMSS_<experiment_label>/
  plan.toml
  run.json
  summary.csv
  steps/
    00_power_status.json
    01_scope_capture.json
```

其中 scope capture 不复制原始波形，只引用现有采集包路径：

```json
{
  "step_index": 1,
  "kind": "scope.capture",
  "package": "data/raw/20260430_...",
  "metadata": "data/raw/20260430_.../metadata.json"
}
```

避免把大文件复制来复制去。

## 错误处理

如果某一步失败，默认行为是：

- 立即停止；
- 写 `run.json`，标记 `status = "failed"`；
- 记录失败 step、错误类型、错误消息；
- 已经生成的 capture package 保留；
- 不执行后续步骤。

需要继续采点时，step 必须显式声明 `on_failure = "continue"`。安全门触发时始终停止，并先执行已授权的 OFF 策略。

第一版不做自动 rollback。

原因：真实仪器 rollback 不是数据库事务。乱恢复比不恢复更危险。

## 当前实现状态

截至 2026-04-30，已完成并验证：

```text
run check: 解析 plan 并打印步骤摘要，不连接仪器
run plan : 执行 source / power / scope / sleep 显式 step
safety  : 按 scope_guard_channel 查询 CHAN<n>:COUP?；命中 require_scope_coupling_not 时拒绝执行
quality : `scope.capture` 可记录质量状态；`auto_recover = true` 时按配置多次 `scope.auto` 重采，并做重复一致性判断
expect : `scope.capture` 可设置指标 min/max 断言，失败时 run 状态为 failed
output  : data/runs/YYYYMMDD_HHMMSS_<label>/run.json + summary.csv + step records
restore : `[restore] source_state = true` 时 snapshot source 状态，并在成功/失败路径恢复
state guard : run 内 Source/Power 基础控制写入执行 compare-before-write；漂移时零 setter 调用，
并将 `state_drift` 与状态差异写入工件
scope status : 缺少 `scope.snapshot` 时返回 `status=partial`；`--strict` 路径保持完整快照门槛
capability : `capability explain` 离线解释 OperationSpec、descriptor、access 和安全替代
intent : `run intent` / `run plan --intent` 校验 plan、config 和 payload digest
```

DP800 电压阶跃实机 smoke：

```text
plan = plans/dp800_scope_probe_voltage_steps.toml
run  = data/runs/20260430_150454_dp800_scope_probe_voltage_capture
steps = 6, status = ok
sequence = guard -> before capture -> 3.3 V set/capture -> 5 V restore/capture
scope CH2 mean ≈ 4.8826 V -> 3.1976 V -> 4.8842 V
DP800 final = output ON, set 5.0 V / 0.1 A, measured ≈ 5.0114 V
```

DG4202 duty-cycle + DP800 对照实机 smoke：

```text
plan = plans/dg4202_duty_10k_power_ch2_check.toml
run  = data/runs/20260430_154307_dg4202_duty_10k_power_ch2_check
steps = 18, status = ok, restore = ok
source CH2 = 10 kHz square, 3.3 Vpp, duty 25% -> 50% -> 75%
scope CH1 measured duty = 0.25 -> 0.50 -> 0.75
scope CH2 measured DP800 mean ≈ 4.8864 V -> 4.8824 V
final source CH2 = SIN / 5000 Hz / 5 Vpp / output ON / duty 50%
```

100 kHz duty 复验：修复 `target_cycles/window_frequency_hz` 到 `time_range_s` 的换算后，100 kHz / 75% duty 的采样间隔为 10 ns、约 1000 points/cycle，实测 duty = 0.75。此前读成约 0.70 的根因是 run plan 没有把目标周期数转换为示波器时窗，导致默认 10 ms 窗口下每周期只有约 10 个点。

## 实现顺序

### Step 1：只写 parser 和 dataclass（已完成）

新增：

```text
src/wavebench/services/run_plan.py
```

数据结构：

```python
RunPlan
RunStep
SafetyGuard
```

只解析 TOML，不执行仪器。

测试：

```text
解析 power.set / scope.capture / safety guard
未知 kind 拒绝
缺必需字段拒绝
```

### Step 2：实现 run check（已完成）

新增 CLI：

```text
wavebench run check --plan <file>
```

只做：

```text
解析 plan
检查 step kind
打印步骤摘要
```

暂时不连仪器。

### Step 3：实现可选只读 guard（已完成）

仅当 plan 声明 safety guard 时连接 scope，查询 coupling。

如果 `CHAN<scope_guard_channel>:COUP? == DC`，拒绝执行。

### Step 4：实现最小执行器（已完成）

只支持：

```text
power.status
power.set
scope.capture
sleep
```

用 DP800 电压阶跃作为第一条实机流程。已在 `data/runs/20260430_150454_dp800_scope_probe_voltage_capture/` 验证通过。

### Step 5：再接 source step（已完成）

已接入：

```text
source.status
source.set_freq
source.set_func
source.set_vpp
source.set_duty
source.output
```

已用 10 kHz square duty 25% / 50% / 75% 的实机流程验证。

## 第一条推荐实机流程

推荐从已经验证过的 DP800 电压阶跃开始：

```text
guard CH2 not 50Ω
power status
scope capture before
power set 3.3V / 0.1A
scope capture v3v3
power set 5.0V / 0.1A
scope capture restore_5v
```

它不涉及 signal generator，不涉及 output on/off，比三仪器全开更安全。

## 设计结论

第一版多仪器流程不追求复杂的自动判断，而是保持记录清楚：每一步写明动作，执行后留下证据。

WaveBench 的流程层不代替人工判断风险，而是减少手工拼接脚本时的错误。


## Source 状态恢复

计划可以显式开启 source 状态恢复：

```toml
[restore]
source_state = true
source_channel = 2
```

当前 snapshot / restore 覆盖：

```text
output
function
frequency_hz
amplitude_vpp
square_duty_cycle_percent
```

恢复顺序：

```text
set_function -> set_amplitude_vpp -> set_frequency -> set_square_duty_cycle -> set_output
```

仍然不恢复 DG4202 sweep mode、offset、phase、load、modulation。原因和 sweep restore 一样：这些字段还没有完整安全设计，不能假装可以无风险恢复。


## scope.auto 步骤

可以在 run plan 里显式插入示波器自带自动调节：

```toml
[[steps]]
kind = "scope.auto"

[[steps]]
kind = "scope.capture"
channel = 1
label = "after_auto"
```

这对应 RTM2032 的 `AUToscale`，并沿用已有 `*OPC?` 等待完成。它不会被 `scope.capture` 隐式调用，因为 auto 会改变水平、垂直和触发设置。需要它时，把它作为一个清楚的 step 放进计划里。


## scope.capture 质量检查与自动重采

`scope.capture` 默认仍然只是采集，不会擅自按示波器 Auto。需要质量判断时显式写：

```toml
[[steps]]
kind = "scope.capture"
channel = 1
label = "duty_100k"
expect_frequency_hz = 100000
window_frequency_hz = 100000
target_cycles = 10
quality_gate = true
auto_recover = true
```

当前质量提示包括：

```text
frequency_unavailable
low_cycle_count
low_points_per_cycle
low_signal_amplitude
frequency_mismatch
```

如果 `auto_recover = true` 且第一次采集出现 warning，执行器会按 `[quality].auto_recover_attempts` 调用 RTM2032 `AUToscale + *OPC?` 并重采 `<label>_auto_retryN`。`run.json` 会保存最终采集包，也会在 `quality_recovery` 中记录每次采集包、warning 和一致性判断结果。

如果最近 `consistency_required_captures` 次采集的频率、Vpp、均值、duty 等指标都在 `[quality]` 容差内，即使 warning 还存在，也标记为 `ok_by_consistency`。

这样做的边界是：自动调节仍然是显式 opt-in，不会被普通 `scope.capture` 偷偷触发。


## scope.capture 实验断言

`[steps.expect]` 用来判断实验有没有达标。它和质量门分开：质量门判断数据是否可信，expect 判断可信数据是否满足实验目标。

```toml
[[steps]]
kind = "scope.capture"
channel = 1
label = "pwm_check"
expect_frequency_hz = 100000
quality_gate = true
auto_recover = true

[steps.expect]
duty_cycle = { min = 0.73, max = 0.77 }
frequency_error_ratio = { max = 0.02 }
voltage_vpp_v = { min = 3.0, max = 3.6 }
```

断言支持 `min` / `max`。如果指标缺失，或实际值超出范围，对应 step 状态为 `failed`，整体 run 状态也为 `failed`。采集包、质量信息、失败原因和实际值仍然写入 `run.json` / `summary.csv`。
