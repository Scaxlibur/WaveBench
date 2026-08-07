# WaveBench 更新日志

这里记录已经打过 Git tag 的版本。内容以 tag 之前已经合入的代码、测试、发布说明和边界记录为事实源；路线图里的候选功能不算已发布能力。没有独立 tag 的 release notes 草稿只并入对应正式版本，不单列为版本。版本号和时间以仓库 tag 为准，当前开发分支的变化不放在历史版本里。

## v0.8.0 — 2026-07-24

依据：tag `v0.8.0`（提交 `4799b12`）以及从 `v0.7.0` 到该 tag 的提交记录。

### 主要变化

- 将仪器接入拆成「长期预装基线 + 可选外置发行版」两层：主包继续预装 RTM2000/RTM2032、DS1104Z/DS1000Z、DG4000/DG4202、DP800 和 DM3000/DM3058 五个仪器族。
- 建立可执行 Instrument API V2，统一 descriptor、capability、权限提示、配置字段、resource scheme 和 registry/factory 契约。
- 增加本地 wheel/source package 检查，以及 `plugin install/installed/upgrade/downgrade/remove/recover` 受管生命周期；事务包含账本、文件摘要、漂移检测、回滚和保守恢复。
- DG4000、DM3000、DP800 和 RTM2000 支持受限 canonical 覆盖；外置插件只能声明 canonical ID，不能覆盖短 alias；DS1000Z 使用独立外置 canonical ID。
- 增加 RIGOL DS1104Z/DS1000Z 四通道示波器路径，包括 BYTE 波形换算、RAW 分块读取、截图、autoscale、错误队列和一次 acquisition 后的统一多通道读取。
- 强化长波形读取的二进制长度校验、telemetry、调用级长传输超时和不可安全重放错误语义。
- 增加经校验的 DM3000/DM3058 RS-232 transport 配置；外置 DM3000 发行版严格限制为 TCPIP/LAN。
- 增加 RsInstrument SocketIO 路径，并保留 RsVisa、VXI-11/pyvisa-py 等显式兼容选择；收紧 backend 别名和 resource scheme 边界。
- 增加 sweep analyzer 公共数据契约，为 query-only 扫频分析仪保留硬件无关的接入面。
- run plan 增加保守模板、参数化模板和 source/scope 离散扫频模板；执行期间复用仪器 session，减少重复连接和状态漂移。
- `wavebench doctor` 增加 resource、IDN、型号匹配和显式网段内的只读发现建议；报告增加多点 sweep 摘要和更完整的质量/一致性信息。
- TUI 继续作为 `wavebench[tui]` optional extra，范围冻结在电源、万用表和信号源三个控制面板，并修复退出时信号源 session 的关闭问题。

### 边界

- 外部 Python 插件是受信任代码，不是沙箱；安装流程只处理用户明确指定的本地包，不自动联网下载依赖。
- 插件市场在此版本仍是本地 JSON 索引，只读展示，不负责下载或安装。
- 扫频分析仪目前只有公共 API 契约，不代表已有具体型号接入。
- TUI 仍是实验性界面，范围限于电源、万用表和信号源面板；run plan、示波器完整波形查看和插件管理仍以 CLI/服务为准。
- DS1000Z 的长波形和硬件覆盖有单独的复测边界记录；本节不把文档中的实验计划当成普遍硬件保证。

### 兼容性与验证

- Instrument API V2 和受管插件生命周期从 `v0.8.0` 起提供；外置插件统一要求 `wavebench>=0.8,<0.9`，不与 `v0.7.0` 配套运行。
- 历史发布门禁记录为核心 `583 passed`、外置插件仓库 `108 passed`、Ruff 和 `git diff --check` 通过；核心及插件 wheel/sdist 共生成 14 个发行物。
- 干净虚拟环境完成内建仪器族可用性、外置插件 package check、canonical 接管、卸载和回退验收；这些数字和结果只代表 v0.8.0 发布时的验证状态。

## v0.7.0 — 2026-07-04

依据：tag `v0.7.0`（提交 `585f746`）以及从 `v0.6.0` 到该 tag 的提交记录。

### What's new

- 增加只读网络发现：扫描常见仪器端口，对 SCPI socket 候选发送 `*IDN?`，输出可复制的资源候选。
- 增加本机 HTTP MCP 只读服务，提供 `run.schema`、`run.check` 和 `capture.inspect`；服务默认 loopback，并要求受保护端点使用 Bearer token。
- 为 `run.check` 和 `capture.inspect` 增加路径边界，限制 MCP 读取范围，避免通过接口访问任意路径。
- 建立内建插件 registry、插件 metadata、`plugin list/info/doctor` 和只读本地 market index。
- 增加声明式 SCPI 插件 TOML 的 check、info、doctor 和显式只读 probe。
- 将 CLI 解析、输出、RunService 的 analysis、artifact、restore 和 safety 逻辑拆成独立模块，保持原有命令入口。
- 补充 HTTP MCP、network discovery、插件 market 和声明式 SCPI 插件的项目文档与示例。

### 边界

- MCP 端点只提供只读工具，不执行 run、不切换输出，也不暴露 raw SCPI；请求体和路径范围有限制。
- 默认不导入第三方 Python entry point，必须显式启用发现；本地 market 条目不会被自动加载。
- 声明式 SCPI 文件默认只校验本地 TOML，只有显式 `probe` 才连接指定资源。
- 本版本的插件 registry 是能力目录，不是远程包管理器。

### 验证

- 历史发布门禁记录为 `373 passed`、`16 subtests passed`，Ruff、wheel 内容检查和 `git diff --check` 通过。
- 现场验证仅执行已开机 DP832A 的只读 `*IDN?` probe；HTTP MCP、network discovery 和声明式 SCPI 路径不执行仪器写操作。

## v0.6.0 — 2026-05-08

依据：tag `v0.6.0`（提交 `c54254b`）以及从 `v0.5.0` 到该 tag 的提交记录。

### What's new

- 增加 `report-index` 的 JSON/CSV manifest 和静态 HTML 汇总页，可把多次 run 放在一个索引里查看。
- 增加实验性 Textual TUI，提供 DP800 电源、DM3000/DM3058 万用表和 DG4202 信号源的查看与常用控制；加入 fake 模式和持久日志。
- TUI 信号源面板支持显式设置波形、频率、Vpp 和输出；电源面板显示多通道状态、CC、OVP/OCP 并复用安全校验；DMM 面板覆盖常用测量功能。
- 增加 DMM 切挡后的稳定等待配置 `[dmm].settle_ms_after_function_change`，并减少自动刷新对仪器功能查询的依赖。
- TUI 日志默认写入 `data/tui/wavebench-tui.log`；退出时停止刷新任务，避免继续调度后台 I/O。
- 增加 DP800 保护状态/阈值控制，并支持多通道 source restore。
- 复用 run 期间的仪器 session，补充 TUI 的忙状态、退出和 DMM 读数日志处理。
- 增加 source-scope-DMM 组合示例 plan。

### 边界

- TUI 是实验性、可选依赖的控制面板，不承担 run-plan 编排、完整示波器波形查看或报告系统。
- `report-index` 和报告命令读取已有产物；它们不代替实时采集，也不连接仪器。
- DMM 的具体型号和串口行为仍以 driver/配置中的已验证路径为准。

### 验证

- 历史发布门禁记录为 focused TUI/config/transport tests `79 passed`，全量测试 `299 passed`、`13 subtests passed`；Ruff、`git diff --check` 和公开文档隐私扫描通过。
- TUI 仍复用 `SourceService`、`PowerService`、`DmmService` 和现有配置，不绕过 driver/service 安全层。

## v0.5.0 — 2026-05-06

依据：tag `v0.5.0`（提交 `5f1c5ab`）以及从 `v0.4.4` 到该 tag 的提交记录。

### What's new

- 报告增加实验证据摘要、证据时间线和产物链接，能够从 run 页面跳到 `run.json`、`summary.csv`、采集包和截图等文件。
- 报告布局继续收紧，保持单文件静态 HTML；DMM ACV/DCV 读数可以和 scope 验收信息出现在同一份 run report 中。
- DMM 读取增加可配置的 `settle_ms_before_read` 等待时间。
- 收紧报告的紧凑布局，并补充相应测试。

### 边界

- 报告仍是静态离线 HTML；新增的索引和链接不会改写原始采集数据。
- 等待时间只影响 DMM 读取前的节奏，不等于仪器稳定性或测量精度保证。

### 验证

- 完成一次 source/scope + DMM report smoke：scope 约 `1000.500 Hz`、`1.000 Vpp`，`expect` 与 `expect_fft` 通过；DMM ACV 读数 `0.3530127 V`，expect 通过。

## v0.4.4 — 2026-05-06

依据：tag `v0.4.4`（提交 `0ec5865`）以及该 tag 包含的 v0.4.3 文档准备和功能提交。

### What's new

- 增加 DM3058 LAN/VISA 万用表读取路径，以及 `dmm idn` 和多种 `dmm read` 测量函数。
- run plan 增加 `dmm.read` 步骤；可用 `[steps.expect]` 对数值读数设置范围门禁。
- `dmm.read` 默认读取 DCV，也可指定 ACV、DCI、ACI、RES、FRES、FREQ、PERIOD、CONT、DIODE 或 CAP；expect 以 payload 数值字段为门禁对象，常用字段为 `value`。
- HTML 报告增加 DMM 读数卡片，并在 expect 表中显示 DMM 的预期、实测和状态。
- 增加 DMM smoke 示例、FFT/报告验收检查和示波器输入阻抗保护；补充 `DP800 -> DMM`、`DG4202 -> DMM` 的公开 smoke 计划和脚本。
- 保留 DM3000 RS-232 driver/transport skeleton，未把它写成完整的型号覆盖承诺。

### 合并说明

- 没有 `v0.4.3` 独立 tag。v0.4.3 发布说明中的 `[steps.expect_fft]`、FFT 验收摘要、公开闭环计划和报告布局改进，随 v0.4.4 tag 前后的合并提交一并进入本版本历史。

### 边界

- DMM 支持仍以代码中列出的 LAN/VISA 和串口路径为准；smoke 脚本需要真实接线和设备。
- 报告与 `capture inspect` 仍只读已有 artifact，不连接仪器。

## v0.4.2 — 2026-05-04

依据：tag `v0.4.2`（提交 `e985288`）以及从 `v0.4.1` 到该 tag 的提交记录。

### What's new

- 增加 `[safety_limits]` 软件安全上限：`max_source_vpp`、`max_power_voltage_v` 和 `max_power_current_limit_a`。
- `run check`、`run verify` 和实际写操作都会在连接或写入前检查这些上限；打开输出时还会检查当前设定值。
- `source set-vpp`、`source arb-load`、`power set` 以及 source/power output enable 都会在写入前经过安全上限检查。
- PyVISA 写入、查询、二进制读写和 `*OPC?` 失败统一包装为用户可读的 WaveBench I/O 错误，并保留命令上下文。

### 边界

- 安全上限只拦截超过配置值的明确写操作；不会替用户判断接线、负载或示波器 50 Ω 是否安全。
- 传输层错误包装不会让仪器写操作自动重试。

### 验证

- 历史发布门禁记录为 `171 passed`，Python 3.11/3.12 CI 通过；实际 DG4202 CH2 验证了超限时在写输出前拒绝，设备状态保持不变。

## v0.4.1 — 2026-05-04

依据：tag `v0.4.1`（提交 `fc376e3`）以及该 tag 的准备提交。

### What's new

- 增加 `run verify`，用只读 `*IDN?` 预检 run plan 中引用的仪器。
- PyVISA transport 增加浮点列表、二进制 block、`*OPC?` 查询和空响应后的备用读取。
- 加固 socket 查询处理，兼容真实响应前残留空行的情况，并补充任意波形归一化、DP800 异常响应和 run-plan 仪器选择测试。
- 包版本读取改为优先从项目 metadata 获取；加入 Python 3.11/3.12 的 GitHub Actions 单元测试工作流。

### 边界

- `run verify` 只做可达性和基础身份预检，不执行 plan，也不校验完整仪器状态。
- 备用读取依赖后端是否提供对应 VISA 方法。

### 验证

- 历史发布门禁记录为 `158 passed`；`run verify` 在 RTM2032 + DG4202 组合上完成只读预检，DG4202 CH2 状态解析正确。

## v0.4.0 — 2026-05-02

依据：tag `v0.4.0`（提交 `df58a1d`）以及该提交明确记录的发布边界。

### What's new

- 增加 DG4202 任意波形上传：从已校验的 CSV/NPY 生成 DG4000 14-bit DAC binary block，并通过 `DATA:DAC VOLATILE` 写入。
- `source arb-load` 从离线 dry-run payload 校验扩展到最小上传命令；run plan 增加 `source.arb_load` 步骤。
- 支持用显式 plan 完成上传、输出、RTM2032 采集、FFT/报告和 `[steps.expect]` 验收。
- 记录 DG4202 任意波到 RTM2032 的首条完整流程证据，并保留 payload、采集包和报告产物。

### 边界

- 这是最小完整流程，不是任意波形编辑器；不含 GUI 编辑、跨厂商抽象、非易失波形库、RAF/Ultra Station 工作流或自动波形合成。
- 上传会写入真实信号源；`output-on` 仍需显式指定，实验后状态恢复必须由 plan/调用方明确安排。

## v0.3.0 — 2026-05-01

依据：tag `v0.3.0`（提交 `ee34f5a`）以及该 tag 的 release notes。

### What's new

- 报告顶部增加 Summary card，集中显示 run 状态、步骤、采集、警告、expect、截图、restore 和主要信号指标。
- 增加 `Expected vs measured` 表、从 NPY 生成的轻量 SVG 波形预览、截图缩略图和 `report-assets/manifest.json`；报告仍保持静态、离线可读。
- `capture inspect --fft` 增加离线 FFT 文本摘要：Hann 窗、采样率、频率分辨率、主峰、噪声底、2 到 5 次谐波、粗略 THD，并提示异常时间轴。

### 边界

- 报告和 inspect 只读取已有 artifact，不连接仪器，也不改写原始数据。
- FFT 是轻量文本检查，不是默认报告图表；本版本不增加 GUI、交互图表、新工作流语言、条件/矩阵 plan 或新仪器型号。

### 验证

- 历史发布说明记录为 `124 passed`；报告派生预览和 manifest 不会修改原始采集数据、metadata 或 run records。

## v0.2.0 — 2026-04-30

依据：tag `v0.2.0`（提交 `9a8ac25`）以及该 tag 的 release notes。

### What's new

- 增加离线 capture/run package reader、`capture inspect` 和静态 `run report`。
- `scope capture` 与 run plan 支持截图 artifact；报告会显示截图缩略图和截图区块。
- 报告增加 Signal analysis 区块，汇总频率、Vpp、RMS、均值、duty、rise/fall 和质量告警。
- 增加 WSL 场景下 native RS VISA 不可用时的 `pyvisa-py` fallback。

### 边界

- `capture inspect` 和 `run report` 离线运行，不连接仪器；截图是采集 artifact，不是报告命令的副作用。
- 仍没有 GUI、YAML、条件/循环/矩阵实验；报告只汇总已有 metadata，不重新处理 NPY 波形。

## v0.1.0 — 2026-04-30

依据：tag `v0.1.0`（提交 `a183a3c`）以及该 tag 中的 v0.1 整理清单和此前实现提交。

### What's new

- 建立 RTM2032 的 `scope fetch`/`scope capture` 采集路径，生成 CSV、NPY、JSON metadata 和 `commands.log`；失败时保留 partial artifact 和错误记录。
- 增加波形摘要与质量提示，包括 Vpp、RMS、均值、频率估计、周期/采样点信息，以及适用时的 duty、rise/fall。
- 建立 DG4202 基础信号源控制和离散扫频，并支持显式 source 状态快照/恢复。
- 建立 DP800 的状态读取、设定值写入和显式输出开关。
- 建立 TOML run plan、`run check`、`run schema`、安全耦合 guard、质量恢复和 `[steps.expect]` 记录。

### 边界

- 这是一个窄范围的基线版本：没有 GUI、截图、自动报告、YAML 或条件/循环/矩阵工作流，也不承诺完整仪器状态回滚或跨型号通用抽象。
- 多通道采集按通道顺序执行，不保证同步；RTM2032 只覆盖项目实际使用并核对过的 SCPI 路径。
- source restore 只覆盖文档列出的可恢复字段；电源 `set` 和 `output` 仍是相互独立的显式动作。

## 版本记录边界

- 仓库有 `v0.1.0`、`v0.2.0`、`v0.3.0`、`v0.4.0`、`v0.4.1`、`v0.4.2`、`v0.4.4`、`v0.5.0`、`v0.6.0`、`v0.7.0` 和 `v0.8.0`；没有 `v0.4.3` tag。v0.4.3 的若干提交和文档准备最终包含在 v0.4.4。
- 当前开发分支的包版本可能高于最新稳定 tag；本文件不把未打 tag 的开发提交冒充正式版本。
- tag 中记录的实机验证、测试计数和发布说明是历史记录，不等于当前硬件矩阵或当前主分支的保证。
