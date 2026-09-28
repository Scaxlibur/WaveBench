# run plan Reference

信号处理的操作步骤与示例见[使用信号处理流水线](../how-to/signal-processing.md)。

## 独立离线配方

`analysis` 命令直接处理历史 capture package，不需要仪器配置。显式选择一个通道，配方包含 `schema = "wavebench.analysis_recipe.v1"`、`operations` 和可选 `[expect]`／`[resources]`，共用下文的算子与验收合同。示例为 `plans/example_analysis_recipe.toml`。

## 高级谱质量估计

`spectral_quality` 只接受 mean Welch PSD，沿用命名指标与 `[expect]`。完整配方见 `plans/example_spectral_quality.toml`；每个字段均显式声明。

| 字段 | 合同 |
| --- | --- |
| `name`、`metrics` | 安全名称；显式选择下文指标，不重复 |
| `band_hz` | 总测量闭区间，必须在 Nyquist 内 |
| `dc_exclude_hz`、`exclude_hz` | DC 排除区从 0 开始；其它排除区最多 32 个，位于总频带内 |
| `fundamental` | 仅 `{frequency_hz=...}` 或 `{search_hz=[low,high]}`；搜索模式在带内合格峰中选择最大密度峰，不插值 |
| `fundamental_half_width_hz`、`harmonic_half_width_hz` | 正的积分半宽，不小于实际 bin 间隔；不自动推断主瓣 |
| `harmonic_orders` | 显式选择 2～16 阶，最多 15 项且不重复；可为空 |
| `min_fundamental_v2` | 正的基波积分功率门 |
| `min_peak_density_v2_per_hz`、`min_prominence_v2_per_hz` | 正的基波峰密度及显著性门，固定频率也必须通过 |
| `min_noise_bins` | 至少 1 个有效噪声 bin，最大 1000000 |
| `spur_search_hz` | 总频带内的杂散搜索区，排除 DC、无效区及基波区，包含谐波 |
| `spur_half_width_hz`、`spur_distance_hz` | 正的杂散积分半宽和候选最小间距 |
| `spur_min_density_v2_per_hz`、`spur_min_prominence_v2_per_hz` | 正的杂散候选密度／显著性门 |

按闭区间 bin 中心分配区域，`P(S)=sum(PSD[S])*df`。基波、谐波和剩余噪声区分别为 F、H、N；区域冲突或基波窗口被截断时失败。超出测量带界的谐波记录未覆盖；其结果只说明声明的带内估计。固定基频使用最近 bin 检查峰门，积分区域仍围绕声明频率。

- `snr_db = 10*log10(P(F)/P(N))`。
- `sinad_db = 10*log10(P(F)/(P(H)+P(N)))`。
- `thdn_ratio = sqrt((P(H)+P(N))/P(F))`，不覆盖旧 `thd_ratio`。
- `sfdr_db = 10*log10(P(F)/P(spur))`，spur 为最大积分杂散区；相应 `spur_dbc` 为反号。

可选指标还包括 `fundamental_frequency_hz`、`fundamental_power_v2`、`harmonic_power_v2`、`noise_power_v2`、`noise_bandwidth_hz`、`spur_frequency_hz`、`spur_power_v2`。输出键为 `<name>_<metric>`。有效信号不足、零分母或有效噪声 bin 太少时对应结果为 `null`，不加 epsilon；杂散窗口裁断、重叠或间距不足时 SFDR 不可用，不静默合并谱簇。谱峰端点沿用 SciPy `find_peaks` 的排除规则。

这些是带宽受限的 PSD 积分估计：基波区内噪声不扣除，剩余噪声区可能含非谐波杂散，不承诺等价于仪器标准自动 SNR。报告展示实际区域、积分功率及最大杂散位置。旧 FFT 幅值、每 bin 噪声底和 H2～H5 保持原算法。

## 串行批量分析

`analysis batch --manifest batch.toml --output <new-directory>` 读取 `wavebench.analysis_batch.v1` 清单。字段为 `recipe`、`entries`、`on_failure="stop|continue"`、`duplicates="reject|allow"`、`max_output_bytes`；路径相对于清单目录解析。每个 entry 必须有唯一安全 `id`、`capture` 和正整数 `channel`。同包同通道重复仅在 `allow` 时接受；条目最多 256 个，并受环境文件数上限约束。

批次默认启用独立分析进程监督，每条分析的默认超时为 300 秒；可用 `--analysis-execution` 显式替换。一次只执行一个条目，取消停止整个批次，普通失败遵循清单的 stop／continue。`max_output_bytes` 不得超过环境总输出限额，历史 attempt、当前结果和索引都计入总额；索引预留空间可能使小配额提前耗尽。失败诊断可尽力超额保存，但批次状态为失败。

`--resume` 要求原批次目录，重新核对清单、配方、有效资源／执行配置、数值库版本、来源 metadata／NPY 摘要和已完成产物摘要。变化或损坏时拒绝复用，要求新的输出目录；未成功的条目写入新的 attempt 目录，保留旧文件。文件锁防止两个进程同时写同一批次。中断期间尚未完成的 attempt 不冒充已验证成功结果。

批次 JSON／CSV 逐项列出指标、状态、错误和目录，不自动对不同合同或频带的指标求平均。`analysis report <batch-directory> --output report.html` 可以复用已保存的曲线，仍受报告资源预算约束。

## 双通道分析

独立入口为 `analysis pair-check/pair-run --capture <package> --recipe <recipe>`，`pair-run` 另需新的 `--output`。配方 schema 为 `wavebench.analysis_pair_recipe.v1`，包含 `reference_channel`、`response_channel`、`operations` 和可选 `expect`／`resources`。资源与执行配置选项同单通道接口。示例见 `plans/example_pair_analysis.toml`。

RunPlan 使用独立的 `analysis.pair`，同样要求 `source={step="earlier_capture"}` 指向更早且显式保存 NPY 的 `scope.capture`。它与 `analysis.pipeline` 共同组成离线后缀，不支持 safety_gate，在硬件恢复、会话关闭与租约释放后执行。真实同步采集通过独立 `scope.capture_synchronized` capability 提供，缺少该 capability 时在触发前拒绝。`scope.capture` 显式设置 `channels=[1,2]`、`synchronized=true`、`save_npy=true` 和 `points="DEF"`；不接受单通道 `channel`、quality／expect／自动重试配置。CLI 等价入口为 `scope capture --channel 1 --channel 2 --points def --synchronized`。适配的具体型号、固件和采集模式由插件声明与验证；不会自动升级旧包。

同包两路必须不同且指向不同 NPY。metadata 的 `synchronization` 必须使用 `wavebench.capture_sync.v1`，接受 `kind="synthetic"` 或 `kind="driver_frozen_single"`，状态均须为 `verified`。还要求 `producer.name`／`producer.version`、来源类型与 kind 一致的 `acquisition_group.id`、`timebase_id`、`record_id`、`single_record`／`frozen_read` 保证，以及每路 time_start_s、sample_interval_s、samples、skew_s、uncertainty_s。示例生成器给出 synthetic 结构。driver_frozen_single 还需 `driver.id`／`driver.model`／`driver.firmware`、producer 与 procedure 版本匹配、single_count=1、完成等待与前后冻结证明、每路配置未变检查。Core 校验通用结构及 metadata 身份，厂商专属完成语义由插件负责。未知模拟 skew／不确定度可为 null，不能据此推断为零。普通 metadata 和 SHA-256 用于一致性追溯，并非防伪签名，也不能排除前面板或不合作控制器的并发干预。

两路点数一致、各自等间隔，并逐块检查时间轴绝对差不超过 `dt*1e-6`；不使用绝对时间戳的相对容差放宽偏移。不自动裁剪、补零、重采样或 deskew；skew 信息仅记录，不应用补偿。旧包缺证据仍可单通道分析，不能通过同目录、同时间轴或主机 ID 推断同步。

| 算子 | 显式字段与约束 |
| --- | --- |
| `delay` | `name`、`max_lag_s>=0`、布尔 `remove_mean`、`polarity="same|either"`、`min_overlap_ratio`、`min_correlation` 在 (0,1]、`ambiguity_delta` 在 [0,1)、`metrics`；至多一次且在 transfer 前 |
| `transfer` | `name`、三种周期 `window`、`nperseg`、`noverlap`、`nfft`、`detrend="none|constant|linear"`、正的 `min_reference_density`／`min_response_density`、`min_coherence` 在 [0,1]、布尔 `unwrap_phase`、`metrics`；至多一次 |
| `export` | 安全 `name`、不重复的 `formats=["npy","csv"]`；必须在 transfer 后，导出名不重复 |

时延为整数采样：`response_delay_s>0` 表示 response 较晚，去均值对整条记录显式应用，归一化能量只使用当前 lag 的实际重叠。超出记录的搜索范围按实际可重叠样本限制；能量为零、相关门不通过或最高两个 lag 分数差不超过歧义门时不可用。允许反相时按绝对相关值排序并单独报告极性。指标为 `response_delay_samples`、`response_delay_s`、`correlation`、`polarity`、`overlap_samples`，均加测量名称前缀。它描述测量链路总延迟，不是已校准 DUT 延迟。

transfer 至少需要两个完整 Welch 段，固定 mean，并共用两路分段谱：`Sxy=mean(conj(X)*Y)`、`H1=Sxy/Sxx`、`coherence=abs(Sxy)^2/(Sxx*Syy)`。弱参考／响应密度处掩码无效；相干性在 1 附近不超过 `1e-9` 的舍入误差可裁剪，明显越界失败。低相干估计保留并以 coherent 掩码区分；相位展开只在连续有效区进行。指标为 `mean_coherence`、`min_coherence`、`valid_bin_count`、`coherent_bin_count`，显式选择并加名称前缀。

## 分析进程监督

`analysis check/run` 与 `run check/intent/verify/plan` 接受 `--analysis-execution <toml>`，文件使用 `wavebench.analysis_execution.v1`。示例见 `plans/example_analysis_execution.toml`。

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `timeout_s` | 300 秒 | 单条分析的墙钟超时，包含启动与读取 |
| `grace_s` | 2 秒 | 请求取消后等待协作退出的时间 |
| `memory_bytes` | 不设置 | 可选的平台硬内存限额 |
| `cgroup_root` | 不设置 | Linux 硬限额所需的已委派 cgroup v2 目录 |

时间必须为有限正数，硬限额必须为 1～`2^63-1` 的整数；未知字段拒绝。执行配置属于环境，不加入 RunPlan step 或分析配方。未指定文件时保持同进程执行，资源预算仍然有效。`check` 检查配置和平台能力，实际超时监督只作用于 `run`／`plan` 的分析阶段。

指定文件后，每条分析链在独立 `spawn` 子进程执行。RunPlan 在硬件恢复、会话关闭和租约释放后启动分析，不传递仪器句柄。Ctrl+C 或 Service 的取消事件先请求协作退出，超过宽限期后 terminate，仍未退出时 kill；父进程确认退出后整理产物。普通失败和超时按 `on_failure` 决定后续分析，用户取消停止整个分析后缀。数值块、读写块与算子边界可协作取消；单次不可中断的原生调用依靠进程终止处理。

Windows 硬限额使用 Job Object 的 job committed memory；Linux 使用 cgroup v2 的 `memory.max` 并设置 `memory.swap.max=0`，要求 memory controller 和 `cgroup.kill` 可用。两者统计口径不同，不称为等价的 RSS 配额。硬限额请求在预检时创建临时作用域并验证测试进程绑定，失败就拒绝；不自动提权或降级。父进程和绑定前的启动阶段不受该分析硬限额保护，分析进程也不是不可信代码沙箱。

## 分析资源预算

分析使用有限的默认预算；超限时拒绝执行，不自动降低 taps、FFT 长度或采样率。旧的极大配方可能因此失败，正常预算内的数值参数与结果保持原样。

资源文件独立于仪器配置，以 `schema = "wavebench.analysis_resources.v1"` 开头，随后是限额字段。`--analysis-resources <file.toml>` 可用于 `analysis check/run/report` 和 `run check/intent/verify/plan/report`。未提供的字段沿用默认值。独立离线分析仍不需要 `wavebench.toml`。

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `max_working_bytes` | 536870912 | 估算工作集，512 MiB |
| `max_input_samples` | 20000000 | 来源或重采样后的样本数 |
| `max_fft_length` | 1048576 | FFT／PSD FFT 长度 |
| `max_fir_taps` | 4095 | FIR taps |
| `max_zero_phase_fir_taps` | 255 | 零相位 FIR taps，同时受上一项约束 |
| `max_work_units` | 2000000000 | 累计运算量估算，不代表秒数 |
| `max_output_bytes` | 1073741824 | 每条分析输出总字节；报告用于累计导出读取及报告输出 |
| `max_temp_bytes` | 1073741824 | 当前临时输出文件字节 |
| `max_peak_candidates` | 100000 | 峰候选最坏数量／报告标记累计数量 |
| `max_report_curves` | 32 | 单份信号处理报告曲线数 |
| `max_operations` | 128 | 每条处理链算子数量 |
| `max_output_files` | 256 | 分析文件数；独立报告来源目录数 |
| `max_metadata_bytes` | 8388608 | 单个配方／元数据文档字节 |

所有限额是 1～`2^63-1` 的整数，不接受布尔值或无限值。环境资源文件可以明确提高预算；配方 `[resources]` 和分析 step 的 `[steps.resources]` 只能收紧有效环境值，提高时拒绝配置。资源文件示例见 `plans/example_analysis_resources.toml`。

`run check` 在硬件会话之前检查操作数量、FIR taps、PSD `nfft` 和文件数量。实际采集长度此时未知，因此不能把静态通过视为全部资源检查通过。独立 `analysis check` 读取受限 NPY header、校验实际波形，并按重采样后的长度推演处理链。实际执行仍逐阶段检查，以保留此前成功导出和准确失败位置。

工作集估算计入数组副本、滤波器、PSD 重叠分段及复数工作区；运算量按 FIR 的样本数乘 taps、Welch 的段数乘 FFT 长度及对数阶等保守模型累计。峰检测在创建候选属性前按最坏峰数准入，平坦的大波形也可能被拒绝。mean Welch 按原段顺序逐段累计，预算单段工作区；median 继续预算完整分段矩阵。因果 FIR／IIR 按 4096 点分块并传递滤波状态，零初态和滤波器设计不变；零相位仍整段执行。分段累计与整段算法按数值容差兼容，不承诺浮点产物字节一致。

预算范围是单个分析 step／独立分析目录；多个 RunPlan 分析 step 串行执行，各自计账，尚无整个 run 的磁盘总配额。已有重采样比例、样本数等算子固有限制仍有效，资源文件不能解除它们。

超限错误为 `resource_limit_exceeded`，包含维度、限额、请求量和阶段。已开始的分析按 `on_failure` 处理，不重采集；当前临时文件清理，已完成文件保留。诊断 metadata 在配额耗尽后仍尽力写入并标记失败，这部分可能超过输出配额；磁盘完全耗尽时不保证诊断落成。

资源预算是保守准入估算，不是操作系统 RSS 硬限制或不可信代码沙箱。不同 SciPy／NumPy 版本的内部工作集可能不同；未知规模应先做受控基准。常规 `run report` 的资源选项只约束信号处理曲线区域，不覆盖旧截图、PDF 或频响报告的全部资源。

```bash
wavebench analysis check --capture data/capture --channel 1 --recipe plans/example_analysis_recipe.toml
wavebench analysis run --capture data/capture --channel 1 --recipe plans/example_analysis_recipe.toml --output data/analysis_trial_1
```

输出必须是新的独立目录，不能位于来源 capture package 或既有 run 内。再次分析应使用另一个输出目录。来源读取或算子失败写入分析产物；配置和输出目录不合法时在执行前拒绝。验收失败时命令返回非零状态。

本页说明如何查询 WaveBench 当前支持的 run plan 结构。完整的 step、必填字段、可选字段和简要行为由离线命令生成，不在 Guide 中复制维护。

## Synopsis

```bash
python -m wavebench run schema
python -m wavebench run template --list
python -m wavebench run template <name> --print
```

三个命令都不连接仪器。`run schema` 输出顶层 TOML 表以及按 step kind 排序的字段清单；`run template --list` 列出当前模板；`--print` 将一个模板的 TOML 输出到标准输出。

当前安装版本的完整离线输出也以版本控制形式保存在[生成的 run plan schema](generated/run-schema.md)。该页由源码生成；本页只说明查询方式和使用边界。

## 使用顺序

1. 先运行 `run schema`，确认安装版本接受的 `kind` 和字段。
2. 再用 `run template --list` 选择接近实验目标的保守模板。
3. 使用 `run check` 检查实际 plan；它不能替代连接预检或实验台安全确认。

## 事实来源与边界

当前 schema 的 canonical source 是 `src/wavebench/services/run_plan.py` 中的 step schema 以及 `python -m wavebench run schema` 的输出。模板名称与默认内容来自 template registry。页面中的计划片段只能说明一个任务，不能作为完整字段表或型号 capability 的来源。

实际执行步骤、连接预检和副作用见[执行一次实验](../how-to/run-an-experiment.md)。字段错误和 schema 变更的排查见[run plan 排错](../how-to/troubleshooting.md)。

## 稳定 step ID 与离线分析

每个 `[[steps]]` 都可以声明结构字段 `id`。ID 必须匹配 `^[a-z][a-z0-9_-]{0,63}$`，并在同一个 plan 内唯一；没有 ID 的既有 plan 无需迁移。`id` 不属于 step 的执行参数，因此不会出现在 `RunStep.fields` 中。

`analysis.pipeline` 使用稳定 ID 引用同一 plan 内更早的 `scope.capture`：

```toml
[[steps]]
id = "capture_main"
kind = "scope.capture"
channel = 1
save_npy = true
on_failure = "continue"

[[steps]]
id = "spectrum_main"
kind = "analysis.pipeline"
source = { step = "capture_main" }
operations = [
  { op = "measure", metrics = ["voltage_mean_v", "voltage_rms_v", "voltage_vpp_v"] },
  { op = "remove_dc" },
  { op = "filter", family = "fir", response = "bandstop", cutoff_hz = [49.0, 51.0], numtaps = 101, mode = "zero_phase" },
  { op = "filter", family = "iir", design = "butterworth", response = "highpass", cutoff_hz = 20.0, order = 4, mode = "causal" },
  { op = "window", name = "hann" },
  { op = "fft" },
  { op = "measure", metrics = ["peak_frequency_hz", "peak_amplitude_v", "noise_floor_v", "thd_ratio"] },
  { op = "export", name = "spectrum", formats = ["npy", "csv"] },
]

[steps.expect]
peak_frequency_hz = { min = 990, max = 1010 }
thd_ratio = { max = 0.05 }
```

来源 capture 必须显式设置 `save_npy = true`。首版不接受历史 capture package 路径，也不接受其他 step 类型或后续 step 作为来源。所有 `analysis.pipeline` 必须形成 plan 的连续末尾部分；硬件步骤、恢复、会话关闭和租约释放完成后，才会执行离线分析。分析 step 支持 `on_failure`，不支持 step 局部 `safety_gate`，也不会触发硬件安全门。

## 算子合同

`operations` 是有序的 TOML 内联表数组。当前允许以下算子：

| 算子 | 参数 | 输入／输出域 |
| --- | --- | --- |
| `remove_dc` | 无 | 时域 → 时域 |
| `detrend` | `method = "linear"` | 时域 → 时域 |
| `filter` | FIR 或 IIR 的判别式设计参数 | 时域 → 时域 |
| `smooth` | 方法、奇数窗口长度、模式和边界，见下文 | 时域 → 时域 |
| `resample` | 比例、Kaiser 窗参数和边界，见下文 | 时域 → 新采样率时域 |
| `window` | `name = "hann|hamming|blackman"` | 时域 → 时域 |
| `fft` | 无 | 时域 → 频域 |
| `psd` | Welch 分段参数，见下文 | 时域 → PSD |
| `measure` | 非空 `metrics` 数组 | 观察当前域，不改变数据 |
| `measure_band` | `name`、`band_hz`、`exclude_hz`、`metrics` | 观察 PSD，不改变数据 |
| `peaks` | 命名检测、筛选条件和数量上限，见下文 | 观察当前域，不改变数据 |
| `export` | 安全的 `name`；`formats` 为 `npy`、`csv` 的非空子集 | 导出当前域，不改变数据 |

`remove_dc`、`detrend`、`window` 和 `fft` 各至多出现一次；`remove_dc` 与 `detrend` 互斥。`filter`、`smooth` 和 `resample` 可以重复，按声明顺序执行。去直流、去趋势、滤波、平滑和重采样必须位于整段窗口之前，所有时域变换必须位于 FFT／PSD 之前。测量指标和导出名称在同一流水线内不得重复，流水线至少包含一个 `measure`、`measure_band`、`peaks` 或 `export`。

### 时域平滑

```toml
{ op = "smooth", method = "moving_average", window_length = 5, mode = "causal", boundary = "edge" }
{ op = "smooth", method = "savgol", window_length = 11, polyorder = 2, mode = "centered", boundary = "reflect" }
```

`method`、`window_length`、`mode` 和 `boundary` 必填。窗口为 3～1001 的奇数，输入必须等间隔且长度不小于窗口。`savgol` 另需 `polyorder`，为 0～5 且小于窗口长度的整数；移动平均不接受该字段。平滑必须在整段 window、FFT 和 PSD 之前，可串联多个 stage。

`centered` 使用左右等长窗口，边界可选 `reflect`（不重复端点的反射）或 `edge`（首末值延拓）。`causal` 仅使用当前及过去样本，起始处只允许 `edge`，不允许引入未来样本的反射。输出样本数和时间轴不变，不自动补偿延迟。

移动平均各点等权；因果模式名义群延迟为 `(window_length - 1) / 2` 个样本。Savitzky–Golay 使用零阶导数系数，居中模式在窗口中点评价，因果模式在末点评价；因果模式不声明固定群延迟。系数非有限或常量增益校验失败时明确失败，不静默修正。移动平均仅使用 NumPy，Savitzky–Golay 按需检查 SciPy 的 `savgol_coeffs`。

### 有理数比例重采样

```toml
{ op = "resample", up = 2, down = 3, window = "kaiser", beta = 5, padtype = "line" }
```

全部参数必填。`up`／`down` 是正整数，约分后各不超过 10000；输入整数上限为 `2^63 - 1`，输出不超过 20000000 个样本。`window` 固定为 `kaiser`，`beta` 为 0～30 的有限数；边界选 `constant`（零延拓）或 `line`（按首末点连线延拓）。只接受等间隔时域输入，必须位于整段 window、FFT 和 PSD 之前。

实现使用 `resample_poly` 和显式设计的对称 FIR。设约分后的 `rate = max(up, down)`，滤波器为 `20 × rate + 1` taps、归一化截止频率 `1 / rate` 的 Kaiser 窗设计，设计采样率为原采样率乘 `up`。比例为 1 时直接保留数据，不滤波。该滤波器提供抗混叠，实际通带与阻带性能随参数变化，不等同于理想砖墙滤波。

输出长度为 `ceil(N × up / down)`，时间轴为 `t0 + arange(N_out) × dt × down / up`。保留时间原点，可能产生位于原末样本之后、但属于输出采样网格的末点；边界值由延拓合同决定。不通过拉伸时间轴强行匹配原末点。无法表示有限且严格递增的新时间轴时失败。后续滤波、峰值、FFT 和 PSD 使用新采样率，原始 NPY 保持不变。

### FIR 滤波

FIR 算子同时支持四种响应：

- `lowpass`／`highpass` 使用单个有限正数 `cutoff_hz`。
- `bandpass`／`bandstop` 使用两个有限正数组成的严格递增数组 `cutoff_hz`。
- `numtaps` 必须是大于等于 3 的奇数。
- `mode` 必须显式设置为 `causal` 或 `zero_phase`。

实际采样率由来源 NPY 的时间轴计算。时间轴必须等间隔，所有截止频率必须严格低于 Nyquist 频率；这两个条件依赖采集结果，因此在离线分析 step 执行时校验并形成结构化产物。

`causal` 使用 Hamming 设计窗的 `scipy.signal.firwin` 和零初始状态的单向 `lfilter`，保留起始暂态及名义群延迟。`zero_phase` 固定使用 `filtfilt` 的奇延拓、`method = "pad"` 和 `padlen = 3 * numtaps`，因此至少需要 `3 * numtaps + 1` 个采样点。零相位模式的有效幅频响应为单向 FIR 幅频响应的平方；两种模式都保持样本数和时间轴，不自动裁剪或补偿时间。

FIR 需要可选分析依赖：

```bash
python -m pip install -e ".[analysis]"
```

Plan 包含 FIR、IIR 或 PSD 算子时，`run check` 检查 SciPy；缺少依赖时会在租约、session 和仪器 I/O 之前失败。仅使用 NumPy 算子的流水线不需要 SciPy。

### IIR 滤波

IIR 继续使用同一个 `filter` 算子。`family = "iir"` 时必须声明 `design` 和 `order`：

```toml
{ op = "filter", family = "iir", design = "butterworth", response = "lowpass", cutoff_hz = 5000.0, order = 4, mode = "causal" }
{ op = "filter", family = "iir", design = "chebyshev1", response = "highpass", cutoff_hz = 100.0, order = 4, ripple_db = 1.0, mode = "zero_phase" }
{ op = "filter", family = "iir", design = "chebyshev2", response = "bandpass", cutoff_hz = [100.0, 5000.0], order = 6, attenuation_db = 40.0, mode = "causal" }
{ op = "filter", family = "iir", design = "elliptic", response = "bandstop", cutoff_hz = [49.0, 51.0], order = 6, ripple_db = 1.0, attenuation_db = 60.0, mode = "zero_phase" }
```

参数按 `design` 严格区分：

- `butterworth` 不接受 `ripple_db` 或 `attenuation_db`。
- `chebyshev1` 必须且只接受 `ripple_db`。
- `chebyshev2` 必须且只接受 `attenuation_db`。
- `elliptic` 必须同时接受 `ripple_db` 和 `attenuation_db`，且纹波必须小于衰减。
- `order` 必须是 1～12 的整数；`ripple_db` 位于 `(0, 20]`，`attenuation_db` 位于 `(0, 200]`。

四种设计都支持低通、高通、带通和带阻，并固定使用 SciPy 的 SOS 输出。设计后会校验二阶节形状、有限系数、单位化分母和所有极点严格位于单位圆内。`order` 对低通／高通表示数字滤波器阶数，对带通／带阻表示原型阶数；带型变换后的数字滤波器阶数为 `2 * order`。

单程临界频率的含义随设计而异：Butterworth 是 `-3 dB` 点；Chebyshev I 和 Elliptic 是通带纹波边缘；Chebyshev II 是阻带衰减边缘。`cutoff_hz` 始终表示单程 SciPy 设计参数，零相位输出不会重新把它解释为最终 `-3 dB` 点。

`causal` 使用 `sosfilt` 和全零初始状态。`zero_phase` 使用 `sosfiltfilt` 与固定奇延拓；padding 长度根据实际 SOS 明确计算并写入 manifest，输入点数必须大于该长度。零相位的有效幅频响应仍是单程幅频响应的平方。两种模式都保留原时间轴和样本数。

FIR、IIR 和 PSD 共用 `.[analysis]` 可选依赖。`run check` 根据 Plan 实际选择的算子参数检查所需 SciPy 函数。

### Welch 功率谱密度

PSD 算子将时域数据转换为单边功率谱密度，单位为 `V²/Hz`。全部参数必须显式声明：

```toml
{ op = "psd", method = "welch", window = "hann", nperseg = 256, noverlap = 128, nfft = 256, detrend = "none", average = "mean" }
{ op = "export", name = "density", formats = ["npy", "csv"] }
```

| 参数 | 合同 |
| --- | --- |
| `method` | 固定为 `welch` |
| `window` | `hann`、`hamming` 或 `blackman`，每段使用周期窗 |
| `nperseg` | 每段样本数，整数且至少为 4 |
| `noverlap` | 相邻段重叠样本数，整数且满足 `0 <= noverlap < nperseg` |
| `nfft` | 每段 FFT 长度，整数且不小于 `nperseg`；较大值只做补零 |
| `detrend` | `none`、`constant` 或 `linear`，在每段加窗前执行 |
| `average` | `mean` 或经过偏差修正的 `median` |

PSD 可以跟在去直流、去趋势或 FIR／IIR 之后，但不能跟在整段 `window` 或 `fft` 之后。每条流水线至多有一个 PSD；PSD 之后允许 `export`、`measure_band` 和 `peaks`，至少执行其中一个。需要同时生成 FFT 和 PSD 时，使用两个分析 step 引用同一个 capture。PSD 的频带测量不复用 FFT 的峰值幅度、THD 或噪声底。

### 通用峰值检测

```toml
{ op = "peaks", name = "tones", polarity = "positive", height = 0.01, prominence = 0.01, distance = 10, width = 0, max_peaks = 20, metrics = ["count"] }
```

所有字段必填。`height`、`prominence`、`width` 非负，零值表示不设对应下限；`distance` 必须为正；`max_peaks` 为 1～10000 的整数。时域支持 `positive`、`negative` 和 `both`，FFT／PSD 只允许 `positive`。极性表示局部极大／极小方向，不保证电压绝对正负；例如负直流偏置上的局部极大值，在 `height = 0` 时也会保留。负峰在电压取反后检测，结果仍保存原始电压。高度和显著性单位随域为 V 或 V²/Hz；距离和宽度单位在时域为秒、频域为 Hz。时域要求等间隔采样。

先按高度、显著性、半显著性宽度筛选，再以带极性的高度降序、位置升序确定间隔竞争和输出顺序，最后截断至上限。负峰的高度按取反后的值排序，正负峰共同竞争间隔。端点不视为峰，平台峰选择中间样本，偶数长度时取靠前样本；不对峰位置做亚 bin 插值。峰宽使用半显著性高度的插值交点。

`metrics = ["count"]` 显式生成 `<name>_count`，表示截断前、筛选后峰数量，可用于 `expect`。峰列表写入独立 JSON／CSV；空列表是合法结果。检测不改变信号数据域，可继续变换、测量或导出。报告只在峰表的信号摘要与绘制曲线一致时标记峰。

### PSD 频带验收

```toml
{ op = "measure_band", name = "audio", band_hz = [20, 20000], exclude_hz = [[990, 1010]], metrics = ["mean_square_v2", "rms_v", "noise_rms_v"] }
```

所有字段必填；没有排除频带时显式填写 `exclude_hz = []`。频带边界必须非负且严格递增，排除区间必须位于测量频带内；运行时测量频带不得超过实际 Nyquist。按闭区间选择 bin 中心，并按闭区间排除；结果为剩余 bin 密度之和乘 bin 间距，DC 与偶数点 Nyquist 均使用完整 bin 权重。空结果写入 `null` 和警告。

`mean_square_v2` 单位为 V²，`rms_v` 为其平方根，单位为 V；没有负载信息时不转换为瓦特。`noise_rms_v` 使用同一积分，但要求显式提供非空信号排除区间，其噪声含义依赖声明的频带选择。所有指标均应用 `exclude_hz`；需要未排除的带内 RMS 时另建一个名称不同的测量。

上例产生 `audio_mean_square_v2`、`audio_rms_v` 和 `audio_noise_rms_v`，可在 `[steps.expect]` 或离线配方 `[expect]` 中使用 min/max。命名测量不可重名。积分沿用所选 Welch 均值或中位数估计，不额外重标定。

运行时按实际时间轴检查等间隔采样，容差为 `rtol=1e-6, atol=0`。样本数小于 `nperseg` 时失败，不自动缩短段长。只处理完整段；不足一段的尾点不补齐，并在 manifest 中记录数量。仅有一段时仍可导出，同时记录没有跨段平均的警告。

数值实现使用 [SciPy Welch](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html)，固定 `scaling="density"` 和单边输出。每段按 `sample_rate_hz × sum(window²)` 归一化，仅将非 DC、非偶数点 Nyquist 的 bin 功率乘 2。`detrend="none"` 不隐式去直流。频率 bin 间距为 `sample_rate_hz / nfft`，补零不会改善由段长与窗决定的分辨能力。

时域指标为 `voltage_min_v`、`voltage_max_v`、`voltage_mean_v`、`voltage_rms_v` 和 `voltage_vpp_v`。频域指标为 `peak_frequency_hz`、`peak_amplitude_v`、`noise_floor_v`、`thd_ratio`，以及 `harmonic_2`～`harmonic_5` 的 `frequency_hz` 和 `amplitude_v` 字段。`[steps.expect]` 只能引用流水线中已显式选择的测量指标。

完整示例见 `plans/example_signal_processing_pipeline.toml`。数值定义和派生产物结构见[运行产物 Reference](artifacts.md)。旧 `scope.capture` 的 `expect_fft` 保持原有算法，不由新流水线重定义。

## 基础源恢复范围

`[restore] source_state = true` 要求基础状态恢复，不能解释为完整仪器备份。插件可声明覆盖字段、未覆盖字段和支持恢复的操作；不支持所请求恢复时，`run check` 在连接仪器前拒绝。执行阶段还会先读取并验证全部恢复快照，初态不能恢复时不执行实验步骤。

`source.arb_load` 与恢复组合要求插件明确支持，且上传通道必须包含在 source_channels（或默认恢复通道）中。易失任意波内容可以明确排除；基础参数恢复成功不表示旧任意波内容已恢复。没有恢复要求时可以执行上传，但报告仍展示覆盖范围。查看声明可用 `plugin info <driver-id> --load`。

恢复先关闭输出、恢复并验证参数，最后按目标处理输出；安全门要求 OFF 的通道不会因快照原来为 ON 而重新启用。恢复失败仍使 run 失败，并阻止离线分析后缀。驱动开发合同见[插件开发](../development/plugin-development.md)，结果字段见[产物 Reference](artifacts.md)。
