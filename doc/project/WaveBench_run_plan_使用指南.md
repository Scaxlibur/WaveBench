# WaveBench run plan 使用指南

这页写给“要自己写 plan 的人”。目标不是展示所有内部实现，而是让人能在现场快速写、快速检查、看懂失败原因。

除非单独标注，带 `^` 的多行命令示例使用 PowerShell 续行；bash / zsh 可把 `^` 换成 `\`，或直接写成一行。

## 先看 schema，再写 plan

先运行：

```powershell
python -m wavebench run schema
```

它会列出当前代码真正支持的 step kind、必填字段和可选字段。文档可能会落后，`run schema` 以当前代码为准。

接线或 IP 可能变动时，先跑只读 doctor：

```powershell
python -m wavebench doctor --config wavebench.toml

python -m wavebench doctor --config wavebench.toml ^
  --discover-subnet 192.0.2.0/24 ^
  --discover-timeout-ms 500
```

`doctor` 会对配置里的 scope/source/power/dmm 做只读 `*IDN?` 检查，确认资源可达、型号匹配，并给出排错建议。`--discover-subnet` 只在配置资源不可达或型号不匹配时扫描候选资源；它只建议替代 `resource`，不会修改配置。

最小流程：

```powershell
python -m wavebench run schema
python -m wavebench run check  --config wavebench.toml --plan plans/example_scope_expect_quality.toml
python -m wavebench run verify --config wavebench.toml --plan plans/example_scope_expect_quality.toml
python -m wavebench run plan   --config wavebench.toml --plan plans/example_scope_expect_quality.toml
```

`run check` 只解析 plan 并打印摘要，不连接仪器；`run verify` 执行只读资源和安全预检；`run plan` 才会真实执行。

## run plan 的 session 生命周期

`run plan` 会在一次 run 开始时为 plan 需要的仪器统一打开 session，并在 safety guard、snapshot/restore 和所有 step 之间复用这些 session。run 成功、step 失败、safety guard 失败或 restore 失败时，都会统一关闭已经打开的 session。

这和普通 CLI one-shot 命令不同：

- `power status`、`source set-freq` 这类单次 CLI 命令仍然是一次操作打开一次、结束关闭。
- `run plan` 是整次实验持有 session，减少同一 plan 内反复连接/断开的开销，也让 safety、采集和 restore 使用同一批仪器连接。
- 当前第一版不做长 session 断线自动重建；如果 run 中途断线，应该让 run 失败并留下 `run.json` 证据，而不是偷偷重连后继续。

## 不想手写时先用模板

`run template` 只负责生成标准 TOML plan，不连接仪器、不改配置、不覆盖已有文件（除非传 `--force`）。生成后仍然走普通流程：`run check`、`run verify`、`run plan`、`run report`。

常用模板：

```powershell
python -m wavebench run template --list

python -m wavebench run template source-scope-sine ^
  --frequency 1000 ^
  --vpp 1.0 ^
  --source-channel 1 ^
  --scope-channel 1 ^
  --output plans/source_scope_sine_1k.toml

python -m wavebench run template source-scope-sweep ^
  --frequencies 100,1000,10000 ^
  --vpp 1.0 ^
  --source-channel 1 ^
  --scope-channel 1 ^
  --output plans/source_scope_sweep.toml

python -m wavebench run template source-scope-frequency-response ^
  --frequencies 100,1000,10000 ^
  --source-channel 1 ^
  --reference-channel 1 ^
  --response-channel 2 ^
  --fit ^
  --output plans/source_scope_frequency_response.toml

python -m wavebench run template dmm-acv-source ^
  --frequency 1000 ^
  --vpp 1.0 ^
  --source-channel 2 ^
  --output plans/dmm_acv_source_smoke.toml

python -m wavebench run template power-dmm-dcv ^
  --voltage 3.3 ^
  --current-limit 0.1 ^
  --power-channel 1 ^
  --output plans/power_dmm_dcv_smoke.toml
```

模板边界：

- `source-scope-sine`：生成单频点 DG4202 -> RTM2032 闭环 plan，带 source restore、scope safety、质量检查、`[steps.expect]` 和 `[steps.expect_fft]`。
- `source-scope-sweep`：把 `--frequencies` 里的频点展开成多组 `source.set_freq` + `scope.capture`，每个频点都有独立 label、expect 和 FFT expect。它不是新的执行器，只是 run plan 展开器。
- `source-scope-frequency-response`：生成一次同步双通道采集的频响 plan。reference 通道接 DUT 输入，response 通道接 DUT 输出；`--fit` 额外写入线性增益拟合配置。
- `dmm-acv-source`：生成 DG4202 -> DMM ACV smoke plan，ACV 期望值按 `Vpp / (2 * sqrt(2))` 自动缩放。
- `power-dmm-dcv`：生成 DP800 电压设置 + DMM DCV 读回 plan，只设置电压/限流，不自动打开或关闭电源输出。

模板生成后建议立刻跑：

```powershell
python -m wavebench run check  --config wavebench.toml --plan plans/source_scope_sweep.toml
python -m wavebench run verify --config wavebench.toml --plan plans/source_scope_sweep.toml
```

## 双通道频率响应 / Frequency response

`sweep.frequency_response` 让信号源按离散频点设定频率，并让示波器在每个频点只触发一次、同步读取两路波形：

- `reference_channel` 是 DUT 输入参考；`response_channel` 是 DUT 输出，二者必须不同。
- source 输出必须已经由前面的显式 `source.output state = "on"` 打开。频响 step 不会偷偷打开输出；若输出关闭或设频写入失败，会立即停止后续频点并走已有 restore 路径。
- 两路都会经过高阻保护。执行前仍需人工确认探头、线缆、量程和接地；WaveBench 不自动 deskew，也不会把测得相位冒充为已校准 DUT 相位。
- 每个成功采集强制保存双路 NPY 与 `metadata.json` 作为原始证据，即使全局输出配置关闭了 NPY/JSON。采集或分析失败会写入该频点 CSV 行后继续；信号源状态/写入异常会停止。
- 一个 plan 最多包含一个该 step，避免固定的 `frequency_response.csv` / `frequency_response_fit.json` 产物名冲突。

显式频点示例：

```toml
[[steps]]
kind = "source.output"
channel = 1
state = "on"

[[steps]]
kind = "sweep.frequency_response"
label = "lowpass_bode"
source_channel = 1
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 316.228, 1000, 3162.28, 10000]
target_cycles = 10
settle_s = 0.3
points = "def"
save_csv = false
screenshot = true

[steps.fit]
methods = ["linear_log", "polynomial", "pchip"]
polynomial_degree = 2
```

也可让 parser 生成等比或等差频点：

```toml
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
start_frequency_hz = 100
stop_frequency_hz = 100000
frequency_count = 31
spacing = "log" # "log" 或 "linear"
```

拟合的因变量始终是线性增益 `gain_linear`，自变量是 `x = log10(f / Hz)`；不会对 dB 增益拟合，也不会在测量频段外外推：

- `linear_log`：分段线性插值，导出每段 `m`、`b`，即 `G = m*x + b`。
- `polynomial`：1–5 阶多项式，导出降幂系数。阶数必须小于有效频点数。
- `pchip`：保形三次插值，导出每段 `x_start`、`x_stop` 和 `[c3, c2, c1, c0]`，即 `G = c3*dx^3 + c2*dx^2 + c1*dx + c0`。它需要先安装 `python -m pip install -e ".[analysis]"`。

相位使用输出相对输入的相量差，CSV 同时提供 `phase_wrapped_deg` 和不跨失败点连接的 `phase_unwrapped_deg`。探头、电缆和通道延迟均会包含在相位里；先做直通基线或 deskew，才能把相位解释为 DUT 本体特性。

## 一个 step 只做一件事

例如设置电源电压不会顺手打开输出：

```toml
[[steps]]
kind = "power.set"
channel = 1
voltage_v = 3.3
current_limit_a = 0.1
```

如果要开关输出，必须另写：

```toml
[[steps]]
kind = "power.output"
channel = 1
state = "on"
```

这看起来啰嗦，但它能避免现场调试时被隐藏动作吓到。

## 常见 `run check` 报错

### step kind 拼错

错误 plan：

```toml
[[steps]]
kind = "scope.captur"
```

输出类似：

```text
wavebench: steps[0].kind 'scope.captur' is not supported. Did you mean 'scope.capture'? Supported kinds: ... Run `python -m wavebench run schema` for field details.
```

修法：改成 `scope.capture`，或先运行 `run schema` 查看支持列表。

### 字段名拼错

错误 plan：

```toml
[[steps]]
kind = "sleep"
duraton_s = 0.5
```

输出类似：

```text
wavebench: steps[0] has unknown key(s): duraton_s. Did you mean 'duraton_s' -> 'duration_s'? Allowed keys: duration_s, kind. Run `python -m wavebench run schema` for field details.
```

修法：改成 `duration_s`。

### 缺少必填字段

错误 plan：

```toml
[[steps]]
kind = "power.set"
voltage_v = 3.3
```

输出类似：

```text
wavebench: steps[0] power.set missing required field 'current_limit_a'. Required fields: voltage_v, current_limit_a. Optional fields: channel. Run `python -m wavebench run schema` for examples.
```

修法：补上 `current_limit_a`。

## `scope.capture` 的质量检查和断言

`scope.capture` 默认只采集。需要截图、质量检查或断言时都要显式写：

```toml
[[steps]]
kind = "scope.capture"
channel = 1
label = "duty_50"
window_frequency_hz = 10000
target_cycles = 10
screenshot = true
quality_gate = true
auto_recover = true

[steps.expect]
frequency_estimate_hz = { min = 9500, max = 10500 }
duty_cycle = { min = 0.45, max = 0.55 }
voltage_vpp_v = { min = 2.8, max = 3.8 }
```

含义：

- `screenshot = true`：本次采集额外保存 `screenshot.png`，并在 `metadata.json.files.screenshot` 记录路径；截图失败不会吞掉波形包。
- `quality_gate = true`：把采集质量状态写入 `run.json` / `summary.csv`。
- `auto_recover = true`：如果质量有 warning，显式执行 `scope.auto` 并重采，最多次数由 `[quality].auto_recover_attempts` 控制。
- `[steps.expect]`：对采集摘要指标做 min/max 断言。断言失败时 run 标记为 `failed`，但采集包仍保留。

## run 输出字段契约

`run plan` 会生成：

```text
data/runs/YYYYMMDD_HHMMSS_<label>/
  plan.toml
  run.json
  summary.csv
  steps/
    00_<kind>.json
    01_<kind>.json
```

### `run.json`

稳定字段：

| 字段 | 含义 |
|---|---|
| `status` | 整个 run 的状态，常见值：`ok`、`failed`。 |
| `plan` | plan 路径、实验名、label 等摘要。 |
| `safety` | safety guard 的检查结果；未启用时为空或简短状态。 |
| `restore` | source restore 的 snapshot / restore 状态；未启用时为空或简短状态。 |
| `steps` | 每个 step 的执行记录。 |

`steps[]` 常见字段：

| 字段 | 含义 |
|---|---|
| `index` | step 序号。 |
| `kind` | step kind，例如 `scope.capture`。 |
| `status` | step 状态，常见值：`ok`、`failed`。 |
| `artifact` | 该 step 产生的结构化结果。 |
| `error` | 失败时的错误类型和消息。 |

`scope.capture` 的 `artifact` 常见字段：

| 字段 | 含义 |
|---|---|
| `package` | 普通采集包路径。 |
| `metadata` | 采集包 `metadata.json` 路径。 |
| `quality` | 质量摘要，含 `status`、warnings 和关键测量指标。 |
| `quality_recovery` | 自动恢复尝试记录；未启用或未触发时可能不存在。 |
| `expect` | `[steps.expect]` 的检查结果；未配置时可能不存在。 |

`sweep.frequency_response` 的 `artifact.frequency_response` 常见字段：

| 字段 | 含义 |
|---|---|
| `csv` | run 根目录的逐点 `frequency_response.csv`。 |
| `fit_json` | 启用拟合时的 `frequency_response_fit.json`；未启用则为空。 |
| `captures` | 每个已有双通道采集包与 metadata 的引用，供报告和审计使用。 |
| `failed_point_count` / `warning_point_count` | 频点失败与质量 warning 数量。 |

### `summary.csv`

`summary.csv` 面向脚本和表格查看。常见列：

| 列 | 含义 |
|---|---|
| `step_index` | step 序号。 |
| `kind` | step kind。 |
| `status` | step 状态。 |
| `package` | scope capture 产生的采集包路径。 |
| `metadata` | 对应 `metadata.json` 路径。 |
| `quality_status` | 质量状态，例如 `ok`、`warning`、`ok_by_consistency`。 |
| `quality_warnings` | 质量 warning 摘要。 |
| `expect_status` | 断言状态，例如 `ok`、`failed`。 |
| `expect_failures` | 断言失败原因摘要。 |

后续分析脚本应优先读取 `run.json`。`summary.csv` 适合快速查看和导入表格软件。

## 生成离线报告

跑完 run 后，用：

```powershell
python -m wavebench run report data/runs/<run_dir>
```

`run report` 只读已有 `run.json`、`summary.csv`、采集包 `metadata.json`、`ch*.npy` 和截图，不连接仪器，也不修改原始采集数据。

HTML 报告当前会汇总：

- `摘要 / Summary`：run 状态、失败步骤、采集数量、警告、恢复状态、主频率和主 Vpp。
- `实验证据摘要 / Run evidence summary`：source 步骤、scope capture、DMM 读数、run.json、summary.csv、截图和波形预览数量。
- `证据时间线 / Evidence timeline`：按 step 展示 source/scope/DMM/sleep 的证据摘要。
- `扫频摘要 / Sweep summary`：当 run 里有多点 `scope.capture` 或 sweep label 时显示，列出每个频点的 label、status、quality、expect、FFT、frequency、Vpp、FFT peak、peak amplitude 和 THD。
- `频率响应 / Frequency response`：当 run 根目录存在 `frequency_response.csv` 时显示幅频、相频、线性增益拟合对比、逐点表格、拟合公式/参数，并发现每点的截图和原始采集包链接。
- `验收摘要 / Acceptance summary` 与 `预期 vs 实测 / Expected vs measured`：汇总 `[steps.expect]` 和 `[steps.expect_fft]` 的验收结果。
- `DMM 读数 / DMM readings`、`信号分析 / Signal analysis`、`波形预览 / Waveform previews`、`截图 / Screenshots`。

安装 `.[pdf]` 后可在同一离线命令中导出 PDF：

```powershell
python -m wavebench run report data/runs/<run_dir> --pdf
python -m wavebench run report data/runs/<run_dir> --output reports/lowpass.html --pdf --pdf-output reports/lowpass.pdf
```

PDF 会嵌入 HTML 中可见的截图、静态 SVG 曲线和表格，适合把报告发给他人或归档。CSV、拟合 JSON、NPY 和完整采集包仍是独立证据文件；PDF 中保留它们的链接，但不把大型原始波形伪装成可见图表。WeasyPrint 还需要操作系统提供 Cairo、Pango、GDK-PixBuf 和合适的中文字体。

典型 sweep 流程：

```powershell
python -m wavebench run template source-scope-sweep --frequencies 100,1000,10000 --output plans/source_scope_sweep.toml
python -m wavebench run check  --config wavebench.toml --plan plans/source_scope_sweep.toml
python -m wavebench run verify --config wavebench.toml --plan plans/source_scope_sweep.toml
python -m wavebench run plan   --config wavebench.toml --plan plans/source_scope_sweep.toml
python -m wavebench run report data/runs/<run_dir>
```

## 现场写 plan 的顺序

建议按这个顺序来：

1. 先写 `[experiment]` 和必要的 `[safety]` / `[restore]`。
2. 每次只加 1~2 个 `[[steps]]`。
3. 运行 `run check`。
4. 确认摘要里没有隐藏动作。
5. 接上仪器后再运行 `run plan`。
6. 跑完先看 `run.json.status` 和 `summary.csv`，再看具体采集包。

别急着写很聪明的流程。电赛现场，朴素的记录更可靠。
