# WaveBench 数据输出格式

## 设计目标

数据输出围绕三个要求设计：

1. 结果便于阅读；
2. Python 可以直接分析；
3. 采集过程和原始证据可以追溯。

当前阶段只支持：

```text
CSV + NPY + JSON + commands.log
```

暂不考虑 MATLAB 兼容、Parquet、数据库和自动报告。

## 当前实现状态（2026-04-29）

`9c9cc32 feat: fetch and capture RTM2032 waveforms` 已实现单通道采集包输出：

```text
data/raw/20260429_162450_square_1khz/
├─ ch1.csv
├─ ch1.npy
├─ metadata.json
└─ commands.log
```

实机样例来自 RTM2032 自带约 1 kHz 方波：

```text
samples = 10000
time    = -1.000000e-03 .. 9.998000e-04 s
dt      = 2.000000e-07 s
voltage = about -0.5 .. 0.62 V
```

当前 `capture` 已推进为 `SINGle + *OPC?` 单次采集后再读取波形并打包。


## 采集包

每次采集生成一个独立目录，称为「采集包」。

单通道示例：

```text
data/raw/20260429_011530_ch1/
├─ ch1.csv
├─ ch1.npy
├─ metadata.json
└─ commands.log
```

以后如果支持多通道：

```text
data/raw/20260429_011530_ch1_ch2/
├─ ch1.csv
├─ ch1.npy
├─ ch2.csv
├─ ch2.npy
├─ metadata.json
└─ commands.log
```

如果以后支持截图，可增加：

```text
screenshot.png
```

## 目录命名规则

默认规则：

```text
YYYYMMDD_HHMMSS_<label>
```

单通道：

```text
20260429_011530_ch1
```

多通道：

```text
20260429_011530_ch1_ch2
```

如果用户传入 `--basename`：

```bash
wavebench scope capture --channel 1 --basename opamp_gain_test
```

则目录名：

```text
20260429_011530_opamp_gain_test_ch1
```

## 默认输出目录

默认输出目录：

```text
data/raw/
```

`data/` 不进入 git。

仓库的 `.gitignore` 已包含：

```gitignore
data/
```

如果用户传参：

```bash
wavebench scope capture --out data/captures
```

则采集包放入指定目录下：

```text
data/captures/20260429_011530_ch1/
```

## CSV 文件

CSV 用来给人看，也方便 Excel、Origin、Python 快速读取。

单通道 CSV：

```csv
index,time_s,voltage_v
0,-4.998e-7,0.0012
1,-4.996e-7,0.0011
2,-4.994e-7,0.0010
```

列定义：

| 列名 | 含义 |
|---|---|
| `index` | 样本序号 |
| `time_s` | 时间，单位秒 |
| `voltage_v` | 电压，单位伏 |

不保存「只有电压」的 CSV。缺少时间轴的数据无法可靠追溯。

当前每通道一个 CSV：

```text
ch1.csv
ch2.csv
```

如果多通道时间轴一致，后续可以评估合并 CSV：

```csv
index,time_s,ch1_voltage_v,ch2_voltage_v
```

## NPY 文件

NPY 用于 Python 快速读取。

当前保存二维数组：

```python
array.shape == (N, 2)
```

列定义：

```text
[:, 0] = time_s
[:, 1] = voltage_v
```

读取示例：

```python
import numpy as np

data = np.load("ch1.npy")
t = data[:, 0]
v = data[:, 1]
```

每通道一个 NPY：

```text
ch1.npy
ch2.npy
```

## `metadata.json`

JSON 只保存元信息，不保存大数组。

示例结构：

```json
{
  "wavebench": {
    "version": "0.1.0",
    "mode": "scope.capture",
    "timestamp": "2026-04-29T01:15:30+08:00"
  },
  "instrument": {
    "kind": "scope",
    "driver": "RTM2032Scope",
    "resource": "TCPIP::192.0.2.10::INSTR",
    "idn": "Rohde&Schwarz,RTM2032,..."
  },
  "acquisition": {
    "operation": "capture",
    "channel": 1,
    "triggered_single": true,
    "reset_before_run": false,
    "front_panel_control": false
  },
  "waveform": {
    "format": "REAL",
    "byte_order": "LSBF",
    "points_mode": "DMAX",
    "points": 5000,
    "x_start_s": -4.998e-7,
    "x_stop_s": 5.0e-7,
    "x_increment_s": 2.0e-10,
    "y_unit": "V"
  },
  "files": {
    "csv": "ch1.csv",
    "npy": "ch1.npy",
    "commands": "commands.log",
    "screenshot": null
  },
  "notes": ""
}
```

必须记录：

- `operation`：区分 `fetch` 和 `capture`；
- `reset_before_run`；
- `front_panel_control`；
- VISA `resource`；
- `idn`；
- `points`；
- `x_start_s`；
- `x_stop_s`；
- `x_increment_s`。

公开分享数据时，`resource` 可以脱敏。

## fetch 与 capture 的差异

`fetch`：不触发新采集，只读取当前波形。

```json
"acquisition": {
  "operation": "fetch",
  "triggered_single": false
}
```

`capture`：触发一次单次采集，等待完成，再读取波形。

```json
"acquisition": {
  "operation": "capture",
  "triggered_single": true
}
```

这两个必须区分，否则以后无法判断数据来源。

## commands.log

`commands.log` 记录关键 SCPI 命令和响应，用于排查仪器控制问题。

示例：

```text
2026-04-29T01:15:30.123 WRITE *CLS
2026-04-29T01:15:30.130 QUERY *IDN?
2026-04-29T01:15:30.145 RESP Rohde&Schwarz,RTM2032,...
2026-04-29T01:15:30.160 WRITE FORM REAL
2026-04-29T01:15:30.170 WRITE FORM:BORD LSBF
2026-04-29T01:15:30.180 WRITE CHAN1:DATA:POIN DMAX
2026-04-29T01:15:30.200 WRITE SING
2026-04-29T01:15:30.210 QUERY *OPC?
2026-04-29T01:15:31.532 RESP 1
2026-04-29T01:15:31.540 QUERY CHAN1:DATA:HEAD?
2026-04-29T01:15:31.550 RESP -4.998E-07,5.000E-07,5000,1
2026-04-29T01:15:31.570 QUERY_BINARY CHAN1:DATA?
```

当前使用简单文本日志，不额外引入复杂日志框架。

commands.log 的目的不是做漂亮日志，而是回答一个问题：

> 脚本刚才到底对仪器说了什么？

## 截图预留

当前已支持显式截图保存。未启用时，采集包不写截图；启用后 metadata 记录截图文件路径。

```json
"files": {
  "screenshot": null
}
```

启用命令：

```bash
wavebench scope capture --channel 1 --screenshot
```

生成：

```text
screenshot.png
```

如果截图失败，波形采集结果仍然保留，`metadata.json` 会写入 `screenshot_error`，并将 `files.screenshot` 记为 `null`。

## 当前结论

```text
一个采集包 = 一个目录
每通道一个 CSV
每通道一个 NPY
一个 metadata.json
一个 commands.log
```

单通道采集包：

```text
data/raw/20260429_011530_ch1/
├─ ch1.csv
├─ ch1.npy
├─ metadata.json
└─ commands.log
```

CSV：

```text
index,time_s,voltage_v
```

NPY：

```python
shape = (N, 2)
columns = [time_s, voltage_v]
```

JSON：只放元信息，不放大数组。

commands.log：记录关键 SCPI 命令与响应。


## 数据质量摘要字段补充

`metadata.json` 的 `waveform.summary` 中包含：

- `frequency_estimate_hz`: 估计频率。
- `frequency_method`: 频率估计方法，例如 `hysteresis_rising_crossing` 或 `fft_peak`。
- `estimated_cycles`: 当前采集窗口内估计包含的周期数。
- `quality_warnings`: 数据质量提示列表；例如少于 2 个周期时给出 `low_cycle_count`，提示频率估计可能不可靠。

- `expected_frequency_hz`: 用户通过 CLI/config 给出的预期频率。
- `frequency_error_ratio`: 估计频率相对预期频率的误差比例。
- `frequency_in_tolerance`: 是否落在给定频率容差内。
- `frequency_mismatch`: 估计频率偏离预期频率时的提示。


## 2026-04-29 实机同步：质量摘要与控制字段

当前 `metadata.json` 的 `operation` 会记录本次采集动作：

```json
{
  "command": "scope capture",
  "channel": 1,
  "label": "sweep_smoke",
  "triggered_single": true,
  "time_range_s": 0.01,
  "expected_frequency_hz": null,
  "target_cycles": 10.0,
  "window_frequency_hz": 1000.0,
  "frequency_tolerance_ratio": 0.05
}
```

`waveform.summary` 会记录基础质量摘要：

```json
{
  "voltage_min_v": -2.54,
  "voltage_max_v": 2.5,
  "voltage_mean_v": -0.0167,
  "voltage_rms_v": 1.756,
  "voltage_vpp_v": 5.04,
  "frequency_estimate_hz": 9382.33,
  "frequency_method": "hysteresis_rising_crossing",
  "estimated_cycles": 93.8,
  "duty_cycle": null,
  "rise_time_s": null,
  "fall_time_s": null,
  "expected_frequency_hz": null,
  "frequency_error_ratio": null,
  "frequency_in_tolerance": null,
  "quality_warnings": []
}
```

频率估计优先使用滞回上升沿，失败时回退到 NumPy FFT peak。若采集窗口少于约 2 个周期，`quality_warnings` 会包含 `low_cycle_count`，表示频率估计可能不可靠。若设置了 `expected_frequency_hz` 且估计频率超出容差，会包含 `frequency_mismatch`。

失败采集包命名为 `*_failed`，至少包含：

```text
metadata.partial.json
error.txt
commands.log
```

配置错误或连接未建立前的失败不会生成采集包；连接成功后发生的采集/仪器/数据错误会生成 failed package，便于复盘 SCPI 序列和仪器错误队列。


## 多通道采集包

当 `scope capture` 接收多个 `--channel` 参数时，WaveBench 会生成一个采集包，并按通道分别写文件：

```text
<package>/
  ch1.npy
  ch2.npy
  metadata.json
  commands.log
```

`metadata.json` 使用 `channels` 字段保存逐通道数据：

```json
{
  "operation": {
    "command": "scope capture",
    "channels": [1, 2],
    "trigger_mode": "single_acquisition"
  },
  "channels": {
    "1": {
      "header": {},
      "summary": {}
    },
    "2": {
      "header": {},
      "summary": {}
    }
  },
  "files": {
    "1": {"npy": ".../ch1.npy"},
    "2": {"npy": ".../ch2.npy"}
  }
}
```

当前多通道语义是：配置全部目标通道后只执行一次 `SINGle` 和一次 `*OPC?`，再顺序读取各通道。因此各通道属于同一次 acquisition；读取和文件写入仍按通道顺序进行。WaveBench 不假设不同通道接入的是同一个信号，频率、幅度和 warning 都按通道独立计算。

每个通道读取成功后，CSV/NPY 会先写临时文件，再用 `os.replace()` 原子提升为最终文件。后续通道失败时，采集包改名为 `*_failed`，已完成通道继续保留；`metadata.partial.json` 额外记录 `completed_channels`、`failed_channel`、`stage`、已完成通道的 metadata/files，以及可用时的 best-effort 截图。


对于看起来像方波 / PWM / 阶跃的波形，`waveform.summary` 还会补充轻量边沿指标：

```json
{
  "duty_cycle": 0.25,
  "rise_time_s": 1.6e-5,
  "fall_time_s": 2.4e-5
}
```

说明：

- `duty_cycle` 是高电平占空比，范围约 `0.0 ~ 1.0`。
- `rise_time_s` 使用 10% → 90% 定义。
- `fall_time_s` 使用 90% → 10% 定义。
- 对正弦或无法可靠识别为两电平的波形，这三个字段返回 `null`，不强行给数。


## Run plan 质量恢复与指标断言输出

`run plan` 的流程级输出写入：

```text
data/runs/YYYYMMDD_HHMMSS_<label>/
  plan.toml
  run.json
  summary.csv
  steps/
    00_<kind>.json
```

`scope.capture` step 的 artifact 会引用采集包，而不是复制波形大文件：

```json
{
  "package": "data/raw/20260430_...",
  "metadata": "data/raw/20260430_.../metadata.json",
  "quality": {
    "status": "ok",
    "warnings": [],
    "frequency_estimate_hz": 10000.0,
    "estimated_cycles": 10.0,
    "points_per_cycle": 1000.0,
    "voltage_vpp_v": 3.3,
    "voltage_mean_v": 0.0,
    "duty_cycle": 0.5,
    "frequency_error_ratio": 0.0
  }
}
```

当 step 设置 `quality_gate = true` 且没有自动恢复时，会额外写入：

```json
{
  "quality_gate": {
    "status": "warning",
    "warnings": ["low_cycle_count"]
  }
}
```

当 step 同时设置 `auto_recover = true`，并且初次采集存在 warning，会额外写入：

```json
{
  "quality_recovery": {
    "trigger": "quality_warnings",
    "max_auto_recover_attempts": 2,
    "attempts": [
      {"index": 0, "kind": "initial", "package": "...", "metadata": "...", "quality": {}},
      {"index": 1, "kind": "auto_retry", "package": "...", "metadata": "...", "quality": {}}
    ],
    "consistency": {
      "status": "consistent",
      "required_captures": 2,
      "actual_captures": 2,
      "checks": {}
    }
  }
}
```

如果多次 warning 采集的可比较指标稳定，最终 artifact 的 `quality.status` 会变成 `ok_by_consistency`，并带上 `trusted_by_consistency = true`。

当 step 设置 `[steps.expect]`，会额外写入：

```json
{
  "expect": {
    "status": "failed",
    "checks": {
      "voltage_mean_v": {
        "status": "failed",
        "value": 3.19,
        "limits": {"min": 4.8, "max": 5.2},
        "reasons": ["below min 4.8"]
      }
    },
    "failures": ["voltage_mean_v: 3.19 below min 4.8"]
  }
}
```

`expect.status = failed` 会让 step status 变成 `failed`，也会让整个 `run.json.status` 变成 `failed`。采集包仍然保留，方便复盘失败原因。

`summary.csv` 当前包含这些流程级列：

```text
index,kind,status,package,metadata,quality_status,quality_warnings,recovered,expect_status,expect_failures
```

## run plan 输出包

`run plan` 不是采集包本身，而是一次实验流程记录。它会引用普通 `scope.capture` 采集包，不复制大波形文件。

目录结构：

```text
data/runs/YYYYMMDD_HHMMSS_<label>/
├─ plan.toml
├─ run.json
├─ summary.csv
└─ steps/
   ├─ 00_power_status.json
   └─ 01_scope_capture.json
```

字段约定：

- `run.json.status`：整个流程状态，常见值是 `ok` / `failed`。
- `run.json.steps[]`：逐 step 记录，包含 `index`、`kind`、`status`、`artifact`，失败时包含 `error`。
- `run.json.restore`：如果启用 `[restore] source_state = true`，这里记录 snapshot 与 restore 结果。
- `run.json.provenance`：记录运行 provenance schema、规范化 plan 哈希、执行意图和频响采集同步等级。
- `run.json.provenance.instrument_io`：记录 run 使用的受保护 transport 原生 I/O 计数；不会从 `commands.log` 反推。
- `run.json.provenance.state_guard`：记录 Source / Power 基础控制写入的 expected state；状态漂移失败的 `error.details` 另保存 `expected`、`actual` 和 `diff`。
- `run.json.provenance.execution_intent`：记录 `wavebench.execution_intent.v1` 的 plan/config/payload 摘要和规范化操作列表，不写入原始资源地址。
- `scope.capture` step 的 `artifact.package` 指向普通采集包目录。
- `scope.capture` step 的 `artifact.quality` 保存质量摘要。
- `scope.capture` step 的 `artifact.expect` 保存 `[steps.expect]` 检查结果。

`summary.csv` 是 `run.json` 的轻量表格视图。它适合肉眼扫一遍，但正式脚本应优先读 `run.json`。

常见列：

```text
step_index,kind,status,package,metadata,quality_status,quality_warnings,expect_status,expect_failures
```

断言失败时，run 会标记为 `failed`，但采集包仍会保留。这样失败结果也能被复盘，而不是只得到一条错误消息。

### `provenance.instrument_io`

当 run 通过 WaveBench factory 打开仪器 transport 时，`run.json` 会写入版本化的 I/O 证据：

```json
{
  "schema": "wavebench.run_instrument_io.v1",
  "coverage": "run_factory_transports",
  "instrument_mutation_writes": 0,
  "instrument_mutation_writes_completed": 0,
  "instruments": {
    "source": {
      "schema": "wavebench.instrument_io.v1",
      "access": "read_only",
      "counters": {
        "query_calls": 2,
        "binary_query_calls": 0,
        "write_requests": 0,
        "write_transmitted": 0,
        "write_completed": 0,
        "blocked_write_requests": 0,
        "instrument_mutation_writes": 0
      }
    }
  }
}
```

计数含义：

- `*_requests`：调用方请求执行的次数；
- `*_transmitted`：已委托给具体 transport 的次数；底层抛出异常时仍按可能已发送计数；
- `*_completed`：具体 transport 调用未抛出异常的次数；不表示仪器已经完成内部处理；
- `blocked_*`：在访问策略边界被拒绝、未调用具体 transport 的次数；
- `instrument_mutation_writes`：文本和二进制写入的 `transmitted` 总数。

`SerialTransport.query()` 内部为完成查询而发送的串口字节只计入 `query_calls`，不会误报为 mutation write。该证据覆盖 run 使用 factory 打开的 transport；discovery、doctor 和独立插件探测虽然也取得资源租约，但不写入该 run provenance，不能据此宣称已被 run I/O 计数覆盖。

## 双通道频率响应产物

单一 `sweep.frequency_response` 保持在本次 run 根目录额外写入下列产物，不覆盖普通 `scope.capture` 包：

```text
data/runs/YYYYMMDD_HHMMSS_<label>/
├─ frequency_response.csv
├─ frequency_response_fit.json   # 仅配置 [steps.fit] 时存在
├─ frequency_response_calibration.csv  # 自动或离线二维校准成功时存在
├─ frequency_response_calibration.json
├─ frequency_response_calibration_fixed.csv # 默认 Q4.12 定点审计表
├─ frequency_response_calibration_q.coe
└─ frequency_response_calibration_q.mem
```

同一 run 含多个频响 step 时，根目录新增 `frequency_responses.json`（`schema_version = 1`）。其 `responses[]` 以唯一 `label`、`step_index`、相对 `directory` 和各派生产物引用描述每个响应；每个响应保存在 `frequency_response/<step>_<label>/`。旧 run 没有 manifest 时仍按根目录单响应产物读取。

`frequency_response.csv` 每请求一个频点就原子刷新一次，因此 source 设频失败、scope 采集失败或分析失败时，前序记录和当前失败行仍会保留。每个原始 `metadata.json.operation.min_signal_vpp` 记录当点采用的低信号门限；普通默认是 20 mVpp，频响 step 可显式降低，但不会抑制频率、削顶或其他质量 warning。稳定基础列为：

```text
index,case_id,acquisition_id,capture_sync_grade,requested_source_vpp,requested_vpp,reference_plane,signal_level_evidence,quality_metrics,plan_hash,amplitude_index,requested_frequency_hz,reference_frequency_hz,response_frequency_hz,
reference_amplitude_peak_v,response_amplitude_peak_v,reference_vpp_v,response_vpp_v,
gain_linear,gain_db,phase_wrapped_deg,phase_unwrapped_deg,
baseline_gain_db,baseline_phase_unwrapped_deg,gain_linear_corrected,gain_db_corrected,
phase_wrapped_corrected_deg,phase_unwrapped_corrected_deg,
adaptive_level,adaptive_parent_start_hz,adaptive_parent_stop_hz,quality_retry_count,
initial_capture_package,initial_metadata_path,retry_capture_package,retry_metadata_path,
status,failure_reason,exclusion_reason,warnings,error,capture_package,metadata_path
```

- `gain_linear` 是输出基波峰值 / 输入基波峰值；`gain_db = 20 * log10(gain_linear)`。
- `case_id` 标识计划中的请求网格点；`acquisition_id` 标识一次物理双通道采集，autoscale 重测会产生新的 ID。`capture_sync_grade` 当前为 `waveforms_atomic_aux_best_effort`，表示两路波形来自同一次 acquisition，但截图等辅助证据不宣称同帧。
- `amplitude_index` 从零开始标识请求 Vpp 切片；固定幅值的旧 run 也写为 `0`。`requested_source_vpp` 和兼容保留的 `requested_vpp` 是信号源请求值；`reference_vpp_v` 是 CH1 实测输入值，只用于审计和诊断，不能替代 LUT 的幅值轴。`reference_plane` 和 `signal_level_evidence` 记录测量平面及换算假设，默认不做换算。
- `phase_wrapped_deg` 在 `[-180, 180)`；`phase_unwrapped_deg` 对连续成功点展开，绝不跨失败点连接。
- `status` 为 `ok`、`warning` 或 `failed`。失败行的数值字段为空，`error` 保存可读错误，不能被误当作零增益或零相位。
- `failure_reason` 和 `exclusion_reason` 说明失败点为何不进入拟合或校准；`quality_metrics` 保存 RMS、峰值、峰值因子和有限样本比例等非破坏性质量指标。
- `capture_package` / `metadata_path` 指向每个成功的同步双通道原始证据。频响采集强制写入两路 NPY 与 metadata，普通可选 CSV 和截图仍遵循该 step 的 `save_csv` / `screenshot` 设置。
- 开启拟合后，CSV 还会增加 `fit_<method>_gain_linear` 与 `fit_<method>_residual` 列；这些值只对应实际有效频点。
- 原始 `gain_*` 和 `phase_*` 永远不被软件校正覆盖。直通基线开启时，`baseline_*` 是 `log10(frequency)` 域的插值基线，`*_corrected` 是派生结果；二维校准优先使用校正增益。
- `adaptive_level = 0` 是初始网格；正数表示加密层级，父区间列记录中点来源。失败点仍保留为失败证据。

`frequency_response_baseline.json` 仅在配置 `[steps.baseline]` 时存在，记录独立基线 response、校正模式、每个 Vpp 的有效域、已用切片和由展开相位拟合的估算延迟。它只证明软件后处理，不改变仪器硬件 deskew。

`frequency_response_fit.json` 是供报告、调试脚本和复算使用的 JSON 文档。它声明 `x_transform = "log10(frequency_hz / Hz)"`、有效范围、被排除的点、拟合公式、参数、误差指标和用于图表的频率/线性增益曲线。它不在定义域外外推：调试脚本应先检查 `valid_domain_hz`。

启用多幅值采集时，传统一维 `[steps.fit]` 文档会选择最低 `requested_vpp` 切片，并通过 `fit_amplitude_vpp` 与 `fit_note` 声明这一点；二维部署/校准必须读取下述校准文件，而不是拼接一维拟合结果。

### 二维校准 LUT

`frequency_response_calibration.csv` 是后端最直接的浮点 LUT 输入；行的稳定列为：

```text
frequency_hz,requested_vpp,fitted_gain_db,correction_db,correction_linear,
correction_limited,slope_limited
```

- 每个 `(requested_vpp, frequency_hz)` 组合一行；频率与幅值节点来自有效测量的共同网格。
- `fitted_gain_db` 是 dB 域平滑样条预测值，不是原始单点噪声的机械复制。
- `correction_db = target_gain_db - fitted_gain_db`，`correction_linear = 10^(correction_db / 20)`。
- `correction_limited` 表示命中 `correction_min_db` / `correction_max_db`；`slope_limited` 表示为满足 `max_slope_db_per_octave` 而受限。后端不应把受限标记忽略后再次放大。

`frequency_response_calibration.json`（`schema_version = 1`）是完整审计和公式载体，包含：

- 源 `frequency_response.csv` 路径、`configuration`、目标模式与最终 `target_gain_db`；
- 频率/Vpp 有效域、采用的实测网格、样条平滑度候选的留点误差、频率及幅值留点验证 RMSE；
- 补偿和斜率限制命中数、完整的 `lut` 行；
- 每个请求 Vpp 的 `chebyshev` 分段。每段以 `x = log10(frequency_hz / Hz)` 为自变量，`x_start` / `x_stop` 映射到 `t ∈ [-1, 1]`，并按 `G_dB = Σ c_k T_k(t)` 计算。

插值约定是「频率方向使用平滑样条、相邻请求 Vpp 方向使用线性插值」；频率或 Vpp 超出 `valid_domain` 时不得外推。

### 定点部署文件

浮点 LUT 始终是审计事实源；自动或离线校准默认还导出其 `correction_linear` 的部署副本：`frequency_response_calibration_fixed.csv`、Xilinx `frequency_response_calibration_q.coe` 和每行一个固定字宽十六进制字的 `frequency_response_calibration_q.mem`。

- 默认是 16 位有符号二补码 `Q4.12`、半值远离零的最近整数、幅值主序 `linear_index = amplitude_index * frequency_count + frequency_index`、`overflow = error`，绝不静默饱和。
- fixed CSV 逐地址记录 Vpp/频率索引、原值、量化整数/十六进制、量化值和逐点误差；calibration JSON 的 `fixed_point` 记录格式、配置、编码、地址映射、文件路径和最大绝对量化误差。
- `[calibration.fixed_point]` 可覆盖 `formats`（`csv` / `coe` / `mem`）、`word_width`、`fractional_bits`、`layout`、`rounding` 与 `overflow`；`saturate` 必须显式选择。

### 交互式三维 HTML 资源

安装 `WaveBench[report3d]` 后，对至少 2 个请求 Vpp × 2 个频率节点的 response 运行 `run report` 会额外写入：

```text
<report-output-directory>/
├─ report.html
└─ report-assets/
   ├─ plotly.min.js
   └─ manifest.json
```

- 所有 response 共用同一份本地 `plotly.min.js`，HTML 只写相对路径，不访问 CDN。`manifest.json` 的 `interactive_assets[]` 记录 `kind = "plotly.js"`、相对 `path` 和生成时的 `exists` 状态。
- X 坐标使用 `log10(frequency_hz)` 几何位置，但刻度和 hover 显示实际 Hz；Y 为 `requested_vpp`；Z 优先使用 `gain_db_corrected` / `gain_linear_corrected`，也可切换到原始增益和 dB/V/V。
- 曲面矩阵只来自 CSV 已有节点。`failed` 点写为缺失值并留洞，绝不补点或域外外推；圆点才表示真实采样。`warning` 点和成功自动恢复点使用独立标记，hover 保留首次 warning、重试次数、首次与最终采集路径。
- 单幅值或不足 2 × 2 的 response 不生成伪曲面，只保留静态 Bode 图。缺少 `report3d` extra 时报告仍能生成，并显示安装提示。
- 移动 `report.html` 时必须同时携带同级 `report-assets/`。PDF compact 路径完全不加载 Plotly，继续作为单文件静态视觉归档。
