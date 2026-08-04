# WaveBench 配置文件格式

## 结论

WaveBench 第一阶段使用 TOML 作为本机配置文件格式。

分工：

```text
TOML = 本机仪器连接与默认行为配置
YAML = 后续实验流程编排配置
```

第一阶段只实现 TOML，不引入 YAML。

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

第一阶段配置查找顺序：

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

[source]
driver = "dg4202"
resource = "TCPIP::192.0.2.11::INSTR"
default_channel = 1
check_errors = true
ensure_fix_mode_on_set_frequency = true
settle_ms_after_set_frequency = 500

[power]
driver = "dp800"
resource = "TCPIP::192.0.2.12::INSTR"
default_channel = 1
check_errors = true
settle_ms_after_set = 2000
settle_ms_after_output = 1000
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

当前只支持：

```toml
backend = "lan"
```

`resource` 是 VISA 资源字符串。

当前阶段只考虑 LAN：

```text
TCPIP::<instrument-ip>::INSTR
```

`read_retry_attempts` 是短文本只读 query 失败后的额外重试次数，默认 1。写操作、
二进制块 query 和浮点列表 query 都不会在原 session 自动重放。后两类读取失败时，
响应可能已被部分消费；应关闭并重建仪器 session，再从完整采集流程起点重试。

`read_retry_delay_ms` 是两次短文本只读 query 之间的等待时间，默认 200 ms。

## `[scope]`

```toml
[scope]
driver = "rtm2032"
model_hint = "RTM2032"
default_channel = 1
reset_before_run = false
check_errors = true
```

当前只支持：

```toml
driver = "rtm2032"
```

`reset_before_run` 默认必须为 false。第一阶段不建议通过配置启用 reset。

## `[autoscale]`

```toml
[autoscale]
wait_opc = true
check_errors = true
```

`AUToscale` 是异步命令，所以必须等待完成。

第一阶段即使配置里出现 `wait_opc = false`，程序也可以选择忽略并强制等待，避免误读波形。

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

第一阶段只支持：

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

截图第一阶段先关闭。`run plan` 的流程级输出不写在这里，它固定写入 `data/runs/<timestamp>_<label>/`，并引用采集包路径，避免复制大波形文件。

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

如果多次 warning 采集的可比较指标稳定，最终采集可标记为 `ok_by_consistency`。这不是让 warning 消失，而是把“重复测量稳定”记录成证据。

`[steps.expect]` 不在本机配置里定义。它属于单个 run plan step，因为指标范围通常和具体实验目标绑定。

## `[safety_limits]`

```toml
[safety_limits]
max_source_vpp = 5.0
max_power_voltage_v = 5.0
max_power_current_limit_a = 0.2
```

这些参数是第一层执行安全上限。全部都是可选项；省略某一项表示这一轴不设软件上限。

- `max_source_vpp`：限制 `source set-vpp`、`source arb-load`、`run plan` 中 `source.set_vpp` / `source.arb_load`，以及 `sweep.frequency_response` 的每个 `amplitudes_vpp` / 生成 Vpp 切片。
- `max_power_voltage_v`：限制 `power set` 与 `run plan` 中 `power.set` 的设定电压。
- `max_power_current_limit_a`：限制 `power set` 与 `run plan` 中 `power.set` 的限流值。

`run plan` 会在创建 run 目录和连接仪器前先检查这些上限。直接 CLI 设置也会在写仪器前检查。`source output on` / `power output on` 会先读取当前设定值，若当前设定值超限，则拒绝打开输出。

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
```

当前 source 支持 DG4202。`ensure_fix_mode_on_set_frequency = true` 表示设置固定频率前，若设备仍在 sweep 模式，先显式切回 FIX，避免扫频状态污染单点实验。

## `[power]`

```toml
[power]
driver = "dp800"
resource = "TCPIP::192.0.2.12::INSTR"
default_channel = 1
check_errors = true
settle_ms_after_set = 2000
settle_ms_after_output = 1000
```

当前 power 支持 DP800 系列。`power set`、`power output` 与 `power protection` 是独立动作：`power set` 只改电压/限流，`power output` 只改输出开关，`power protection` 只查询或修改 OVP/OCP 保护。两个 settle 配置分别用于写入后等待读回稳定。

## 不进入第一阶段配置的内容

暂不配置：

```text
trigger
channel scale / offset / coupling
timebase
implicit autoscale / reset
```

原因：第一阶段不让本机配置悄悄接管前面板主要设置。需要改变仪器状态的动作应写成显式 CLI 命令或显式 run plan step。

## `.gitignore` 规则

```gitignore
# Local helper workspace
# Local WaveBench config and generated data
wavebench.toml
data/
```

## 未来 YAML 的位置

后续如果实现实验流程编排，使用 YAML：

```text
experiments/*.yaml
```

例如：

```text
experiments/lowpass_sweep.yaml
```

TOML 和 YAML 分工：

```text
wavebench.toml        # 本机仪器连接与默认保存配置
experiments/*.yaml    # 实验步骤和流程编排
```

## 第一阶段实现建议

Python 使用标准库读取 TOML：

```python
import tomllib
```

第一阶段只需要读取配置，不需要程序写回配置，因此无需额外 TOML 写入库。


## `[safety_limits]`

```toml
[safety_limits]
max_source_vpp = 5.0
max_power_voltage_v = 5.0
max_power_current_limit_a = 0.2
```

这些参数是第一层执行安全上限。全部都是可选项；省略某一项表示这一轴不设软件上限。

- `max_source_vpp`：限制 `source set-vpp`、`source arb-load`、`run plan` 中 `source.set_vpp` / `source.arb_load`，以及 `sweep.frequency_response` 的每个 `amplitudes_vpp` / 生成 Vpp 切片。
- `max_power_voltage_v`：限制 `power set` 与 `run plan` 中 `power.set` 的设定电压。
- `max_power_current_limit_a`：限制 `power set` 与 `run plan` 中 `power.set` 的限流值。

`run plan` 会在创建 run 目录和连接仪器前先检查这些上限。直接 CLI 设置也会在写仪器前检查。`source output on` / `power output on` 会先读取当前设定值，若当前设定值超限，则拒绝打开输出。

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
```

当前第二阶段信号源只支持：

```toml
driver = "dg4202"
```

说明：

- `resource` 是信号发生器的 VISA 资源串。
- `default_channel` 是 `wavebench source ...` 未显式传 `--channel` 时使用的通道。
- `ensure_fix_mode_on_set_frequency = true` 表示在执行 `source set-freq` 前，若仪器当前处于 `SWE` 模式，则先切到 `FIX`，避免把 sweep 频率误当成固定频率输出。


### `settle_ms_after_set_frequency`

`source set-freq` 写入频率后会按该配置等待指定毫秒数，再返回状态。离散扫点建议从 `500` 开始，避免信号源刚切换频点时示波器读到过渡状态。
