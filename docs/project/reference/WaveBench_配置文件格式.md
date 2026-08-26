# WaveBench 配置文件格式

## 结论

WaveBench 使用 TOML 表达本机配置和 run plan；YAML 不在当前支持范围内。

分工：

```text
TOML = 本机仪器连接、默认行为和实验流程配置
YAML = 当前不支持
```


## 文件命名

本地配置文件：

```text
wavebench.toml
```

示例配置文件：

```text
wavebench.example.toml
```

规则：

```text
wavebench.example.toml 进入 git
wavebench.toml 不进入 git
```

原因：

- `wavebench.example.toml` 给用户和开发者作为模板；
- `wavebench.toml` 可能包含真实仪器 IP、输出路径和现场习惯；
- 本地配置不应该提交。

## 查找顺序

配置查找顺序为：

```text
命令行 --config 指定
  ↓
当前目录 ./wavebench.toml
  ↓
程序默认值
```

示例：

```bash
wavebench scope idn --config lab.toml
```

如果没有传 `--config`，则查找：

```text
./wavebench.toml
```

如果仍然不存在，则使用程序默认值。需要仪器资源字符串的命令，如果没有 `resource`，必须报错。

## 参数优先级

```text
命令行参数 > TOML 配置 > 程序默认值
```

例如配置文件默认 CH1：

```toml
[scope]
default_channel = 1
```

运行：

```bash
wavebench scope capture --channel 2
```

则必须按 CH2 执行。

## 仪器访问策略

`[scope]`、`[source]`、`[rf_source]`、`[power]` 和 `[dmm]` 都支持 `access` 字段。该字段只控制
WaveBench 发起的仪器操作，不会修改配置文件，也不会替代操作系统或仪器自身的权限控制。

```toml
[scope]
access = "read_write"
```

可用值如下：

- `read_write`：默认值，允许已注册的读取、写入和采集操作。
- `read_only`：允许 `observe` 和 `stateful_read` 操作；会拒绝写入和采集。
- `disabled`：拒绝所有仪器操作；离线的 `run check`、`run schema` 等命令仍可执行。

访问检查在建立具体操作前执行。旧配置未填写 `access` 时按 `read_write` 处理，以保持
现有配置的行为不变。需要查看某项操作的能力和风险说明时，可先使用离线的 `run schema`。

## 第一版配置结构

```toml
[connection]
backend = "lan"
resource = "TCPIP::192.0.2.10::INSTR"
timeout_ms = 10000
opc_timeout_ms = 30000
read_retry_attempts = 1
read_retry_delay_ms = 200

[scope]
driver = "rtm2032"
model_hint = "RTM2032"
default_channel = 1
reset_before_run = false
check_errors = true
access = "read_write"

[autoscale]
wait_opc = true
check_errors = true

[waveform]
format = "real"
byte_order = "lsbf"
points = "dmax"

[output]
directory = "data/raw"
package_naming = "timestamp_label"
save_csv = true
save_npy = true
save_json = true
save_commands_log = true
save_screenshot = false

[tui]
log_max_lines = 10000
log_keep_lines_after_trim = 1000

[quality]
auto_recover_attempts = 2
consistency_required_captures = 2
frequency_consistency_ratio = 0.02
voltage_vpp_consistency_ratio = 0.05
voltage_mean_consistency_v = 0.05
duty_consistency = 0.03

[safety_limits]
max_source_vpp = 5.0
max_power_voltage_v = 5.0
max_power_current_limit_a = 0.2
# min_source_port_voltage_v = -5.0
# max_source_port_voltage_v = 5.0

[source]
driver = "dg4202"
resource = "TCPIP::192.0.2.11::INSTR"
default_channel = 1
check_errors = true
ensure_fix_mode_on_set_frequency = true
settle_ms_after_set_frequency = 500
access = "read_write"

# DSG830 已完成 A1／A2；read_only 仍是身份与状态查询的默认安全选择。
[rf_source]
driver = "rigol.dsg830"
resource = "TCPIP::192.0.2.13::INSTR"
access = "read_only"

# `rf-source output` 的 ON preflight 会消费该静态证据；它不授权 CW 或其它 RF 写入能力。
[[rf_source.safety.ports]]
port_id = "rf_out"
minimum_frequency_hz = 9000
maximum_frequency_hz = 3000000000
maximum_power_dbm = -20
actual_termination_ohm = 50

[power]
driver = "dp800"
resource = "TCPIP::192.0.2.12::INSTR"
default_channel = 1
check_errors = true
settle_ms_after_set = 2000
settle_ms_after_output = 1000
access = "read_write"
```

## `[connection]`

```toml
[connection]
backend = "lan"
resource = "TCPIP::192.0.2.10::INSTR"
timeout_ms = 10000
opc_timeout_ms = 30000
read_retry_attempts = 1
read_retry_delay_ms = 200
```

`backend` 的可用值由所选 driver 决定；当前常见值包括 `lan` 和 `serial`。

```toml
backend = "lan"
```

`resource` 是 VISA 资源字符串。

LAN 资源示例：

```text
TCPIP::<instrument-ip>::INSTR
```

串口资源示例：

```toml
backend = "serial"
resource = "COM3"
```

Windows 也接受 `\\.\COM10`；资源租约会将它与 `COM10` 规范化为同一身份。Linux / WSL
继续使用 `/dev/serial/by-id/...` 等设备路径。`ASRLn::INSTR` 是 VISA 资源写法，不与
`COMn` 自动合并；同一台仪器应固定使用一种后端和一种资源写法。

Windows 原生租约目录默认位于 `%LOCALAPPDATA%\WaveBench\resource-leases-v1`，也可通过
`WAVEBENCH_LEASE_DIR` 指定本地目录。UNC、SMB 和 WSL 挂载路径不属于跨环境互斥保证范围。

`read_retry_attempts` 是显式 `safe_to_replay` query 失败后的额外完整命令重放次数，默认 1。
核心 driver 的查询首版均显式采用 `no_replay`，因此增大该值不会让普通状态查询、
`SYST:ERR?`、`*OPC?`、二进制块或浮点列表查询自动重发。命令结果未知、响应部分到达或
通信同步无法证明时，应关闭旧 session、建立新 session，再从完整采集流程起点重试。

`read_retry_delay_ms` 是两次短文本只读 query 之间的等待时间，默认 200 ms。

## `[scope]`

```toml
[scope]
driver = "rtm2032"
model_hint = "RTM2032"
default_channel = 1
reset_before_run = false
check_errors = true
access = "read_write"
```

当前内置 scope driver 的常用配置为：

```toml
driver = "rtm2032"
```

`reset_before_run` 默认必须为 false。WaveBench 不建议通过配置隐式启用 reset；需要重置时，应使用明确的硬件操作。

## `[autoscale]`

```toml
[autoscale]
wait_opc = true
check_errors = true
```

`AUToscale` 是异步命令，所以必须等待完成。

即使配置里出现 `wait_opc = false`，程序也可以忽略该值并强制等待，避免误读波形。

## `[waveform]`

```toml
[waveform]
format = "real"
byte_order = "lsbf"
points = "dmax"
```

RIGOL DS1104Z/DS1000Z 可将 scope 段改为：

```toml
[scope]
driver = "ds1104" # 也接受别名 "ds1000z"
model_hint = "DS1104Z"
default_channel = 1
reset_before_run = false
check_errors = true
```

这里的 `ds1104` / `ds1000z` 是核心内置驱动的兼容 alias。安装式 V2 插件首版只接受
canonical ID；要显式选择外置 DS1000Z 插件，应配置 `driver = "rigol.ds1000z"`，不能
使用外置 alias。

DS1104Z 上 `waveform.points = "def"` 对应 NORM 屏幕波形，`"max"`/`"dmax"`
对应停止状态下的 RAW 存储波形。RAW 使用 BYTE 格式并按 250000 点分块读取；
`waveform.time_range_s` 是总时间窗口，驱动会除以 12 后写入主时基的 s/div。

对应命令方向：

```text
FORM REAL
FORM:BORD LSBF
CHAN1:DATA:POIN DMAX
```

当前 scope waveform 配置支持：

```text
format = real
byte_order = lsbf
points = dmax
```

暂不支持 UINT、手动点数和复杂采样范围。

## `[output]`

```toml
[output]
directory = "data/raw"
package_naming = "timestamp_label"
save_csv = true
save_npy = true
save_json = true
save_commands_log = true
save_screenshot = false
```

默认保存：

```text
CSV + NPY + metadata.json + commands.log
```

`save_screenshot` 控制采集截图是否写入采集包。`run plan` 的流程级输出不写在这里，固定写入 `data/runs/<timestamp>_<label>/`，并引用采集包路径，避免复制大波形文件。

## `[tui]`

```toml
[tui]
log_max_lines = 10000
log_keep_lines_after_trim = 1000
```

`log_max_lines` 是 TUI 持久调试日志的最大行数。

`log_keep_lines_after_trim` 是日志超过最大行数后保留的最新行数。

## `[quality]`

```toml
[quality]
auto_recover_attempts = 2
consistency_required_captures = 2
frequency_consistency_ratio = 0.02
voltage_vpp_consistency_ratio = 0.05
voltage_mean_consistency_v = 0.05
duty_consistency = 0.03
```

这些参数只服务于 `run plan` 中显式开启的 `scope.capture` 质量恢复：

- `auto_recover_attempts`：初次采集出现质量 warning 后，最多执行多少次 `scope.auto` + 重采。
- `consistency_required_captures`：判断 warning 结果是否可被一致性采信时，需要比较最近几次采集。
- `frequency_consistency_ratio` / `voltage_vpp_consistency_ratio`：频率与 Vpp 的相对跨度阈值。
- `voltage_mean_consistency_v` / `duty_consistency`：均值电压与占空比的绝对跨度阈值。

如果多次 warning 采集的可比较指标稳定，最终采集可标记为 `ok_by_consistency`。这不会消除 warning，而是把「重复测量稳定」记录为证据。

`[steps.expect]` 不在本机配置里定义。它属于单个 run plan step，因为指标范围通常和具体实验目标绑定。

## `[safety_limits]`

```toml
[safety_limits]
max_source_vpp = 5.0
max_power_voltage_v = 5.0
max_power_current_limit_a = 0.2
```

这些参数是第一层执行安全上限。现有 V1 轴均为可选项；省略某一项表示该 V1 轴不设软件上限。

- `max_source_vpp`：限制 `source set-vpp`、`source arb-load`、`source basic-configure-v2`、`run plan` 中的 `source.set_vpp` / `source.arb_load` / `source.basic_configure_v2`，以及 `sweep.frequency_response` 的每个 `amplitudes_vpp` / 生成 Vpp 切片。
- `max_power_voltage_v`：限制 `power set` 与 `run plan` 中 `power.set` 的设定电压。
- `max_power_current_limit_a`：限制 `power set` 与 `run plan` 中 `power.set` 的限流值。
- `min_source_port_voltage_v`、`max_source_port_voltage_v`：Source V2 的显式、有符号端口电压区间。
  两项必须同时出现，值必须有限且满足最小值小于最大值；不能从 `max_source_vpp` 推导。两项同时缺失
  不影响现有 V1 行为；已配置时，M5-D basic configure 与 output ON 都会检查 `offset ± Vpp / 2`。

`run plan` 会在创建 run 目录和连接仪器前先检查可静态判断的上限。直接 CLI 设置也会在写仪器前检查。
`source output on` / `source output-v2 on` / `power output on` 会先读取当前设定值，若当前设定值超限，则拒绝打开输出。

这层不会自动判断示波器 50Ω 输入阻抗，也不会替用户推断被测电路是否安全；它只是先挡住明确超过配置上限的写操作。

## `[source]`

```toml
[source]
driver = "dg4202"
resource = "TCPIP::192.0.2.11::INSTR"
default_channel = 1
check_errors = true
ensure_fix_mode_on_set_frequency = true
settle_ms_after_set_frequency = 500
access = "read_write"

[[source.terminations]]
channel = 1
kind = "resistive"
minimum_ohm = 49.5
maximum_ohm = 50.5
```

当前 source 支持 DG4202。`ensure_fix_mode_on_set_frequency = true` 表示设置固定频率前，若设备仍在 sweep 模式，先显式切回 FIX，避免扫频状态污染单点实验。

`[[source.terminations]]` 是实际端接的静态证据，供后续 Source V2 能量操作使用，不会替代仪器的
显示负载，也不会改变现有 V1 CLI、run plan 或 setter 行为。每项必须包含正整数 `channel` 和
`kind`：

- `resistive` 还必须包含有限、正数且递增的 `minimum_ohm`、`maximum_ohm`；
- `high_impedance` 可以不提供电阻区间，但没有有限区间时不能单独形成保守的输出 ON 证明；
- 同一个 channel 最多出现一次。

实际端接与仪器显示的 `HiZ`、`50 Ω` 或其它 load setting 是不同事实。配置项只声明实验台已确认的
外部端接；未配置不会被核心根据显示负载自动推断。

## `[rf_source]`

```toml
[rf_source]
driver = "rigol.dsg830"
resource = "TCPIP::192.0.2.13::INSTR"
access = "read_only"

[[rf_source.safety.ports]]
port_id = "rf_out"
minimum_frequency_hz = 9000
maximum_frequency_hz = 3000000000
maximum_power_dbm = -20
actual_termination_ohm = 50
```

`[rf_source]` 是独立于普通 `[source]` 的 RF 信号源配置。它使用 plugin descriptor 的稳定
`port_id`、Hz 和 dBm，不存在 `default_channel`、Vpp 或波形字段。M0 提供
`wavebench rf-source idn` 与 `wavebench rf-source status`；后者要求 production descriptor 声明
`rf_source.snapshot`。M1 的频率／功率 CLI、M4 的 Pulse／Step Sweep 配置和 M2 的输出 CLI 都要求对应 capability、`read_write` 访问和
fresh preflight；M2 的端口 ON/OFF 还要求完整安全配置。DSG830 已完成 A1／A2／A3／A4 Pulse／A4 Step Sweep，声明 `rf_source.idn`、`rf_source.snapshot`、`rf_source.cw_configure`、`rf_source.output`、`rf_source.pulse_configure` 与 `rf_source.sweep_configure`：status 可在已配置的只读 session 中执行，端口 ON/OFF 还要求切换为 `read_write` 并提供完整安全配置。Pulse 与 Step Sweep 配置分别保持 Pulse／Sweep disabled；调制、trigger、Sweep execute／fire 与 Level Sweep 仍由 capability 门禁拒绝。

字段说明：

- `driver`：已安装 RF 插件的 canonical driver ID；默认值是 `rigol.dsg830`，但只有已安装插件才可解析。
- `resource`：RF 信号源的 VISA 资源串；可由 `rf-source` 命令的 `--resource` 临时覆盖。
- `access`：沿用通用访问策略。`read_only` 是 production 状态查询的默认选择；`read_write` 本身不授予写入，
  还必须同时具备对应 descriptor capability、profile、fresh safety preflight 和实机证据。
- `options`：可选的插件私有配置表；公开配置不要放入真实资源之外的凭据或实验室专有数据。

`[[rf_source.safety.ports]]` 是按端口声明的本地静态安全证据。每项必须提供唯一 `port_id`、有限的
`minimum_frequency_hz`、`maximum_frequency_hz`、`maximum_power_dbm` 和正数
`actual_termination_ohm`；最大频率不得小于最小频率。它不改变仪器显示的负载设置，也不会把 dBm
换算为 Vpp。M2 的 RF ON 事务会使用它进行准入判断；若某个 descriptor 缺少 output capability，事务仍会在
打开 transport 前拒绝。DSG830 已声明该 capability，但 `read_write`、完整安全配置和 fresh snapshot 缺一不可。

RF 的 capability、A1–A5 证据门和 DSG830 的当前 production 边界见
[RF 信号源领域设计](../design/WaveBench_RF信号源设计.md)和
[RF 信号源开发里程碑](../design/WaveBench_RF信号源开发里程碑.md)。

## `[power]`

```toml
[power]
driver = "dp800"
resource = "TCPIP::192.0.2.12::INSTR"
default_channel = 1
check_errors = true
settle_ms_after_set = 2000
settle_ms_after_output = 1000
access = "read_write"
```

当前 power 支持 DP800 系列。`power set`、`power output` 与 `power protection` 是独立动作：`power set` 只改电压/限流，`power output` 只改输出开关，`power protection` 只查询或修改 OVP/OCP 保护。两个 settle 配置分别用于写入后等待读回稳定。

## 不由配置隐式修改的内容

暂不配置：

```text
trigger
channel scale / offset / coupling
timebase
implicit autoscale / reset
```

原因：本机配置不应悄悄接管前面板主要设置。需要改变仪器状态的动作应写成显式 CLI 命令或显式 run plan step。

## `.gitignore` 规则

```gitignore
# Local helper workspace
# Local WaveBench config and generated data
wavebench.toml
data/
```

## YAML 边界

当前不支持 YAML。配置和实验流程均使用 TOML；实验计划通过 `run schema` 和 `run template --list` 获取当前语法。

## 实现说明

Python 使用标准库读取 TOML：

```python
import tomllib
```

程序只读取配置，不写回用户配置文件，因此无需额外 TOML 写入库。


## `[safety_limits]`

```toml
[safety_limits]
max_source_vpp = 5.0
max_power_voltage_v = 5.0
max_power_current_limit_a = 0.2
# min_source_port_voltage_v = -5.0
# max_source_port_voltage_v = 5.0
```

这些参数是第一层执行安全上限。现有 V1 轴均为可选项；省略某一项表示该 V1 轴不设软件上限。

- `max_source_vpp`：限制 `source set-vpp`、`source arb-load`、`source basic-configure-v2`、`run plan` 中的 `source.set_vpp` / `source.arb_load` / `source.basic_configure_v2`，以及 `sweep.frequency_response` 的每个 `amplitudes_vpp` / 生成 Vpp 切片。
- `max_power_voltage_v`：限制 `power set` 与 `run plan` 中 `power.set` 的设定电压。
- `max_power_current_limit_a`：限制 `power set` 与 `run plan` 中 `power.set` 的限流值。
- `min_source_port_voltage_v`、`max_source_port_voltage_v`：Source V2 的显式有符号端口电压区间。
  两项必须同时配置，且不从 `max_source_vpp` 推导；两项缺失时旧 V1 命令继续保持原有行为，已配置时
  M5-D basic configure 与 output ON 使用 `offset ± Vpp / 2` 检查区间。

`run plan` 会在创建 run 目录和连接仪器前先检查可静态判断的上限。直接 CLI 设置也会在写仪器前检查。
`source output on` / `source output-v2 on` / `power output on` 会先读取当前设定值，若当前设定值超限，则拒绝打开输出。

这层不会自动判断示波器 50Ω 输入阻抗，也不会替用户推断被测电路是否安全；它只是先挡住明确超过配置上限的写操作。

## `[source]`

```toml
[source]
driver = "dg4202"
resource = "TCPIP::<dg4202-ip>::INSTR"
default_channel = 1
check_errors = true
ensure_fix_mode_on_set_frequency = true
settle_ms_after_set_frequency = 500

[[source.terminations]]
channel = 1
kind = "resistive"
minimum_ohm = 49.5
maximum_ohm = 50.5
```

当前第二阶段信号源只支持：

```toml
driver = "dg4202"
```

说明：

- `resource` 是信号发生器的 VISA 资源串。
- `default_channel` 是 `wavebench source ...` 未显式传 `--channel` 时使用的通道。
- `ensure_fix_mode_on_set_frequency = true` 表示在执行 `source set-freq` 前，若仪器当前处于 `SWE` 模式，则先切到 `FIX`，避免把 sweep 频率误当成固定频率输出。

`[[source.terminations]]` 声明已确认的外部端接，而不是仪器显示负载。`resistive` 需要有限的
`minimum_ohm`、`maximum_ohm`；`high_impedance` 可省略电阻区间，但不能因此自动获得 Source V2
输出 ON 准入。该配置仅供后续 Source V2 安全预算使用，不改变现有 V1 source 命令。


### `settle_ms_after_set_frequency`

`source set-freq` 写入频率后会按该配置等待指定毫秒数，再返回状态。离散扫点建议从 `500` 开始，避免信号源刚切换频点时示波器读到过渡状态。
