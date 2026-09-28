# 运行产物 Reference

## 独立离线分析

分析 manifest 的 `resources` 记录 `wavebench.analysis_resources.v1`、估算器 `conservative.v2`、有效限额、累计工作量，以及写入最终 metadata 前的派生数据字节／文件数。每个实际执行阶段的 `resources` 记录该阶段工作集与运算量估算。缺少这些字段的旧产物仍可读取。

NPY 来源先检查有界 header、实数 dtype、二维形状和文件长度，再只读映射；验证按块完成，跨块时间轴同样检查。处理结果使用独立工作数组，原始映射不写入。来源摘要使用同一打开文件计算，检测到文件替换或元数据变化时拒绝；这不是文件系统快照，不能保证识别所有外部并发修改。

时域／频域 NPY 与 CSV 按 4096 行块组装和原子写出，保持既有列、行序、浮点文本及文件摘要。报告按块验证全部数据与指纹，仅保存不超过 1200 个显示点；CSV 不再全量载入。预算不足或坏文件显示警告，仍可展示其它可用曲线。

默认环境继续生成 `wavebench.execution_intent.v1`，旧 Plan 的 digest 不变。显式 `--analysis-resources` 使用 `wavebench.execution_intent.v2`，把完整有效环境限额及估算器版本写入 `analysis_resources` 并绑定摘要，验证时必须提供同一资源配置。任务级收紧字段本身属于 Plan／配方内容，随既有摘要覆盖。默认 v1 不承诺绑定跨版本的默认预算。

成功重采样也写入 `transformations` 和 stage 的 `transformation`，记录约分比例、输入／输出样本数、采样率、间隔、时间范围、输出长度规则、固定滤波器 tap 数与截止频率、设计采样率、系数摘要、SciPy 版本和边界规则。manifest 的 `sampling` 随重采样更新，后续算子记录实际使用的新采样率。

成功平滑时，manifest 条件性增加 `transformations`，对应 stage 记录同一份 `transformation`。其中包括规范化参数、实际采样率、左右边界影响样本数、系数摘要、时间轴是否平移、可定义的名义群延迟；Savitzky–Golay 另外记录 SciPy 版本和窗口评价位置。未执行成功时不增加该项，后续失败保留此前的变换记录。

`peaks` 将峰列表写入处理目录的 `peaks/<name>.json` 和 `peaks/<name>.csv`。JSON 使用 `wavebench.peaks.v1`，记录数据域、单位、SciPy 版本、检测输入两列 little-endian float64 的 SHA-256、完整数量、保留数量、截断状态和峰属性。CSV 列为 `index,position,value,prominence,width,polarity`；极性以 1／-1 表示。manifest、对应 stage 及 step artifact 条件性增加峰表路径与文件摘要。后续算子失败保留已完成峰表，截断会记录警告。

`analysis report <analysis-or-run-dir> [...] --output comparison.html` 读取已保存的导出，生成独立 HTML；输出文件必须尚不存在。常规 run HTML 报告也会展示派生曲线。时域和 FFT 纵轴单位为 V，PSD 为 V²/Hz，使用线性坐标；同来源摘要、通道与数据域的曲线可叠加，不同来源分开显示。图形保留真实横轴，不自动补偿滤波延迟。

报告检查导出路径与 SHA-256，损坏或缺失导出显示警告。NPY 和 CSV 同时存在时优先读取 NPY，读取失败可回退到 CSV。显示抽稀保留局部极值，指标始终来自完整数据的既有产物；报告不重新执行算子。

`analysis run` 在新输出目录写入 `analysis.json`、`manifest.json`、`metrics.json` 和 `exports/`。`analysis.json` 使用 `wavebench.analysis.v1`，包含总体状态、WaveBench 版本、规范化配方及其 SHA-256、来源与处理结果。manifest 使用 `wavebench.offline_pipeline.v1`，复用 stage 与数值字段；派生路径以该分析目录为基准。

离线来源记录 capture package 绝对路径、通道、包内相对 NPY 路径及原始摘要。来源没有状态字段时记录 `null`，不推断为采集成功。不生成虚构 run 或采集 step，既有 RunPlan 的产物 schema 与来源路径合同保持不变。

本页说明 `run plan` 写入的运行产物入口。字段的 machine source 是 `src/wavebench/services/run_artifacts.py` 和对应的 typed result；不要从旧 Guide 推断新增或可选字段。


显式 `--analysis-execution` 使用 `wavebench.execution_intent.v3`，以 `analysis_execution` 绑定规范化监督配置；同时指定资源文件时继续包含 `analysis_resources`。无执行配置时继续使用 v1／v2。估算器从 `conservative.v1` 升为 `conservative.v2` 后，绑定旧估算器的显式资源 intent 需要重新生成。

监督模式在 manifest 与 step artifact 中记录 `execution`：配置、启动方式、实际内存后端与口径、退出码、耗时、触发原因及是否强制终止。结构化错误使用 `analysis_cancelled`、`analysis_timeout`、`analysis_worker_failed`，外层状态仍为 `failed`。Linux 有明确证据时记录 `oom_kill_count`；未知退出不推断成 OOM。报告展示监督结果、超时值、内存后端及强制终止标记。

每个阶段和完成的导出保存检查点。强制终止后，父进程以最后一个完整检查点恢复指标及导出索引，保留所有已原子提交的数据文件；`committed_files` 列出终止时实际保留的文件，包含尚未来得及更新导出索引的文件。仅清理当前分析目录内的临时文件。检查点不代表阶段成功；运行中的阶段在终态中改为失败。检查点和最终元数据的磁盘占用按当前文件计，不按反复写入的累计流量计；失败诊断沿用配额外尽力保存规则。

## 输出

成功或失败的 run 在写入运行目录后会产生以下文件：

```text
<run-dir>/
  plan.toml            原始 plan 存在时的副本
  run.json             运行级结构化记录
  summary.csv          面向快速查看和表格导入的摘要
  steps/
    00_<kind>.json     单个 step 记录
  processing/          仅在存在 analysis.pipeline 时生成
    01_<step-id>/
      manifest.json
      metrics.json
      exports/
```

`run report <run-dir>` 只读取已有产物并生成离线报告，不连接仪器，也不修改原始采集数据；它会在运行目录或显式输出位置写入派生的 HTML，使用 `--pdf` 时还会写入 PDF。

## `run.json`

以下字段始终由 writer 写入：

| 字段 | 含义 |
| --- | --- |
| `status` | 运行级状态。 |
| `experiment` | plan 中的实验名称和标签。 |
| `plan` | plan 文件路径字符串。 |
| `steps` | 每个已记录 step 的结构化记录。 |

`error`、`restore`、`provenance`、`source_operations` 和 `rf_source_operations` 仅在对应条件满足时出现。`source_operations` 与 `rf_source_operations` 只接受带有已知 schema 的类型化 operation artifact。

## step 记录

每个 `steps/<index>_<kind>.json` 记录包含 `index`、`kind`、`status`、`fields` 和 `artifact`。step 声明 ID 时还会包含 `id`；没有 ID 的旧记录不增加该字段，文件名仍保持原格式。具体 `artifact` 形状取决于 step；采集、频响、DMM、Source V2、RF Source 和离线分析不共享一张人工字段表。

## 信号处理派生产物

每个 `analysis.pipeline` step 使用独立目录：

```text
<run-dir>/processing/<index>_<step-id-or-analysis_pipeline>/
  manifest.json
  metrics.json
  exports/
    <name>.npy
    <name>.csv
```

`manifest.json` 的 schema 为 `wavebench.analysis_pipeline.v1`。它记录来源 step 及状态、来源 capture package／metadata／NPY 的 run-relative POSIX 路径、原始 NPY 的 SHA-256、规范化算子、逐阶段状态、采样信息、窗与相干增益、警告、导出、数值定义和结构化错误。某个后续算子失败时，已经完成的导出和 filter stage 元数据会保留，并由 `partial` 和 `failed_stage` 标明部分结果。

存在成功 filter stage 时，manifest 条件性增加 `filters` 数组，并在对应 stage 中记录同一份滤波元数据。FIR 项包括响应、截止频率、tap 数、实际采样率、SciPy 版本、设计窗、缩放方式、执行函数、遍数、边界规则和单程名义群延迟。`coefficients_sha256` 是实际 FIR 系数转为 little-endian float64 连续字节后的 SHA-256。零相位 FIR 另外记录固定的 `method`、`padtype` 和 `padlen`。

IIR 项记录 design、响应、截止频率、原型阶数、变换后的数字滤波器阶数、实际采样率、设计函数、SOS section 数、SciPy 版本、稳定性、最大极点模、执行函数、遍数和边界规则。`sos_sha256` 是实际 SOS 转为 little-endian float64 连续字节后的 SHA-256；Chebyshev／Elliptic 的纹波或衰减参数只在适用时出现，零相位 IIR 另外记录实际 `padtype` 和 `padlen`。manifest 不写入完整 FIR 系数或 SOS。没有成功 filter stage 的既有流水线不增加 `filters` 字段。

`metrics.json` 的 schema 为 `wavebench.analysis_metrics.v1`，结构如下：

```json
{
  "schema": "wavebench.analysis_metrics.v1",
  "metrics": {
    "peak_frequency_hz": 1000.0,
    "thd_ratio": null
  }
}
```

指标值只写有限 JSON 数字或 `null`，不写 `NaN`、`Infinity`。step artifact 的 `metrics` 保留同一份小型映射；`expect` 继续使用既有 `{ min, max }` 结果结构，因此 `summary.csv` 的 expectation 列和 HTML 验收表不需要另一套解释。

时域 NPY 和 CSV 固定为 `time_s,voltage_v` 两列。FFT 频域 NPY 和 CSV 固定为 `frequency_hz,real_v,imaginary_v,amplitude_v` 四列。PSD NPY 和 CSV 固定为 `frequency_hz,psd_v2_per_hz` 两列。每个导出记录文件路径、列名和 SHA-256；路径相对于 run 目录并使用 POSIX 分隔符。来源 NPY 保持原样，处理器只读取 capture package 内经过边界校验的文件。

成功执行 PSD 时，manifest 条件性增加 `psd` 对象，并在对应 stage 中记录同一份元数据，输出域为 `psd`。该对象包括规范化参数、执行函数、SciPy 版本、实际采样率、周期窗标记、窗功率增益、窗 SHA-256、完整分段数和丢弃尾点数。窗 SHA-256 使用实际周期窗的 little-endian float64 字节计算。`bin_spacing_hz` 为采样率除以 `nfft`；`segment_frequency_scale_hz` 为采样率除以 `nperseg`，不表示加窗后的等效噪声带宽。

PSD 元数据的 `algorithm` 区分 `welch_segment_mean.v1` 与 `scipy_welch_median.v1`；因果滤波记录 `causal_blocks.v1` 及 `block_samples=4096`。

PSD 元数据同时记录单边密度缩放、`V^2/Hz` 单位和归一化公式。仅有一段或存在尾点时写入警告；后续导出失败仍保留成功 PSD 的元数据。没有成功 PSD 的流水线不增加 `psd` 字段，schema 继续使用 `wavebench.analysis_pipeline.v1`。`measure_band` 在对应 stage 的 `measurement` 中记录选中 bin 数量、间距、积分和边界规则，以及 Welch 平均方式；标量保存为 `<name>_<metric>` 并复用现有 `metrics` 与 `expect`。没有测量算子时 `metrics` 为空映射。HTML 报告显示 Welch 分段参数、警告和导出链接。

频域 `amplitude_v` 是单边峰值幅度，不是 RMS。`noise_floor_v` 是排除 DC 与主峰后的非 DC 幅度 bin 中位数，表示每 bin 峰值幅度，不表示积分噪声。THD 使用 Nyquist 范围内的 H2～H5。

HTML 报告在存在分析 step 时增加「信号处理 / Signal processing」区域。FIR 算子显示 family、响应、截止频率、tap 数和执行模式；IIR 算子显示 family、响应、截止频率、design、order 和执行模式。报告 manifest 条件性增加 `analysis_pipelines`，没有分析 step 的旧报告 manifest 不增加该字段。

## `summary.csv`

当前 writer 的列顺序为：

```text
index, kind, status, package, metadata, quality_status, quality_warnings,
recovered, expect_status, expect_failures, expect_fft_status, expect_fft_failures
```

`summary.csv` 适合快速查看和表格导入。需要保留完整字段、条件字段或错误 evidence 的自动化工具应优先读取 `run.json` 和对应 step JSON。

## 多个 run 的离线索引

`run report-index` 读取一个或多个已有 run 目录，并在 `--output` 目录写入 `manifest.json`、`manifest.csv` 和 `index.html`。它不连接仪器；输出中的生成时间不应被当作实验时间或原始测量证据。

运行产物与 scope capture package 是不同层次的对象：run 目录记录 plan 和 step 关系，capture package 保存单次波形及其 metadata。需要分析具体采集字段时，以对应的 typed result、package loader 和 `metadata.json` 为准。

## 相关页面

- [执行一次实验](../how-to/run-an-experiment.md)
- [从模板到报告](../tutorials/from-template-to-report.md)
- [run plan 排错](../how-to/troubleshooting.md)
- [run plan Reference](run-schema.md)

## 高级谱质量、批次与双通道产物

`spectral_quality` 的 stage measurement 使用 `wavebench.spectral_quality.v1`，记录完整参数、PSD 条件、定义、频率间隔、各区间的 bin 范围／数量／带宽／V²，以及每阶谐波覆盖状态和最大杂散位置。JSON 标量只保存有限数或 `null`，缺失值沿用 unavailable 验收。

批次目录中的 `batch.json` 使用 `wavebench.analysis_batch.v1`，保存 binding、binding_sha256、逐项状态、指标、错误及 attempts 文件摘要；`summary.csv` 列为 `id,status,metrics_json,directory,error_code`。结果位于 `items/<id>/attempt_<number>/`，复用独立分析文件。`.batch.lock` 是进程互斥文件。已完成结果恢复前验证内容，未完成 attempt 保留在原位置并计入后续总预算。

双通道 manifest 使用 `wavebench.analysis_pair.v1`，外层仍复用 `analysis_pipeline` artifact 键，以共用 run 汇总、expectation 和 R3 监督。独立 `analysis.json` 标记 `analysis_kind="pair"`。来源记录 reference／response 两路 SHA-256、metadata SHA-256、同步证据及时间轴检查；stage measurement 分别使用 `wavebench.pair_delay.v1` 和 `wavebench.pair_transfer.v1`。

双通道频域 NPY／CSV 的列固定为 `frequency_hz,real_v,imaginary_v,gain_db,phase_rad,coherence,valid,coherent`。real_v／imaginary_v 为 H1 的实部／虚部，实际单位是 V/V；gain_db 为 20 log10 幅值，phase_rad 为弧度。频率始终有效；无效响应的五个数值列在 NPY 中为 NaN，CSV 中为空，valid／coherent 为 0／1。这个独立合同不改变单通道导出禁止非有限值的规则。JSON 指标继续使用 null。

报告将双通道增益、相位、相干性分图展示，保留无效区断点，并显示有效／高相干 bin 数、延迟指标及同步证据类型。当前曲线渲染读取 NPY；仅导出 CSV 时显示数据链接，不伪造图形。显示抽稀仍不参与测量或验收。

## 驱动生成的同步证据

显式同步 capture 在 metadata 中写入 `synchronization`，使用 `wavebench.capture_sync.v1` 的 `driver_frozen_single` 类型。driver 字段记录驱动 ID、型号与固件；procedure 记录插件流程版本、一次采集设置、完成／冻结确认、逐通道配置检查和诊断配置。acquisition group 是主机事务标识，不是硬件采集序号；未提供硬件序号时为 null。任一通道读取或证据校验失败，部分波形可保留，但失败包不得带 verified 同步证明。

原始 NPY 与 metadata 不因离线分析被改写。RunPlan capture 的 package 路径沿用工作目录相对路径约定；pair 入口在执行前解析绝对位置，包内文件仍受路径越界检查。未校准的链路时延和相干结果不构成 DUT 精密延迟校准。

## 源恢复声明与结果

声明恢复能力的插件在 `provenance.source_restore_coverage` 记录是否请求恢复、版本化声明及未覆盖项；任意波上传 step 还记录 `artifact.restore_coverage`。没有声明的上传记录 supported 为 null，不能推断为完整可恢复。未请求恢复不产生伪造的成功结果。

采用独立恢复入口时，`restore.results` 按通道记录声明、status、实际回读或结构化 error。状态包括 not_started、restoring、verified、failed；只有覆盖字段验证完成才标 verified。安全门覆盖输出目标时另记 `output_override="safety_gate_off"`，原快照保持。HTML 展示范围、未覆盖项和逐通道结果；整体恢复状态仅表示请求的基础范围。旧插件的记录形状保持不变。
