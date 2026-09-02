# WaveBench 文档系统审计与迁移提案

> 审计日期：2026-09-02  
> 代码基线：`master@d602637`，包版本 `0.8.26`  
> 状态：第一轮审计完成；尚未执行目录迁移或大规模重写

## 结论

WaveBench 已经积累了覆盖 CLI、run plan、artifact、插件、安全模型和 RFC 的大量文档，但当前主要问题不是覆盖不足，而是页面职责与事实归属失控：landing page、指南、Reference、Concept、RFC 和开发里程碑经常在同一页出现；版本、capability、schema 与型号状态又被多处手写复制。

第一轮不移动现有文件。先建立 `wavebench-docs` 仓库级 Skill、明确 canonical source、保存完整 inventory，并用轻量脚本把确定性的断链和漂移检查接入 CI。后续迁移从 README、docs index、Quickstart 和最大的 mixed-purpose guide 开始，每轮只处理 2～4 个页面。

本次审计覆盖：

- 根 `README.md`、`CHANGELOG.md` 和 `SKILL.md`；
- `docs/` 下全部 41 份 Markdown；
- `plans/README.md` 和 19 份受跟踪 plan；
- `.agents/skills/wavebench/` 的入口与 8 份 reference；
- CLI、run schema、config、artifact、capability、registry 和 safety 的实现入口及相关测试；
- GitHub Actions 中现有的离线验证路径。

`tool-of-rei/` 是本机工作区，不属于公开文档 inventory。根 `SKILL.md` 是指向 `.agents/skills/wavebench/SKILL.md` 的受跟踪符号链接，因此 54 个 Markdown 路径对应 53 个规范文件。

## 当前事实源

| 易变事实 | Canonical source | 文档处理 |
| --- | --- | --- |
| 当前包版本 | `pyproject.toml` 的 `[project].version`；运行时由 `src/wavebench/__init__.py` 读取 distribution metadata | 入口页需要展示时由检查器核对，不在多页独立维护 |
| 正式发布版本 | Git tag / release | `CHANGELOG.md` 解释变化，但不反向定义 tag |
| 版本变化 | `CHANGELOG.md`，受对应 tag 的提交与测试约束 | 开发分支进度不得写成正式发布 |
| CLI 命令和参数 | `src/wavebench/cli_parser.py:29` 的 `build_parser()` 与实际 `wavebench --help` | CLI Reference 应生成或直接链接运行时输出 |
| run plan step 和字段 | `src/wavebench/services/run_plan.py:378` 的 `StepSchema`、`STEP_SCHEMAS`、`format_run_plan_schema()` 与 `wavebench run schema` | 指南不复制完整 step 表 |
| run template | 模板 registry 与 `wavebench run template --list` | 页面只选择与任务有关的模板 |
| 配置字段 | `src/wavebench/config.py` 的配置 dataclass 与 `load_config()` | `wavebench.example.toml` 只是示例，不是完整 schema |
| capability | `InstrumentDescriptor`、`CAPABILITY_METHODS`、`InstrumentRegistry`、`OPERATION_REGISTRY` | 精确能力由 descriptor/registry 查询或生成 |
| 仪器支持状态 | Core 内建 descriptor 与已安装的 instrument plugin descriptor | Core 页面只保留短摘要和查询入口 |
| artifact 格式 | `run_artifacts.py`、typed operation result、`data/packages.py` loader | 按 artifact 家族拆分，机器字段优先生成 |
| 安全约束 | Core safety/service contract、`OperationSpec` 与聚焦测试 | Guide 摘要链接稳定 Concept/Reference，不复制旧说明 |
| 厂商私有 SCPI、型号限制、quirk 和实机证据 | `wavebench-instrument-plugins` 的 descriptor、实现、测试和证据 | Core 仓库不维护完整型号矩阵 |

事实发生冲突时，按「实现和模型 → 可执行 help/schema → 聚焦测试 → descriptor/registry → 对应 tag 的发布记录 → 当前文档」核验。RFC 是决策或提案来源，不是当前可用性的证明。

## 完整文档 inventory

`Canonical` 表示该页面是否应直接维护所述事实。每篇页面只给一个主要动作；动作是迁移判断，不表示本轮已经执行。

### 根入口与导航

| File | Type | Audience | Canonical | Problems | Action |
| --- | --- | --- | --- | --- | --- |
| `README.md` | Landing / Mixed | 初次访问者、操作者、贡献者 | 否；版本、CLI、capability 来自代码、tag 与 descriptor | 228 行内混入 landing、Quickstart、型号矩阵、RF 里程碑、事务细节和安全 Reference | REWRITE |
| `CHANGELOG.md` | Release history | 用户、发布维护者 | 是；仅限已打 tag 的版本变化说明 | 当前边界清楚；应继续拒绝未发布开发进度 | KEEP |
| `docs/README.md` | Landing / Mixed | 中文文档读者 | 否；只应维护导航 | 与根 README 重复版本、scope/RF 状态和安全分类 | REWRITE |
| `docs/README_EN.md` | Landing / Mixed | 英文文档读者 | 否；只应维护英文入口和语言覆盖说明 | 复制版本与 capability；多数深页仍为中文，入口没有形成完整英文旅程 | REWRITE |
| `docs/project/README.md` | Navigation / Mixed | 用户、开发者 | 否；只应维护导航 | 自称只负责导航，却复制 RF production 状态、RFC 里程碑与精确预算 | MERGE |
| `plans/README.md` | Reference index | plan 使用者 | 是；plan 目录及风险说明 | 已覆盖全部 plan；列表仍需机械核对目录变化 | KEEP |

### Guides 与 contributing

| File | Type | Audience | Canonical | Problems | Action |
| --- | --- | --- | --- | --- | --- |
| `docs/project/guides/WaveBench_CLI形态.md` | Reference / Mixed | CLI 用户、脚本作者 | 否；参数来自 `--help` | CLI 总览混入 RF/Source V2 合同和多处能力表 | SPLIT |
| `docs/project/guides/WaveBench_HTTP_MCP_只读接口.md` | Reference | MCP 客户端、部署者 | 部分；HTTP 行为应由实现与测试约束 | 范围单一，边界清楚 | KEEP |
| `docs/project/guides/WaveBench_RF信号源使用指南.md` | How-to / Mixed | RF 操作者、plan 作者 | 否；型号状态来自插件 descriptor | 操作步骤、完整 production 矩阵、历史 evidence 和里程碑混写 | SPLIT |
| `docs/project/guides/WaveBench_TUI终端控制面板.md` | How-to | TUI 使用者 | 部分；命令和行为来自实现 | 页面短且任务明确；实验性标签需要持续保留 | KEEP |
| `docs/project/guides/WaveBench_run_plan_使用指南.md` | Tutorial / How-to / Reference | plan 作者、实验操作者、分析用户 | 否；字段来自 `run schema`，artifact 来自 model | 837 行混合起步、完整 step Reference、频响算法、artifact 与报告部署 | SPLIT |
| `docs/project/guides/WaveBench_可安装仪器插件.md` | How-to / Mixed | 插件用户、运维人员 | 否；lifecycle 和 registry 来自实现 | 安装流程混入 RTM transport 验收与具体覆盖矩阵 | SPLIT |
| `docs/project/contributing/WaveBench_插件开发指南.md` | Development / Reference | 插件开发者 | 部分；开发流程可维护，API 签名不可手写复制 | 350 行中大段 capability/API Reference 与开发流程混写 | SPLIT |
| `docs/project/contributing/WaveBench_新增仪器驱动指南.md` | Development | Core 驱动开发者 | 是；接入流程，具体 API 仍链接实现 | 职责清楚，保留 Core 与外置插件选择说明 | KEEP |

### Design

| File | Type | Audience | Canonical | Problems | Action |
| --- | --- | --- | --- | --- | --- |
| `docs/project/design/WaveBench_项目边界.md` | Concept / Product boundary | 用户、维护者 | 是；稳定范围与安全红线 | RF 行项目塞入 DSG830 证据、M/A 阶段和精确 profile | REWRITE |
| `docs/project/design/WaveBench_设备抽象层.md` | Concept | Core、插件开发者 | 是；稳定分层模型 | 当前架构、早期 MVP 目录、伪代码和型号历史混杂 | REWRITE |
| `docs/project/design/WaveBench_多仪器协同流程设计.md` | Concept / Reference / History | 用户、Core 开发者 | 部分；稳定 run 模型可保留 | 692 行同时维护建议第一版、当前 schema、实机记录和实施步骤 | SPLIT |
| `docs/project/design/WaveBench_sweep状态恢复设计.md` | Historical proposal | Core 开发者 | 否；当前 restore 以实现/schema 为准 | 第一版未完成清单仍像当前设计，且 restore 字段与现行说明冲突 | ARCHIVE |
| `docs/project/design/WaveBench_RF信号源设计.md` | Concept / Reference / History | Core、插件开发者 | 部分；稳定领域合同可保留 | 当前 capability、长期模型、CLI、M0～M4/A5 和提交证据混写 | SPLIT |
| `docs/project/design/WaveBench_RF信号源开发里程碑.md` | Development history | Core、插件维护者 | 否；当前 capability 来自 descriptor | 重复 production 清单，主体是 A1～A5 实机与提交历史 | ARCHIVE |

### Reference

| File | Type | Audience | Canonical | Problems | Action |
| --- | --- | --- | --- | --- | --- |
| `docs/project/reference/WaveBench_数据输出格式.md` | Reference / Mixed | 用户、分析脚本作者 | 否；字段来自 artifact models/loaders | 871 行混入五类 artifact；开头「只支持」与后文截图、报告等内容冲突 | SPLIT |
| `docs/project/reference/WaveBench_配置文件格式.md` | Reference | 实验台维护者、CLI 用户 | 否；字段来自 config model | `[safety_limits]`、`[source]` 重复，默认值和 driver 列表易漂移 | REWRITE |
| `docs/project/reference/WaveBench_错误处理和日志策略.md` | Reference / Concept | CLI 用户、自动化调用者、插件作者 | 部分；策略可解释，异常/退出码来自实现 | 顶层错误分类、异常类、scope 命令序列、日志和 session 合同混写 | REWRITE |
| `docs/project/reference/plugins/WaveBench_可执行仪器插件API.md` | API Reference / Mixed | 插件开发者、Core 维护者 | 否；签名和 capability 映射来自公开 Protocol/model | 783 行囊括全部领域 API、开发线版本门和测试矩阵 | SPLIT |
| `docs/project/reference/plugins/WaveBench_声明式SCPI插件.md` | Schema Reference | 本地实验室用户 | 否；字段来自 validator/schema | 字段、默认值与约束可从实现生成；人工页只需用途与示例 | GENERATE |
| `docs/project/reference/plugins/WaveBench_插件市场索引.md` | Schema Reference | 插件发现用户、索引生成者 | 否；JSON 结构来自 model/parser | 手写 schema、默认路径、搜索域和示例版本易漂移 | GENERATE |
| `docs/project/reference/plugins/WaveBench_插件注册表.md` | Concept / Reference map | 插件用户、开发者 | 是；三种插件路径与 Core/plugin 边界 | 少量 alias/CLI 细节后续可生成，当前地图价值明确 | KEEP |

### RFC

RFC 的 `Canonical` 只针对提案与裁决，不代表当前产品可用性。

| File | Type | Audience | Canonical | Problems | Action |
| --- | --- | --- | --- | --- | --- |
| `docs/project/rfcs/README.md` | RFC index | 维护者、插件作者 | 是；RFC 状态词与索引 | 索引复制大量开发线实现细节，并与项目总目录重复 | REWRITE |
| `docs/project/rfcs/WaveBench_scope可移植性RFC组合说明.md` | RFC series / History | Scope 维护者 | 是；系列关系 | 规范词典、实施计划、M0～M8 完成记录和验收日志混写 | SPLIT |
| `docs/project/rfcs/WaveBench_scope可移植性RFC-0001_消费型文本查询.md` | Superseded RFC | Transport、插件维护者 | 是；被替代裁决 | 已由 R1 合同取代，保留价值是历史决策 | ARCHIVE |
| `docs/project/rfcs/WaveBench_scope可移植性RFC-0002_通道输入状态.md` | RFC / Implementation record | Scope 插件作者 | 是；接口裁决 | V2 规范、实现状态、迁移与验收矩阵混写 | SPLIT |
| `docs/project/rfcs/WaveBench_scope可移植性RFC-0003_截图framing与菜单.md` | Superseded RFC | Scope 插件作者 | 是；被替代裁决 | 原入口被更严格的 binary/screenshot 合同替代 | ARCHIVE |
| `docs/project/rfcs/WaveBench_scope可移植性RFC-0004_数字通道状态.md` | RFC / Implementation record | Scope 插件作者 | 是；数据模型裁决 | 规范、核心实现和插件 opt-in 状态混写 | SPLIT |
| `docs/project/rfcs/WaveBench_scope可移植性RFC-0005_可组合状态快照.md` | RFC / Implementation record | Scope 插件作者 | 是；snapshot 裁决 | 候选模型、文档裁决和离线验收阶段混写 | SPLIT |
| `docs/project/rfcs/WaveBench_scope可移植性RFC-0006_采集状态与平均采集.md` | RFC bundle | Scope/Core 维护者 | 是；三个子合同 | 806 行内三个成熟度不同的子 RFC 共用顶层状态 | SPLIT |
| `docs/project/rfcs/WaveBench_scope可移植性RFC-0007_统计FFT与光标读取.md` | RFC bundle | Scope/Core 维护者 | 是；三个只读能力裁决 | statistics、FFT、cursor 无法独立判断生命周期 | SPLIT |
| `docs/project/rfcs/WaveBench_scope可移植性RFC-0008_有界波形传输裁决.md` | RFC summary | Waveform 维护者 | 部分；完整合同在标准波形 RFC | 与标准波形 RFC 高度重叠，形成双入口 | MERGE |
| `docs/project/rfcs/WaveBench_scope可移植性RFC-0009_SINGLE模式终态STOP证明.md` | RFC addendum | Acquisition 维护者 | 是；窄裁决 | 边界清楚；未发布状态由索引统一解释 | KEEP |
| `docs/project/rfcs/WaveBench_transport重放与session健康RFC.md` | RFC / Implementation history | Transport 维护者 | 是；replay/session 裁决 | Accepted 规范、实施状态和开发里程碑未分层 | SPLIT |
| `docs/project/rfcs/WaveBench_scope通用扩展接口RFC.md` | RFC / History | Scope/Core 维护者 | 是；R1.3 总合同 | 2306 行混入规范、候选实现、否决方案、里程碑和 addendum | SPLIT |
| `docs/project/rfcs/WaveBench_scope通用扩展接口RFC-R1.3-acceptance-addendum.md` | Acceptance record | Core、插件迁移者 | 否；总 RFC 已包含同内容 | 与总 RFC 第十二节重复，形成两个验收入口 | MERGE |
| `docs/project/rfcs/WaveBench_scope通用扩展接口RFC_核心实施说明.md` | Implementation note | Core、插件作者 | 否；当前行为来自实现 | 文件名和目录把随开发线变化的实施说明伪装成 RFC | MOVE |
| `docs/project/rfcs/WaveBench_source能力状态与复合输出安全RFC.md` | RFC bundle / History | Source/Core 维护者 | 是；多轮 Source V2 裁决 | 4122 行串联多轮修订、已实现能力、候选和里程碑，无法按 feature 判断状态 | SPLIT |
| `docs/project/rfcs/WaveBench_标准波形有界二进制传输RFC.md` | RFC / Implementation record | Waveform 维护者 | 是；标准传输裁决 | 稳定合同、开发线实现、commit 基线和实机验收混写 | SPLIT |

### 现有 WaveBench Skill

| File | Type | Audience | Canonical | Problems | Action |
| --- | --- | --- | --- | --- | --- |
| `SKILL.md` | Compatibility entry | Agent Skills host | 否；符号链接到规范入口 | 相对链接取决于宿主是否按 symlink target 解析；仓库校验器已把 target 设为规范入口 | KEEP |
| `.agents/skills/wavebench/SKILL.md` | Skill entry | WaveBench Agent | 是；运行、诊断、代码与硬件安全工作流 | 与新文档 Skill 有相邻职责，但不应承载完整文档信息架构 | KEEP |
| `.agents/skills/wavebench/references/development-validation.md` | Skill reference | 开发任务 | 是；开发验证与交接 | 文档段只保留通用开发验证；文档治理交给 `wavebench-docs` | KEEP |
| `.agents/skills/wavebench/references/eval-prompts.md` | Skill reference | Skill 维护者 | 是；触发与安全回归 | 范围清楚 | KEEP |
| `.agents/skills/wavebench/references/plugins.md` | Skill reference | 插件操作任务 | 是；插件操作工作流 | 范围清楚 | KEEP |
| `.agents/skills/wavebench/references/power-and-dmm.md` | Skill reference | 电源、DMM 任务 | 是；硬件工作流 | 范围清楚 | KEEP |
| `.agents/skills/wavebench/references/run-plans.md` | Skill reference | run plan 任务 | 是；安全执行工作流 | 与文档 How-to 不同，不应合并 | KEEP |
| `.agents/skills/wavebench/references/safety-and-recovery.md` | Skill reference | 实时写入任务 | 是；安全门与恢复 | 范围清楚 | KEEP |
| `.agents/skills/wavebench/references/scope-and-capture.md` | Skill reference | 示波器任务 | 是；采集工作流 | 范围清楚 | KEEP |
| `.agents/skills/wavebench/references/source-and-harmonics.md` | Skill reference | 信号源任务 | 是；信号源工作流 | 范围清楚 | KEEP |

## Plan inventory

19 份 plan 均由 `plans/README.md` 导航，未发现真实 IP、序列号、串口或 VISA 资源。下列动作只表示后续样例治理方向。

| Plan | 角色与问题 | Action |
| --- | --- | --- |
| `active_filter_raw_2d_10mv_2v_10hz_5mhz.toml` | 有源 DUT 高成本二维扫频，属于进阶实验 | MOVE |
| `closure_sine_1k.toml` | 当前公开正弦闭环示例 | KEEP |
| `closure_sine_1k_fft.toml` | 文件已声明为被替代的 v0.4 历史示例 | ARCHIVE |
| `closure_triangle_1k.toml` | 当前公开三角波闭环示例 | KEEP |
| `demo_dg4202_10k_screenshot_report.toml` | v0.2 型号绑定示例，与通用 scope quality 示例近重复 | MERGE |
| `dg4202_duty_10k_power_ch2_check.toml` | 台架验收性质强，不是通用用户路径 | ARCHIVE |
| `dp800_scope_probe_voltage_steps.toml` | 两次 power set，无 restore 或 safety gate，不能继续作为普通示例 | ARCHIVE |
| `example_dmm_acv_source_smoke.toml` | 有明确接线提醒的通用 source→DMM 示例 | KEEP |
| `example_scope_expect_quality.toml` | 通用 scope quality 示例 | KEEP |
| `example_source_scope_dmm_report.toml` | 多仪器 report 示例 | KEEP |
| `passive_filter_2d_calibrated.toml` | 引用时间戳 `data/runs` 路径，跨机器不可复用 | REWRITE |
| `passive_filter_adaptive_5mhz.toml` | 进阶频响流程 | MOVE |
| `passive_filter_dense_2d_calibrated.toml` | 引用时间戳 `data/runs` 路径，跨机器不可复用 | REWRITE |
| `passive_filter_raw_2d_10hz_1mhz.toml` | 进阶二维频响样例 | MOVE |
| `passive_filter_raw_2d_10hz_1mhz_500mv_1v_2v.toml` | 与 retry 版本共享同一测量网格 | MERGE |
| `passive_filter_raw_2d_10hz_1mhz_retry_test.toml` | 只为 warning/retry 验证增加开关，更像测试资产 | ARCHIVE |
| `through_baseline_2d_10k_500k.toml` | 与 stable baseline 目标重复 | MERGE |
| `through_baseline_2d_stable.toml` | 当前 calibrated plan 引用的基线流程 | KEEP |
| `through_diagnostic_100mvpp.toml` | 诊断型进阶样例 | MOVE |

## 最严重的 10 个系统问题

1. **没有独立 Quickstart。** 根 README 的离线步骤位于 `README.md:94-126`，但没有明确的 reader outcome、逐步预期结果或单一可见成功产物；初次体验仍散落在 landing page。
2. **根 README 同时承担至少五类职责。** 型号矩阵在 `README.md:129-141`，RF production 细节在 `README.md:143-151`，安全 Reference 在 `README.md:184-203`。入口因此会随实现细节频繁变化。
3. **三个入口重复维护同一事实。** `README.md:10`、`docs/README.md:5` 和 `docs/README_EN.md:5` 都手写开发版本与稳定 tag；scope focus、RF 状态和 I/O 分类也在多处重复。
4. **run plan 主指南已经失去单一读者目标。** `WaveBench_run_plan_使用指南.md` 有 837 行，`420-593` 行维护 Source V2 step，`701-783` 行维护 artifact，前段又承担 Quickstart 与频响教程。
5. **Reference 主要靠人工复制机器事实。** `WaveBench_配置文件格式.md:378-431` 与 `525-587` 重复 `[safety_limits]` 和 `[source]`；`WaveBench_数据输出格式.md:11-17` 的范围陈述与后文新增 artifact 冲突。
6. **当前版本与能力状态已经发生漂移。** 包版本是 `0.8.26`，但 `WaveBench_RF信号源设计.md:5,18` 和开发里程碑仍称 Core `0.8.25` 开发线，`docs/project/README.md:56` 仍维护 `0.8.24` 开发线状态。
7. **RFC 既是规范，又是开发日志。** Scope 总 RFC 为 2306 行，Source V2 RFC 为 4122 行；Accepted、Implemented-unreleased、候选实现、commit 和实机 evidence 无法按 feature 快速区分。
8. **Core/plugin 文档边界被型号状态污染。** DSG830、RTM2032、DG4202 等 production profile 在 README、项目边界、RF 设计、里程碑和指南中反复出现；精确状态应由插件 descriptor 与插件仓库负责。
9. **旧设计仍与当前行为并列。** `WaveBench_sweep状态恢复设计.md:302-308` 只列四个恢复字段，`WaveBench_多仪器协同流程设计.md:604-616` 又列出 duty 字段；旧 proposal 未归档导致读者无法判断现行合同。
10. **基线缺少文档 CI，样例治理也没有机械门。** 原 CI 只运行 Ruff、pytest 和 Windows CLI smoke；两个 calibrated plan 还引用受跟踪文件外的时间戳 `data/runs` 路径。本分支新增轻量 audit，但生成式 Reference 与 schema snapshot drift 尚未建立。

## 用户旅程评估

| Journey | 当前断点 | 目标入口 |
| --- | --- | --- |
| 第一次看到 WaveBench | README 信息过载；离线流程没有独立成功判据 | `README.md` → `docs/index.md` → `getting-started/quickstart.md` |
| 第一次连接实验台 | 安装、配置、`doctor`、`run verify` 分散在入口、配置 Reference 和 run 指南 | `installation.md` → `configure-bench.md` → `doctor` → `run verify` |
| 做一次真实实验 | 837 行 run 指南同时承担教程和 Reference | 窄 Tutorial/How-to → `run plan` → artifact → report |
| 出错 | 有错误策略 Reference，但没有按症状行动的 Troubleshooting | Error message → `how-to/troubleshooting.md` → `reference/errors.md` |
| 查精确参数 | `--help` 和 `run schema` 是事实源，但导航先落到手写混合页 | Search/nav → generated CLI/config/run Reference |
| 理解设计 | Concept 页面混入版本、型号和里程碑 | `concepts/` 下的稳定模型页 |
| 新增仪器 | 两份开发指南可用，但插件 API 总页过大 | `development/instrument-drivers.md` 或 `plugin-development.md` → generated API Reference |
| 查未来设计或历史 | RFC 状态定义存在，但正文与当前实现记录混杂 | `rfcs/index.md` 查决策，`CHANGELOG.md` 查发布，`archive/` 查实施历史 |

## 目标信息架构

以下是迁移方向，不是一次性建空目录的任务清单：

```text
docs/
  index.md
  getting-started/
    quickstart.md
    installation.md
    configure-bench.md
  tutorials/
  how-to/
    run-an-experiment.md
    use-rf-source.md
    use-tui.md
    serve-mcp.md
    manage-plugins.md
    troubleshooting.md
  reference/
    cli.md
    configuration.md
    run-schema.md
    artifacts.md
    capabilities.md
    instrument-support.md
    errors.md
    plugins/
  concepts/
    architecture.md
    safety-model.md
    capability-model.md
    sessions-and-recovery.md
    device-abstraction.md
    plugin-model.md
  development/
    contributing.md
    documentation.md
    plugin-development.md
    instrument-drivers.md
    testing.md
  rfcs/
    index.md
  archive/
```

第一批只创建具有明确用户任务的页面：`docs/index.md`、Quickstart、Installation/Configure Bench、run experiment、Troubleshooting、CLI/config/run Reference、Architecture/Safety、documentation workflow。其余页面在真实内容拆分到来时再创建。

`docs/project/` 没有额外语义，可以分批消除。迁移前先记录入链；对高入链旧路径保留短说明，避免外部链接立即失效。

## `wavebench-docs` Skill 设计

```text
.agents/skills/wavebench-docs/
  SKILL.md
  agents/openai.yaml
  references/
    information-architecture.md
    audit.md
    migrate.md
    write.md
    review.md
    eval-prompts.md
  scripts/
    audit_docs.py
```

入口只保留触发边界、事实优先级、四种模式路由和不变量；详细规则按模式加载。`audit` 负责 inventory、用户旅程和系统问题，`migrate` 负责已接受方案的小步实施，`write` 负责单页合同与事实核验，`review` 只检查 diff 及直接关联页面，不默认升级为全仓审计。

`allow_implicit_invocation` 保持开启，但 description 同时要求「文档开发是主要交付物」，并排除以下请求：只读查文档、仪器操作、普通代码修改中的附带一句说明、无关项目的 Markdown 和通用中文润色。这样可以在明确的 WaveBench 文档开发任务中自动选中 Skill，又不让日常使用和代码开发被文档全流程劫持。触发回归用例单独维护在 `references/eval-prompts.md`。

Skill 不授权硬件访问。help、schema、template 与生成检查必须离线；任何真实设备示例的执行都要另行进入 `wavebench` 的安全工作流并取得明确授权。

## 第一轮 pilot migration 建议

### Slice 1：README

- 将根 README 收敛为 landing page。
- 只保留项目定位、主要入口、4～6 个核心能力、无硬件 Quickstart 摘要、短支持表、安全提醒、Contributing、License 和 Acknowledgements。
- 删除 RF 阶段号、descriptor 内部合同、精确 profile 和 recovery 实现细节，改为一两句摘要链接。

完成标准：README 不再维护完整 capability 状态；所有移出的技术内容都有目标页，不丢失信息。

### Slice 2：docs index

- 将 `docs/README.md` 与 `docs/project/README.md` 的导航职责合并到 `docs/index.md`。
- 入口按用户旅程组织，不按历史目录堆文件名。
- 英文入口保留明确的语言覆盖边界，不伪装成完整英文文档站。

完成标准：八条旅程都有唯一首选入口；导航页不再复制版本和 RFC 开发状态。

### Slice 3：Quickstart

- 从 README 和 run plan 指南提取一条无硬件、可见、可验证的完整流程。
- 优先使用 plan template/check 或 `tui --fake`；每一步写明预期结果。
- 安装背景、配置全集和设计原因分别链接 Installation、Reference 和 Concepts。

完成标准：新环境可以按单一路径得到明确成功结果，且全过程不连接仪器。

### Slice 4：run plan 拆分

- 保留最短可靠执行流程为 How-to。
- 将 step/字段移到 generated `run-schema` Reference。
- 将 artifact 段移到 artifact Reference，将频响与校准拆为独立 Tutorial/How-to。

完成标准：原 837 行页面的每一段都有去向；`wavebench run schema` 与 Reference 由同一检查保证一致。

每个 slice 独立提交并运行链接 audit、相关离线 CLI/schema、聚焦测试和 `git diff --check`。MkDocs Material、GitHub Pages、搜索和版本化文档放在 taxonomy 与导航稳定之后。

## `wavebench-docs` 与中文写作 Skill 的分工

| 职责 | `wavebench-docs` | `tech-doc-style-chinese` |
| --- | --- | --- |
| 页面 type、audience、reader outcome | 负责 | 不负责 |
| 信息架构、导航、用户旅程 | 负责 | 不负责 |
| canonical source 与重复事实 | 负责 | 不负责 |
| Core/plugin 边界 | 负责 | 不负责 |
| Current/Experimental/RFC/History | 负责 | 不负责 |
| `KEEP`～`DELETE` 生命周期判断 | 负责 | 不负责 |
| CLI/schema/example 技术核验 | 负责 | 不负责 |
| 中文语气、标点、术语、留白和扫读性 | 确定结构后调用 | 负责 |
| 机器字面量保护 | 提供项目上下文 | 负责最终表达检查 |

`tech-doc-style-chinese` 保留为最后的中文表达层。它本机现有的 `Project-Overrides.md` 使用另一个项目的 `2.0`/`3.0` 版本规则，不适用于 WaveBench；本工作流不加载或复制该覆盖文件。

## 机械验证方案

本分支新增 `.agents/skills/wavebench-docs/scripts/audit_docs.py`，使用 Python 标准库，默认规则如下：

- 失效相对链接和越出仓库的链接：error；
- 高置信度失效 anchor：warning；
- 无入链 Markdown、重复 H1、无 H1、超过 600 行：warning；
- 非 RFC 页面中的「当前开发线」版本漂移：warning；
- 非文档网段 IP、VISA resource、序列号和本机绝对路径：warning；
- root `SKILL.md` 按符号链接 target 解析，避免把兼容入口误判为第二套事实源。

warning 默认不阻断 CI；CI 使用 `--quiet-warnings`，只显示并阻断 error，避免每次评审重复刷出存量架构告警。局部 review 显式传入改动路径，完整 audit 才显示全仓 warning；`--strict` 可在清理存量告警后逐步启用。脚本不判断页面类型、是否应拆分、用户旅程或设计内容归属。

首轮暂不实现以下检查：

- CLI/schema snapshot drift：仓库还没有规范 snapshot 或生成目标；先在 run-schema pilot 中建立一个窄生成流程。
- generated docs drift：等首个 generated Reference 确定 source marker 和生成命令后再加，避免先发明无人使用的框架。
- 外链在线可用性：不把网络波动引入基础 CI。
- 全自动 anchor 阻断：不同 Markdown renderer 的 slug 规则仍可能产生误报。

原则保持不变：lint 检查文档有没有坏，Agent 检查文档有没有长歪。
