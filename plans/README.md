# Run plan 示例

这里的 TOML 文件是实验计划，不是模拟器。文件名里有 `example`，也不代表可以在没有仪器时直接执行；很多计划会设置 source、打开输出、触发采集，或者依赖一份本地 baseline。

## 先做离线检查

`run check` 只解析计划，不连接仪器：

```bash
wavebench run template --list
wavebench run check --plan plans/example_scope_expect_quality.toml
```

`run verify` 会读取配置并查询相关仪器，适合执行前预检。`run plan` 会进行真实实验，执行前应确认接线、scope coupling、输出状态、保护限值和 `[restore]` 范围。`run report` 和 `run calibrate` 读取已有产物，不需要再次连接仪器；校准相关拟合需要安装 `.[analysis]`。

## 计划分类

### 通用示例

这些文件适合阅读和改成自己的 plan，但仍需要真实设备才能执行：

- `example_scope_expect_quality.toml`
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
