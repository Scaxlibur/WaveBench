# WaveBench 文档总览

[English](README_EN.md) | 中文

WaveBench 是面向电赛调试场景的轻量 Python 自动测量台。当前阶段支持 R&S RTM2032、RIGOL DS1104Z/DS1000Z 示波器采集、RIGOL DG4202 信号源控制、RIGOL DP800 系列电源控制、RIGOL DM3000/DM3058 系列万用表读取，并坚持显式命令与可验证的小步实验流程。上述五个仪器族是长期保留的主包预装基线，首次使用无需安装外置插件，也没有从主包移除的计划；外置插件是显式选择、独立发布的可选升级或扩展。

> [!IMPORTANT]
> 当前正式版本是 `v0.8.0`。本版本首次发布 V2 可执行插件、受管插件生命周期、覆盖槽位与 SocketIO 后端；这些能力不属于 `v0.7.0`。其他版本的用户应查看对应 tag 内的文档。

## 当前阶段

```text
阶段 1：LAN 仪器显式控制 + 可复现实验采集
```

目标：

```text
可靠地远程读取示波器波形、显式控制信号源和电源，并保存 CSV / NPY / metadata / commands.log。
```

当前已经支持单次/多通道采集、失败采集包、数据质量摘要、采集窗口控制、DG4202 离散扫频与占空比控制、DP800 电源显式控制、DMM 读数，以及多仪器 run plan 执行。双通道 `sweep.frequency_response` 可测量幅频、相频和传统一维拟合，也可按请求 Vpp 形成二维扫频；一个 run 可记录多个独立 response。它支持引用操作者手动直通采集的独立基线 run，在软件中做完整复传递函数、仅相位或仅延迟校正（不改示波器 deskew）；可选自适应频率加密仍保持每个 Vpp 的矩形网格。二维校准会自动生成浮点 LUT、补偿限制审计、分段 Chebyshev 公式，以及默认 signed `Q4.12` 的 audit CSV/Xilinx COE/MEM；`run calibrate --response <label>` 可完全离线重算。安装 `report3d` extra 后，二维频响 HTML 还会离线显示可旋转的实测增益曲面，支持 Raw/Corrected 与 dB/V/V 切换；PDF 保持单文件静态归档。`wavebench doctor` 可只读检查配置中的仪器资源、IDN 和型号匹配，也可用 `--discover-subnet` 在配置资源失效时按 IDN 匹配候选替代 resource，便于现场排查网络和配置问题。`run template` 可生成保守的 run plan 模板，并支持少量频率、频点列表、幅度、通道、电压参数，减少手写 TOML 的低级错误。run plan 的 `scope.capture` 可选择质量检查，并在质量警告时按 `[quality].auto_recover_attempts` 触发多次显式 autoscale 重采；若多次采集指标稳定，可标记为 `ok_by_consistency`。`[steps.expect]` 可对采集指标设置 min/max 断言，`[steps.expect_fft]` 可直接对 FFT 主频、主峰幅度、THD、谐波幅度做断言。断言失败会把实验标记为 failed。可选的实验性 TUI 已覆盖 DP800 电源、DM3000/DM3058 万用表和 DG4202 信号源的常用查看/控制操作，并冻结在这三个面板；CLI、run plan 和 Service 仍是核心能力。HTTP MCP 只读 MVP 已提供 `/health`、`/mcp`、`/tools`、`/call`，其中 `/mcp` 支持 MCP JSON-RPC 的 `initialize`、`tools/list`、`tools/call`，工具为 `run.schema`、`run.check`、`capture.inspect` 三个只读工具。v0.2 已开始加入离线包读取和静态 `run report`；报告现在会输出 `验收摘要 / Acceptance summary`、`预期 vs 实测 / Expected vs measured`，并汇总频率、Vpp、均值、duty、截图与 FFT 验收信息；对多点 sweep run 还会生成扫频摘要表，便于快速查看各频点质量、主峰与 THD。这些报告命令只读已有文件，不连接仪器。

## 长时间测试

对会因静默而断开的终端，使用无额外依赖的心跳运行器：

```bash
python scripts/pytest_progress.py -- -q
python scripts/pytest_progress.py --heartbeat-s 10 -- -q tests/test_run_service.py
```

它原样转发 pytest 输出，并每隔指定秒数打印 keepalive。它只能避免“无输出”超时，不能绕过终端或编排平台本身设置的总运行时限。


## 文档索引

### 项目设计

- [WaveBench_项目边界.md](./project/WaveBench_项目边界.md)
  - 定义项目定位、阶段划分、当前不做什么。
- [WaveBench_CLI形态.md](./project/WaveBench_CLI形态.md)
  - 定义交互式 / 非交互式 CLI、LAN-only 约束、第一阶段命令。
- [WaveBench_设备抽象层.md](./project/WaveBench_设备抽象层.md)
  - 定义 transport、device、service、data/export 分层，以及后续接入信号发生器的边界。
- [WaveBench_插件注册表.md](./project/WaveBench_插件注册表.md)
  - 定义 V1 metadata 与 V2 可执行 driver registry、延迟加载、冲突检查和安全边界。
- [WaveBench_可安装仪器插件.md](./project/WaveBench_可安装仪器插件.md)
  - 说明本地包检查、当前 venv 受管安装、状态核对、升级/降级、卸载、事务回滚与恢复。
- [WaveBench_插件市场索引.md](./project/WaveBench_插件市场索引.md)
  - 定义只读本地插件市场 JSON index、`plugin market search/info` 和非安装安全边界。
- [WaveBench_声明式SCPI插件.md](./project/WaveBench_声明式SCPI插件.md)
  - 定义本地声明式 SCPI 插件 TOML、`plugin scpi check/info/probe/doctor` 和只读 IDN 探测安全边界。
- [WaveBench_插件开发指南.md](./project/WaveBench_插件开发指南.md)
  - 面向 V2 descriptor、DriverContext、factory、contracts、OptionSpec、entry point 和 wheel 测试的作者指南。
- [WaveBench_新增仪器驱动指南.md](./project/WaveBench_新增仪器驱动指南.md)
  - 面向真实新增仪器的驱动、service、config、CLI、doctor、run plan、template、测试和文档清单。
- [WaveBench_数据输出格式.md](./project/WaveBench_数据输出格式.md)
  - 定义采集包目录、CSV / NPY / JSON / commands.log 输出格式。
- [WaveBench_配置文件格式.md](./project/WaveBench_配置文件格式.md)
  - 定义 TOML 本机配置、查找顺序、参数优先级和 example 配置。
- [WaveBench_错误处理和日志策略.md](./project/WaveBench_错误处理和日志策略.md)
  - 定义错误分类、退出码、终端日志、commands.log 和 failed 采集包策略。
- [RTM2032_MVP命令确认表.md](./instruments/RTM2032_MVP命令确认表.md)
  - 整理 MVP-1 要使用的 SCPI 命令、用途、注意事项和推荐命令序列。
- [WaveBench_示波器采集模块设计草案.md](./project/WaveBench_示波器采集模块设计草案.md)
  - 早期开发思路草案，包含资料检索、模块设想、风险分析。

### 设备资料

- [WaveBench_DP800电源只读接入设计.md](./project/WaveBench_DP800电源只读接入设计.md)
  - DP800 系列电源第一阶段只读接入设计：`power idn/status`，不写输出。
- [WaveBench_DP800电源显式设置设计.md](./project/WaveBench_DP800电源显式设置设计.md)
  - DP800 电压/电流限值显式设置设计：`power set`，不控制 output。
- [WaveBench_DP800电源输出控制设计.md](./project/WaveBench_DP800电源输出控制设计.md)
  - DP800 输出开关显式控制设计：`power output on|off`，不设置电压/电流。
- [WaveBench_DM3000万用表接入设计.md](./project/WaveBench_DM3000万用表接入设计.md)
  - DM3000/DM3058 万用表接入设计：LAN/VISA 与 RS232 transport 和 driver 分开；支持 `dmm idn/read`，并可通过显式 run-plan step 参与实验。
- [WaveBench_多仪器协同流程设计.md](./project/WaveBench_多仪器协同流程设计.md)
  - 多仪器实验流程层设计：显式组合 power/source/scope/dmm 动作，先离线 `run check`，再只读 `run verify`，最后执行 `run plan`。
- [WaveBench_TUI终端控制面板.md](./project/WaveBench_TUI终端控制面板.md)
  - 实验性 Textual TUI 控制面板：当前覆盖 DP800 电源、DM3000/DM3058 万用表和 DG4202 信号源的常用操作。
- [WaveBench_HTTP_MCP_只读接口.md](./project/WaveBench_HTTP_MCP_只读接口.md)
  - HTTP MCP 只读接口：启动命令、端点、工具列表和安全边界。
- [WaveBench_run_plan_使用指南.md](./project/WaveBench_run_plan_使用指南.md)
  - 面向 plan 作者的实用说明：`run schema`、`run check` 常见报错、`run.json` / `summary.csv` 输出字段。

### 历史路线图与验证归档

以下文档记录当时的阶段计划、release 收口和实机验证证据；遇到与 README / 使用指南不同的命令语义时，以当前 README 和使用指南为准。

- [WaveBench_v0.1_收口清单.md](./project/WaveBench_v0.1_收口清单.md)
  - 第一个 release 前的能力边界、暂缓事项、门禁和发布步骤。
- [WaveBench_v0.2_路线图.md](./project/WaveBench_v0.2_路线图.md)
  - v0.2 的解耦优先路线：包读取 API、离线报告、capture inspect 与截图保存边界。
- [WaveBench_v0.2_收口清单.md](./project/WaveBench_v0.2_收口清单.md)
  - v0.2 release 前的能力边界、门禁、实机 smoke 和 release notes 入口。
- [WaveBench_v0.3_路线图.md](./project/WaveBench_v0.3_路线图.md)
  - v0.3 的报告可视化路线：summary card、expected vs measured、波形预览、report manifest 和离线 FFT inspect。
- [WaveBench_v0.3_收口清单.md](./project/WaveBench_v0.3_收口清单.md)
  - v0.3 release 前的完成情况、门禁、暂缓事项和 release notes 入口。
- [WaveBench_v0.4_路线图.md](./project/WaveBench_v0.4_路线图.md)
  - v0.4 的 DG4202 任意波形输出 MVP 路线：先确认 SCPI，再做离线 builder、driver upload 和示波器闭环。
- [WaveBench_v0.4_收口清单.md](./project/WaveBench_v0.4_收口清单.md)
  - v0.4 release 前的能力边界、门禁、实机闭环证据和 release notes 入口。
- [WaveBench_v0.4_闭环验证记录.md](./project/WaveBench_v0.4_闭环验证记录.md)
  - DG4202 CH1 到 RTM2032 CH1 的基础波形与任意波闭环验证记录；FFT 验收优先于当前时域频率估计。
- [DG4202_任意波形命令确认表.md](./instruments/DG4202_任意波形命令确认表.md)
  - v0.4 前置命令确认表；`DATA:DAC VOLATILE` 上传与 little-endian DAC block 已经实机确认。

- [RTM2000_VISA_SCPI_手册摘录.md](./instruments/RTM2000_VISA_SCPI_手册摘录.md)
  - RTM2000 用户手册中 VISA / SCPI 相关摘录，作为命令确认依据。

- [DG4000_ProgrammingGuide_CN.md](./instruments/DG4000_ProgrammingGuide_CN.md)
  - RIGOL DG4000 系列编程手册 Markdown 版，作为 DG4202 任意波形 SCPI 的主要依据。
- [普源DP800系列电源_编程手册.md](./instruments/普源DP800系列电源_编程手册.md)
  - RIGOL DP800 系列编程手册 Markdown 版，作为电源 SCPI 的主要依据。
- [普源DM3000万用表_编程手册.md](./instruments/普源DM3000万用表_编程手册.md)
  - RIGOL DM3000 系列编程手册 Markdown 版，作为万用表 SCPI / RS232 / LAN 接入依据。

## 进度速览

### 已完成

- [x] 初始化 Python 项目骨架与本机 TOML 配置。
- [x] 通过 LAN VISA 接通 RTM2032：`TCPIP::<rtm2032-ip>::INSTR`。
- [x] 实机打通 `scope idn`、`scope errors`、`scope autoscale`。
- [x] 实机打通 `scope fetch --channel 1`。
- [x] 实机打通 `scope capture --channel 1 --label <label>`。
- [x] 实机打通 `scope capture --channel 2 --label <label>`。
- [x] 支持 `scope capture --channel 1 --channel 2` 单次 acquisition 多通道读取。
- [x] `scope capture` 默认执行一次 `SINGle + *OPC?` 后读取一个或多个通道。
- [x] 采集包输出：CSV、NPY、`metadata.json`、`commands.log`。
- [x] 失败采集包：`metadata.partial.json`、`error.txt`、`commands.log`。
- [x] 数据质量摘要：Vpp、均值、RMS、频率估计、周期数估计、每周期点数、质量提示。
- [x] 输出控制：`--no-csv`、`--no-npy`。
- [x] 点数范围控制：`--points def|max|dmax`。
- [x] 采集时窗控制：`--time-range <seconds>`。
- [x] 自动窗口：`--window-frequency <Hz> --target-cycles <N>`。
- [x] 预期频率校验：`--expect-frequency <Hz> --frequency-tolerance <ratio>`。

### 当前实机状态

2026-04-29 已在 RTM2032 上完成采集闭环：

```text
Windows Ethernet -> RTM2032 <rtm2032-ip> -> VISA/SCPI
*IDN? -> AUToscale -> fetch/capture -> acquisition package
```

已验证数据样例：

```text
1 kHz square: 10000 samples, dt=200 ns, voltage≈-0.5..0.64 V
1 kHz sine  : 10000000 samples with DMAX, Vpp≈5.12 V, freq≈999.997 Hz
5 kHz sine  : Vpp≈5.12 V, freq≈5001.36 Hz
sweep smoke : 1k→10k linear sweep, auto 10 ms window, no low-cycle warning
formal source->scope loop: source set-freq 1 kHz, scope verified ≈1 kHz
real square-wave metrics: 100 kHz square, duty≈0.5, rise/fall≈32 ns
point-sweep flow validated on 1 kHz / 2 kHz / 5 kHz / 10 kHz
```

已确认下一阶段信号源目标设备：

```text
RIGOL DG4202 @ <dg4202-ip>
PyVISA + NI-VISA verified
resource = TCPIP::<dg4202-ip>::INSTR
fixed-mode write/readback on DG4202 channel output verified
source set-frequency settle delay configurable via settle_ms_after_set_frequency
square duty-cycle command verified with :SOUR2:FUNC:SQU:DCYC
```

已确认电源目标设备：

```text
RIGOL DP832A @ <dp800-ip>
resource = TCPIP::<dp800-ip>::INSTR
power idn/status read-only smoke verified
power protection status reads OVP/OCP settings separately from set/output
CH1 output/set/status smoke verified
power set smoke: 5.0V -> 3.3V -> 5.0V verified with RTM2032 CH2
power output smoke: ON -> OFF -> ON verified with RTM2032 CH2
```

已验证多仪器 run plan：

```text
plan = plans/dp800_scope_probe_voltage_steps.toml
run  = data/runs/20260430_150454_dp800_scope_probe_voltage_capture
steps = 6, status = ok
sequence = scope guard -> 5 V capture -> 3.3 V set/capture -> 5 V restore/capture
scope CH2 mean ≈ 4.8826 V -> 3.1976 V -> 4.8842 V

plan = plans/dg4202_duty_10k_power_ch2_check.toml
run  = data/runs/20260430_154307_dg4202_duty_10k_power_ch2_check
steps = 18, status = ok, restore = ok
source CH2 duty request/readback = 25% / 50% / 75%
scope CH1 measured duty = 0.25 / 0.50 / 0.75 at 10 kHz
source snapshot restored = ON / SIN / 5000 Hz / 5 Vpp / duty 50%
scope CH2 power mean ≈ 4.8919 V -> 4.8876 V
```

### 推荐实机命令

启动实验性 TUI：

```bash
python -m pip install -e ".[tui]"
python -m wavebench tui
python -m wavebench tui --config wavebench.toml
python -m wavebench tui --log-file data/tui/wavebench-tui.log
python -m wavebench tui --fake
```

`wavebench tui` 默认读取当前目录的 `wavebench.toml`。如果在其他目录启动，请用 `--config <toml>` 指定配置文件。TUI 默认日志路径是 `data/tui/wavebench-tui.log`，也可以用 `--log-file <path>` 改到其他位置。

信号发生器最小探测：

```bash
python -m wavebench source idn --config wavebench.toml
python -m wavebench source status --config wavebench.toml --channel 2
```

电源探测与显式控制：

```bash
python -m wavebench power idn --config wavebench.toml --resource TCPIP::<dp800-ip>::INSTR
python -m wavebench power status --config wavebench.toml --resource TCPIP::<dp800-ip>::INSTR --channel 1
python -m wavebench power protection status --config wavebench.toml --resource TCPIP::<dp800-ip>::INSTR --channel 1
python -m wavebench power set --config wavebench.toml --resource TCPIP::<dp800-ip>::INSTR --channel 1 --voltage 5.0 --current-limit 0.1
python -m wavebench power output --config wavebench.toml --resource TCPIP::<dp800-ip>::INSTR --channel 1 on
```

`power set` 不改变 output 状态；`power output` 不改变电压/电流限值；`power protection` 与两者分离。

多仪器流程检查、执行与离线报告：

```bash
python -m wavebench run schema
python -m wavebench run check --config wavebench.toml --plan plans/dp800_scope_probe_voltage_steps.toml
python -m wavebench run check --config wavebench.toml --plan plans/example_scope_expect_quality.toml
python -m wavebench run check --config wavebench.toml --plan plans/demo_dg4202_10k_screenshot_report.toml
python -m wavebench run plan --config wavebench.toml --plan plans/dp800_scope_probe_voltage_steps.toml
python -m wavebench run plan --config wavebench.toml --plan plans/demo_dg4202_10k_screenshot_report.toml
python -m wavebench run report data/runs/<run_dir>
```

离线查看采集包与截图采集：

```bash
python -m wavebench capture inspect data/raw/<capture_dir>
python -m wavebench scope capture --config wavebench.toml --channel 1 --label smoke_with_screen --points def --no-csv --screenshot
```

`run report` 和 `capture inspect` 只读已有 `run.json` / `summary.csv` / `metadata.json` / `ch*.npy`，不会连接仪器，也不会改写原始采集数据。`scope capture --screenshot` 会在采集包中额外写入 `screenshot.png`。

`plans/example_scope_expect_quality.toml` 是公开示例计划，包含 source restore、`quality_gate`、`auto_recover` 和 `[steps.expect]`，只用于展示写法。`plans/closure_sine_1k.toml` 与 `plans/closure_triangle_1k.toml` 是更稳定的公开闭环样板：保留 source restore、高阻保护、截图保存和 `[steps.expect_fft]`。`plans/demo_dg4202_10k_screenshot_report.toml` 是 v0.2 实机 demo：DG4202 CH2 输出 10 kHz 方波，RTM2032 CH1 采集波形和截图，断言频率和可见信号幅度，随后用 `run report` 生成 HTML。

`run plan` 会真实连接仪器并执行计划；DP800 直连示波器探头的计划必须保留显式 scope coupling guard。

信号源最小可写控制：

```bash
python -m wavebench source set-freq --config wavebench.toml --channel 2 1000
python -m wavebench source output --config wavebench.toml --channel 2 on
python -m wavebench source set-duty --config wavebench.toml --channel 2 25
```


快速单次采集（不写 CSV）：

```bash
python -m wavebench scope capture --config wavebench.toml --channel 1 `
  --label smoke `
  --points def `
  --no-csv
```

按最低频率自动设置窗口，例如扫频最低 1 kHz、目标 10 个周期：

```bash
python -m wavebench scope capture --config wavebench.toml --channel 1 `
  --label sweep_smoke `
  --points def `
  --window-frequency 1000 `
  --target-cycles 10 `
  --no-csv
```

固定频率校验，例如期望 500 Hz、容差 5%：

```bash
python -m wavebench scope capture --config wavebench.toml --channel 1 `
  --label sine_500hz_check `
  --points def `
  --window-frequency 500 `
  --target-cycles 10 `
  --expect-frequency 500 `
  --frequency-tolerance 0.05 `
  --no-csv
```



单次 acquisition 多通道采集，例如 CH1 + CH2：

```bash
python -m wavebench scope capture --config wavebench.toml   --channel 1 --channel 2   --label dual_smoke   --points def   --window-frequency 1000   --target-cycles 10   --no-csv
```

多通道模式会先配置所有目标通道，只执行一次 `SINGle` 和一次 `*OPC?`，再顺序读取各通道，并在同一个采集包中写入 `ch1.npy`、`ch2.npy` 和统一的 `metadata.json`。metadata 的 `trigger_mode` 为 `single_acquisition`。读取中途失败时，已完成通道的 CSV/NPY 会保留在 `*_failed` 包中，`metadata.partial.json` 会记录完成通道、失败通道和阶段。通道 header 和 summary 仍各自独立，不假设不同通道信号相同。

### 下一步

- [x] 明确多通道策略：`--channel` 可重复，一次触发后逐通道读取；每个通道独立质量摘要，不假设信号相同。
- [x] 将 DG4202 整理进正式 WaveBench 架构最小骨架：`source idn/status/set-freq/output`。
- [x] 为离散扫点流程增加 source-mode 防呆：在固定频点实验前明确检查 `FREQ:MODE`，必要时从 `SWE` 切到 `FIX`。
- [x] 正式最小闭环已验证：`source set-freq` -> `scope capture`。
- [x] 正式离散扫点命令：`sweep discrete`。
- [x] 接入 DP800 电源命令：`power idn/status/set/output/protection`。
- [x] 设计实验流程层，必须显式组合 power/source/scope 动作，不允许 capture/sweep 默认修改电源。
- [x] 实现 `run check`、可配置 scope coupling guard、最小 `run plan` 执行器。
- [x] DP800 电压阶跃 run plan 实机 smoke 通过：5 V -> 3.3 V -> 5 V，并写入 `data/runs/...`。
- [x] 把 source step 接入 `run plan`，并新增 `source.set_duty`。
- [x] 三仪器流程 smoke 通过：DG4202 CH2 动态 duty -> RTM2032 CH1 分析；DP800 CH1 -> RTM2032 CH2 对照。
- [x] `run plan` source restore 通过：`[restore] source_state = true` 会在成功/失败路径恢复 output/function/frequency/amplitude/duty。
- [x] 修复 run plan 中 `target_cycles/window_frequency_hz` 未换算为 `time_range_s` 的问题；100 kHz duty 75% 复验为 0.75。
- [x] run plan `scope.capture` 支持 `quality_gate = true` 和 `auto_recover = true`；质量警告时按配置执行多次 `scope.auto` 后重采，并支持重复结果一致性判定。
- [x] run plan `scope.capture` 支持 `[steps.expect]` 指标 min/max 断言；断言失败会保留采集证据，并将 step/run 标记为 failed。
- [x] 新增公开 example plan：`plans/example_scope_expect_quality.toml`，展示质量恢复与实验断言写法。
- [x] 新增 `run schema`，从代码打印 run plan step schema；`run check` 对未知 kind、未知字段、缺字段给出更具体提示。
- [x] 截图和静态报告已接入；YAML 实验流程仍暂缓。

## 当前关键约束

- 当前主线优先使用 LAN/VISA；部分设备可保留 serial transport skeleton，具体以配置文档为准。
- VISA 资源格式暂定：`TCPIP::<instrument-ip>::INSTR`。
- 本机配置使用 `wavebench.toml`，示例配置使用 `wavebench.example.toml`。
- `wavebench.toml` 不进 git。
- 第一阶段默认不 `*RST`。
- `auto / autoscale` 是显式命令，会改变水平、垂直和触发设置。
- `fetch` / `capture` 默认不偷偷调用 autoscale。
- `fetch` 与 `capture` 需要分开：
  - `fetch`：读取当前波形；
  - `capture`：触发单次采集后读取波形。
- 交互式和非交互式入口必须共用底层逻辑。
- CLI / shell 不直接写 SCPI；SCPI 集中在设备驱动层。
- Service 层表达实验动作，不表达设备命令。
- 每次采集生成独立采集包。
- 第一阶段输出：每通道 CSV、每通道 NPY、metadata.json、commands.log；可用 `--no-csv` / `--no-npy` 关闭大文件输出。
- 连接成功后失败保留 `*_failed` 采集包。
- `--points` 只接受 RTM2032 支持的 `def|max|dmax`，不接受任意数字点数。
- `--time-range` 会设置 RTM2032 的 `TIMebase:RANGe`，改变水平时窗。
- `--window-frequency` 只用于自动计算窗口；`--expect-frequency` 才用于频率一致性校验。
- `WaveBench_sweep状态恢复设计.md`：离散扫点前后的信号源状态保存与恢复设计。
- `power set` 和 `power output` 是两个独立命令：前者不改变 output，后者不改变电压/电流限值。
- DP800 读回等待通过 `power.settle_ms_after_set` 与 `power.settle_ms_after_output` 配置，不应隐藏为不可见常量。
- 使用示波器测电源输出时，不要自动切换输入阻抗；50 Ω 输入耐压较低，必须由操作者明确确认。
- `[steps.expect]` 只检查采集摘要指标，不扫描原始波形；需要更复杂的判定时应先新增明确的 summary 指标。


### 2026-04-30 `scope.auto` run-plan 步骤

- [x] `run plan` 支持显式 `scope.auto` 步骤。
- [x] 该步骤通过 `ScopeService.autoscale()` 调用 RTM2032 `AUToscale`，并复用既有 `*OPC?` 路径等待完成。
- [x] `scope.capture` 仍不会隐式调用 autoscale；需要 autoscale 时，plan 必须显式插入 `scope.auto`。
