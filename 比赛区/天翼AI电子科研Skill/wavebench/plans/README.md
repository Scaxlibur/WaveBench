# Run plan 示例

这里包含实验 RunPlan 和独立离线分析配方。文件名里有 `example`，不代表 RunPlan 可以在没有仪器时直接执行；很多计划会设置 source、打开输出、触发采集，或者依赖一份本地 baseline。`*_recipe.toml` 使用 `analysis` 命令处理已有采集包，不作为 RunPlan 执行。

## 先做离线检查

`run check` 只解析计划，不连接仪器：

```bash
wavebench run template --list
wavebench run check --plan plans/example_scope_expect_quality.toml
```

`run verify` 会读取配置并查询相关仪器，适合执行前预检。`run plan` 会进行真实实验，执行前应确认接线、scope coupling、输出状态、保护限值和 `[restore]` 范围。`run report` 和 `run calibrate` 读取已有产物，不需要再次连接仪器；校准相关拟合需要安装 `.[analysis]`。

`example_signal_processing_pipeline.toml` 包含 FIR 带阻、IIR 高通、因果和零相位处理，需要安装 `.[analysis]`。`run check` 只在 Plan 选择需要 SciPy 的算子时检查该可选依赖。

## 信号处理功能展示

操作步骤与结果判读见[使用信号处理流水线](../docs/how-to/signal-processing.md)。

`example_analysis_resources.toml` 是执行资源配置，不是 RunPlan 或处理配方。可通过 `--analysis-resources plans/example_analysis_resources.toml` 显式选用；字段与兼容边界见[资源预算说明](../docs/reference/run-schema.md#分析资源预算)。

[完整 RunPlan 示例](example_signal_processing_pipeline.toml) 采集 CH1 一次，然后对同一份原始 NPY 执行三个独立分析步骤：

| 步骤 ID | 展示内容 | 报告产物 |
| --- | --- | --- |
| `spectrum_main` | 时域统计、去直流、FIR 带阻、IIR 高通、Hann 窗、FFT 与谐波验收 | 原始波形、滤波频谱与 THD |
| `processed_main` | Savitzky–Golay 平滑、采样率减半、FFT、多峰检测 | 处理后波形、频谱、峰表与峰标记 |
| `density_main` | Welch PSD、带内均方值／RMS、排除基波频带后的噪声 RMS | PSD 曲线及频带验收结果 |

演示输入为约 1 Vpp 的 1 kHz 正弦，均匀采样率至少 20 kSa/s、至少 4096 点。信号源与采集参数需事先配置；示例不会设置或开启信号源。频率误差受实际记录长度和 FFT bin 间距影响，验收阈值仅供演示，应按实际输入调整。采集或前两个分析步骤失败时继续尝试后续分析，最终 run 仍记录失败。

```bash
wavebench run check --plan plans/example_signal_processing_pipeline.toml
```

完成接线确认与 `run verify` 后，通过 `run plan` 执行。已有运行产物可以直接生成报告：

```bash
wavebench run report data/runs/<run-dir>
wavebench analysis report data/runs/<run-dir> --output data/processing_comparison.html
```

报告可比较原始／处理后波形及两条 FFT 曲线，PSD 使用独立单位显示。派生文件位于 run 的 `processing/`，原始 NPY 保持不变。独立比较报告的输出文件必须尚不存在。

只有历史采集包时，可使用 [FFT 配方](example_analysis_recipe.toml)、[PSD 配方](example_psd_recipe.toml) 或[平滑／重采样／峰值配方](example_processed_recipe.toml)：

```bash
wavebench analysis run --capture data/raw/<capture-dir> --channel 1 --recipe plans/example_processed_recipe.toml --output data/analysis_demo
wavebench analysis report data/analysis_demo --output data/analysis_demo.html
```

## 计划分类

### 通用示例

这些文件适合阅读和改成自己的 plan，但仍需要真实设备才能执行：

- `example_scope_expect_quality.toml`
- `example_signal_processing_pipeline.toml`
- `example_source_scope_dmm_report.toml`
- `example_dmm_acv_source_smoke.toml`
- `demo_dg4202_10k_screenshot_report.toml`

### 基础流程和电源 smoke

- `closure_sine_1k.toml`
- `closure_sine_1k_fft.toml`
- `closure_triangle_1k.toml`
- `dg4202_duty_10k_power_ch2_check.toml`
- `dp800_scope_probe_voltage_steps.toml`

这些计划会控制信号源或电源，并触发示波器采集。`closure_*` 是验收样板，不是无风险演示。

### 频响、滤波和基线实验

- `through_baseline_2d_10k_500k.toml`
- `through_baseline_2d_stable.toml`
- `through_diagnostic_100mvpp.toml`
- `active_filter_raw_2d_10mv_2v_10hz_5mhz.toml`
- `passive_filter_raw_2d_10hz_1mhz.toml`
- `passive_filter_raw_2d_10hz_1mhz_500mv_1v_2v.toml`
- `passive_filter_raw_2d_10hz_1mhz_retry_test.toml`
- `passive_filter_adaptive_5mhz.toml`
- `passive_filter_2d_calibrated.toml`
- `passive_filter_dense_2d_calibrated.toml`

这组计划通常需要特定 DUT、频段、探头接法和一份先前采集的 baseline。两个 `*_calibrated.toml` 文件目前引用本地时间戳目录，不能直接复制到另一台机器；使用前应替换为当前实验的 baseline 路径。

## 写入边界

计划中的这些步骤可能改变硬件状态：

- `source.set_*`、`source.output`、`source.arb_load`
- `power.set`、`power.output`
- `scope.auto`、`scope.capture`
- `sweep.frequency_response`

`[restore] source_state = true` 只恢复文档注明的 basic source 状态，不等于完整通道快照。计划失败时要保留生成的 artifact，并重新查询仪器最终状态。

## 失败策略与安全门

每个 `[[steps]]` 默认使用 `on_failure = "stop"`。断言失败、启用 `quality_gate` 后仍有质量 warning，都会把当前 step 标记为 `failed`，并停止后续步骤。只有明确写出 `on_failure = "continue"` 时，失败 step 才会继续执行后续步骤；频响 step 内部的点级失败仍按自身的 `stop_conditions` 处理。

需要在 gate 失败后关闭已授权输出时，可在计划级显式声明安全门：

```toml
[safety]
safety_gate = true
off_source_channels = [1]
off_power_channels = [1]
```

安全门触发后会先对列出的信号源和电源通道执行 OFF，再停止 run；即使该 step 声明 `on_failure = "continue"` 也不会绕过安全门。若同时启用 source restore，恢复配置后会再次确认这些授权通道为 OFF，避免恢复操作重新打开输出。OFF 操作的结果、失败原因和授权通道会写入该 step 的 artifact 与 `run.json`。没有声明 OFF 目标时，安全门会拒绝继续并保留失败证据；它不会猜测或自动开启其他输出。

公开计划应使用保留地址、占位符和相对路径；不要把真实 IP、序列号、串口路径或 `data/` 下的实验产物写进仓库。

资源与执行环境配置可组合使用：`example_analysis_resources.toml` 设置预算，`example_analysis_execution.toml` 启用独立分析进程、超时和可选硬内存限制。两者都不是 RunPlan，不放入 `--plan`。

## 无硬件的高级指标、批量与双通道示例

`generate_pair_example.py` 只生成明确标记为 synthetic 的两路随机信号，以及单音加谐波／噪声的单通道包。输出目录必须不存在；双通道 response 为两倍增益、延迟 7 个采样点。示例不连接仪器。

```bash
python plans/generate_pair_example.py /tmp/wavebench-demo
python -m wavebench analysis run --capture /tmp/wavebench-demo/tone --channel 1 --recipe plans/example_spectral_quality.toml --output /tmp/wavebench-quality
python -m wavebench analysis batch --manifest /tmp/wavebench-demo/batch.toml --output /tmp/wavebench-batch
python -m wavebench analysis batch --manifest /tmp/wavebench-demo/batch.toml --output /tmp/wavebench-batch --resume
python -m wavebench analysis pair-run --capture /tmp/wavebench-demo --recipe plans/example_pair_analysis.toml --output /tmp/wavebench-pair --analysis-execution plans/example_analysis_execution.toml
python -m wavebench analysis report /tmp/wavebench-pair --output /tmp/wavebench-pair.html
```

Windows 可将 `/tmp/...` 替换为本地新目录。高级指标窗口按示例的 16384 Hz 采样率设计；换用历史包前需要按实际采样率和频率分辨率修改窗口及频带。缺少同步证据的真实旧包不适用于双通道示例，可用于单通道及批量流程验证。

`example_synchronized_pair.toml` 演示已有外部激励下的真实双通道冻结采集与分析，需要驱动声明 `scope.capture_synchronized`。计划本身不打开信号源；采集会修改示波器时基、垂直设置并保持 STOP。先按实际信号幅度与安全条件调整配置，再执行 `run check` 和实时预检。DG 易失性 ARB 上传与 RunPlan 基础恢复组合需要插件声明独立恢复能力；Core 使用写入前的基础快照调用恢复入口。被覆盖的 ARB 内存不在恢复范围内。
