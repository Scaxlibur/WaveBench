# 使用信号处理流水线

信号处理流水线按显式配方处理采集波形，支持时域变换、频谱测量和结果导出。已有采集包可直接离线分析；RunPlan 可在采集后声明独立的分析步骤。原始数据保持只读，派生数据写入独立目录。

## 选择处理方式

| 目标 | 入口 | 输入 |
| --- | --- | --- |
| 重新处理历史波形 | `analysis check`、`analysis run` | 采集包、通道和处理配方 |
| 采集后自动分析 | RunPlan 的 `analysis.pipeline` | 更早的 `scope.capture` step ID |
| 对多个来源应用同一配方 | `analysis batch` | 显式批次清单 |
| 测量两路时延、频响和相干性 | `analysis pair-check`、`analysis pair-run` 或 `analysis.pair` | 有同步证据的双通道采集包 |
| 查看或比较处理结果 | `analysis report` | 已保存的分析、批次或 run 目录 |

`analysis` 命令不连接仪器，也不需要 `wavebench.toml`。RunPlan 中的采集步骤会操作示波器，执行前须遵循[执行一次实验](run-an-experiment.md)的接线、预检和恢复要求。

## 准备输入与配方

在已安装 WaveBench 的环境中使用本页命令。基础去直流、去趋势、窗和 FFT 使用 NumPy；滤波、Welch PSD、峰值、平滑等扩展功能按需检查 SciPy。安装方法见[安装](../getting-started/installation.md)，源码环境可安装 `.[analysis]`。

来源必须是包含 metadata 和 NPY 波形的采集包。单通道 NPY 为两列 `[time_s, voltage_v]`，数值有限、时间严格递增；FFT、滤波等要求等间隔采样。单个 NPY 文件不能直接作为 `--capture` 输入。

配方由有序的 `operations` 组成，按需要组合以下处理：

| 用途 | 算子与测量 |
| --- | --- |
| 时域预处理 | 去直流、线性去趋势、移动平均、Savitzky–Golay 平滑、有理数比例重采样 |
| 滤波 | FIR／IIR 的低通、高通、带通和带阻；显式选择因果或零相位模式 |
| 频谱 | Hann／Hamming／Blackman 窗、单边 FFT、Welch PSD |
| 测量 | 时域统计、主峰／谐波／THD、PSD 频带积分、SNR／SINAD／SFDR、多峰检测 |
| 导出 | 命名 NPY／CSV 文件、峰表、标量指标与处理记录 |

新 FFT 不隐式去直流或加窗；PSD 自行声明分段窗与去趋势，不能接在整段 window 或 FFT 后。FFT 幅度单位为 V，PSD 密度为 V²/Hz；高级谱质量指标还需明确频带、基波／谐波区域和有效性门限。参数、顺序和数据域约束见[RunPlan 与配方 Reference](../reference/run-schema.md)。

## 分析已有采集包

以下命令从仓库根目录执行，将 `data/raw/capture_example` 替换为已有采集包目录：

```bash
wavebench analysis check --capture data/raw/capture_example --channel 1 --recipe plans/example_analysis_recipe.toml
wavebench analysis run --capture data/raw/capture_example --channel 1 --recipe plans/example_analysis_recipe.toml --output data/analysis_fft
wavebench analysis report data/analysis_fft --output data/analysis_fft.html
```

这个配方依次去直流、加 Hann 窗、计算 FFT，测量主峰频率与幅度并导出频谱。`check` 验证来源、配方和预算；实际结果以 `run` 的状态及产物为准。

输出目录和独立报告文件必须尚不存在，分析目录不能位于原始采集包或既有 run 内。尝试另一种窗或滤波参数时，修改配方并使用新的输出目录，保留两次结果供比较。

输出目录包含 `analysis.json`、`manifest.json`、`metrics.json` 和 `exports/`。检查总体状态、选中的指标、warning 和失败阶段；配置了 `[expect]` 时，还应检查验收是否通过。不可用指标为 `null`，相应 expectation 按失败处理。

报告读取已保存的导出，不重新计算指标。同来源、通道和数据域的曲线可以叠加；频谱与 PSD 使用各自的单位，图形抽稀不改变指标。缺少曲线时，先确认配方包含 `export`，再检查文件是否完整。详细字段见[运行产物 Reference](../reference/artifacts.md)。

## 在 RunPlan 中声明处理链

下面的片段展示一个采集步骤与其分析后缀，可加入实验计划：

```toml
[[steps]]
id = "capture_main"
kind = "scope.capture"
channel = 1
save_npy = true

[[steps]]
id = "spectrum_main"
kind = "analysis.pipeline"
source = { step = "capture_main" }
operations = [
  { op = "remove_dc" },
  { op = "window", name = "hann" },
  { op = "fft" },
  { op = "measure", metrics = ["peak_frequency_hz", "peak_amplitude_v"] },
  { op = "export", name = "spectrum", formats = ["npy", "csv"] },
]
```

来源必须是同一 Plan 中更早且显式保存 NPY 的 `scope.capture`。所有分析步骤组成连续末尾部分，在硬件恢复、会话关闭与租约释放后执行。分析失败只改变分析步骤状态，并按 `on_failure` 决定是否继续其它分析；不会触发重新采集。

仓库中的 `plans/example_signal_processing_pipeline.toml` 演示一次采集后的三条独立处理链：滤波 FFT、平滑／重采样／多峰和 PSD 频带验收。先完成离线检查：

```bash
wavebench run check --plan plans/example_signal_processing_pipeline.toml
```

完整示例假定已有约 1 Vpp、1 kHz 的输入，采样率至少 20 kSa/s、至少 4096 点。按实际输入调整参数及阈值，再完成硬件预检和执行。它不会配置或开启信号源；派生文件写入 run 的 `processing/`，可通过 `run report` 查看。

## 批量与双通道分析

没有采集包时，可以用仓库生成器验证离线流程。以下目录均须尚不存在；生成的数据明确标记为 synthetic：

```bash
python plans/generate_pair_example.py data/processing_demo
wavebench analysis batch --manifest data/processing_demo/batch.toml --output data/processing_batch
wavebench analysis batch --manifest data/processing_demo/batch.toml --output data/processing_batch --resume
wavebench analysis pair-check --capture data/processing_demo --recipe plans/example_pair_analysis.toml
wavebench analysis pair-run --capture data/processing_demo --recipe plans/example_pair_analysis.toml --output data/processing_pair
wavebench analysis report data/processing_pair --output data/processing_pair.html
```

批次对显式清单中的来源串行应用同一配方。`--resume` 核对输入、配方、环境配置和已完成文件的摘要；变化或损坏时拒绝原地恢复，失败条目重跑到新 attempt 目录。批次 JSON／CSV 保留逐项结果，不自动平均不同合同的指标。

双通道示例的 response 为 reference 的两倍，并延迟 7 个采样点。检查 `timing_response_delay_samples` 为 7、`system_mean_coherence` 通过配方阈值，以及报告中的增益、相位和相干性曲线。

真实双通道分析要求同次采集证据、共同时间基准和一致的样本轴；普通多通道包不能仅凭相同目录或时间轴推断同步。`plans/example_synchronized_pair.toml` 使用驱动的 `scope.capture_synchronized` capability 采集，再执行 `analysis.pair`。该示例需要事先准备外部激励，会修改示波器设置并保持 STOP；支持型号、固件和采集模式由插件声明。

时延结果描述测量连接中的总延迟，未自动校准探头或通道偏移。弱激励频段标为无效，低相干保留质量标记；不能把无效点当作零响应，也不会自动补偿时延或 deskew。

## 资源控制与常见失败

可在 `analysis check/run` 中附加 `--analysis-resources plans/example_analysis_resources.toml`，选择环境预算；配方中的 `[resources]` 只能进一步收紧。FIR tap 数、FFT 长度、工作集、运算量和输出均受约束，超预算时不会自动改变数值参数。

需要取消或超时控制时，附加 `--analysis-execution plans/example_analysis_execution.toml` 启用独立分析进程。批次默认启用进程监督。配置可以申请平台硬内存限制，但 Linux 需要已委派的 cgroup v2 目录，Windows 使用 Job Object；平台预检不满足要求时拒绝启用，不自动降级。预算估算本身不保证进程不会耗尽内存。

| 现象 | 处理方式 |
| --- | --- |
| 缺少 SciPy | 安装 analysis 可选依赖，重新执行离线检查 |
| 算子顺序或参数不合法 | 按当前数据域检查配方，查阅算子 Reference |
| 输入轴不均匀或缺少同步证据 | 核对采集方式；旧包可用于单通道分析，不手工补造同步证明 |
| 指标为 `null` 或 expectation 失败 | 查看有效性原因、频带和门限；保留失败记录 |
| 资源超限或执行超时 | 根据记录的限制和估算调整环境预算或实验参数，再写入新目录 |
| 只保留部分导出 | 检查 manifest 的失败阶段或取消记录，已完成文件保留用于诊断 |

资源与执行字段、失败语义见[RunPlan Reference](../reference/run-schema.md)，文件布局和摘要见[产物 Reference](../reference/artifacts.md)，其它错误见[排错指南](troubleshooting.md)。
