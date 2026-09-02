# WaveBench 文档宪法

> 加载时机：任何 `audit`、`migrate`、`write` 或 `review` 任务中，需要判断页面职责、事实来源、状态、生命周期、导航或 Core/plugin 边界时加载。  
> 本文件是 WaveBench 文档分类、来源与治理规则的唯一规范入口；其它 workflow reference 不重复定义这些规则。

## 适用范围与优先级

本规则适用于仓库的公开文档、示例说明、README、Reference、开发文档和 RFC。它不改变代码、CLI、schema、descriptor 或发布流程本身；发生冲突时，这些可执行或已发布事实优先于文档。

文档事实的核验顺序为：

1. 实现、类型模型与 schema；
2. 离线可执行的 `--help`、`run schema`、`run template --list` 和 capability 查询；
3. 聚焦测试、descriptor 与 registry；
4. 已发布 Git tag、release 与 `CHANGELOG.md`；
5. 现有文档。

旧页面只能作为待核验材料，不能互相证明当前行为。

## 页面类别

每篇公开页面只选择一个主类别。页面可以链接到其它类别，但不能借「概览」之名承载所有内容。

| 类别 | Audience | Reader goal | 页面职责与允许内容 | 必须链接到其它位置 |
| --- | --- | --- | --- | --- |
| README / Landing | 首次访问者、评估者、贡献者 | 判断项目是否适用，并进入正确起点 | 项目身份、用途、核心能力、无硬件起步摘要、简短支持摘要、安全提醒、主要入口、贡献与许可证 | 安装详情、完整参数、型号状态、RFC、实现细节、长故障排查 |
| Getting Started | 新环境使用者、首次接入实验台的人员 | 安装、配置或完成首个明确结果 | 最小前置条件、线性步骤、每个关键步骤的预期结果、成功判据、下一步 | 完整 schema、所有选项、架构原因、型号私有信息 |
| Tutorial | 正在学习 WaveBench 工作方式的人员 | 在指导下完成一次完整体验并建立直觉 | 单条端到端学习路径、必要解释、可观察结果、最小安全边界 | 参数全集、所有替代路径、深层设计论证 |
| How-to | 已知目标的操作者 | 可靠完成一个具体任务 | 任务目标、必要条件、硬件风险、最短步骤、Verification、常见失败、相关 Reference | 从零教学、完整 API/schema、开发历史 |
| Reference | 查询精确行为的用户、自动化调用方、插件作者 | 查到准确、完整、可定位的机器事实 | Synopsis、syntax/schema、inputs、outputs、exact behavior、side effects、errors、compatibility；优先生成或自动验证 | 教程叙事、设计取舍、路线图、实验过程记录 |
| Concept | 需要理解模型与取舍的用户、维护者 | 理解系统为何这样设计 | Problem、model、how it works、rationale、trade-offs、稳定边界 | 命令手册、字段全集、发布状态流水账、型号实机证据 |
| Development | Core 或插件开发者 | 修改、测试、发布或迁移实现 | 开发流程、贡献边界、测试门、兼容策略、链接到 API Reference 和 RFC | 当前用户操作指南、型号能力矩阵、私有实验细节 |
| RFC | 设计评审者、实现者 | 审阅提案、冻结合同或理解未发布设计 | 问题、约束、兼容性、决策、验收门和明确状态 | 面向用户的当前功能承诺；未发布实现不得伪装为稳定 Reference |
| Historical | 维护者、追溯者 | 查询旧提案、里程碑、证据或发布时状态 | 日期/版本范围、原始背景、替代页面、不能作为当前事实源的声明 | 当前 CLI、实时支持状态、新的用户路径 |

`Navigation` 是页面的辅助职责，不是独立事实源。导航页只说明阅读目的和下一步，不复制参数、profile、RFC 进度或 schema。

## 页面合同

所有主要页面先在工作笔记中确定：`type`、`audience`、`reader goal`、`canonical facts` 和 `related pages`。不要求把这五项机械写成公开页面的元数据块。

### Tutorial

- Learning goal；
- Prerequisites；
- 一条可完成的流程；
- 每个关键步骤的预期结果；
- 后续阅读入口。

深层原因链接 Concept，参数全集链接 Reference。

### How-to

- 任务目标；
- 必要条件和真实硬件风险；
- 最短可靠步骤；
- Verification；
- 常见失败；
- 相关 Reference。

不把单个任务页扩展成产品总教程。

### Reference

- Synopsis；
- Syntax / Schema；
- Inputs / Fields / Parameters；
- Outputs；
- Exact behavior 和 side effects；
- Errors；
- Compatibility / capability requirements。

可以从稳定实现生成的字段、参数或 capability 表，不长期手工复制。生成结果必须标明来源和生成/验证命令。

### Concept

- Problem / motivation；
- Model；
- How it works；
- Design rationale；
- Trade-offs；
- 相关 Guide 与 Reference。

不把命令步骤、当前版本号、commit、里程碑或型号证据塞进概念页。

## One fact, one canonical source

| 事实 | Canonical source | 文档规则 |
| --- | --- | --- |
| 当前包版本 | `pyproject.toml` 的 `[project].version` | 展示时核对，不在多个页面长期手写 |
| 正式发布版本 | Git tag / release | 只有 tag/release 才能表示正式可用版本 |
| 版本变化 | `CHANGELOG.md`，以对应 tag 和发布提交约束 | 开发分支进度不得伪装成发布历史 |
| CLI 命令、参数和默认值 | CLI 实现和 `wavebench --help` | 参考页生成或验证；Guide 只保留任务所需命令 |
| run plan step、字段和 schema | `run_plan.py` 和 `wavebench run schema`；派生页由 `scripts/generate_docs.py` 写入 `docs/reference/generated/run-schema.md` | 生成页必须以 `python scripts/generate_docs.py --check` 进入 CI；Guide 不复制完整 step 表 |
| run template | 模板 registry 和 `wavebench run template --list` | 只在 Tutorial/How-to 选择适用示例 |
| capability | descriptor、capability/operation registry 与 `capability explain` | 解释模型可以人工维护；精确可用性应查询或生成 |
| 仪器支持状态 | Core 内建 descriptor 与 instrument plugin descriptor | Core 只给简短摘要；型号详情链接插件仓 |
| 配置字段和约束 | config model、parser 和 schema | `wavebench.example.toml` 是示例，不是完整 schema |
| artifact 格式 | artifact writer、typed result、package loader | 按 artifact 家族拆页；机器字段优先生成 |
| 安全语义 | Core safety/service contract 与测试 | 说明风险和流程；不得从旧文档复制限制 |
| 厂商 SCPI、型号参数、quirk、profile、实机验证 | `wavebench-instrument-plugins` 的 descriptor、实现、测试和证据 | Core 文档不维护完整型号矩阵 |
| 未来设计 | RFC、issue 或 roadmap | 不写成 Current；用户页只说明 `unavailable` 或 `unsupported` |

Guide 可以解释或摘要 canonical fact，但不得变成第二份完整、易变的手工表。事实无法核验时，必须标为未验证，不以相邻文档补齐。

## Repository 边界

| 归属 | 文档责任 |
| --- | --- |
| WaveBench Core | 通用仪器抽象、CLI、run plan、artifact、安全模型、capability 模型、插件 API、通用配置、session 和 recovery |
| `wavebench-instrument-plugins` | 具体型号、厂商 SCPI、私有参数、quirk、型号 profile/capability、型号限制和实机验证状态 |
| 两仓交界 | Core 定义通用合同与查询入口；插件仓声明实际型号采用和证据。Core 可提供短支持摘要，但不能重复整张型号 capability 矩阵 |

型号名本身不是越界理由。只有当页面维护型号私有命令、范围、profile 或 evidence 时，内容才应回到插件仓。

## README policy

README 是 landing page，不是 specification。它应包含：项目身份和一句定位、主要入口、WaveBench 解决的问题、核心能力、无硬件 Quickstart 摘要、简短支持概览、安全提醒、Contributing、License 和 Acknowledgements。

README 不应承载：RFC 编号、milestone、A1/A2/A4 阶段、descriptor 内部合同、session/rollback 实现、型号级 capability/profile、易变化的型号参数或完整错误/配置/CLI Reference。需要这些信息时，用一两句摘要链接到唯一权威页面。

## 生命周期

每篇现有页面在 audit 或迁移计划中只选择一个主要动作：

| Action | 适用条件 |
| --- | --- |
| `KEEP` | 职责清楚、事实来源稳定、导航可达 |
| `REWRITE` | 路径和主职责保留，但正文无法通过小修恢复单一读者结果 |
| `SPLIT` | 同页服务多个独立读者目标或混合多个页面类别 |
| `MERGE` | 两页重复维护同一职责或同一易变事实 |
| `MOVE` | 内容职责正确，但目录/类别表达错误 |
| `GENERATE` | 机器事实可由稳定模型、schema、registry 或 help 生成或自动验证 |
| `ARCHIVE` | 历史仍有追溯价值，但不能作为当前事实源 |
| `DELETE` | 没有独立价值，且已被 canonical 页面完整替代 |

不因页面很长就自动 `SPLIT`，不因目录不整齐就自动 `MOVE`，也不为对称性创建空页面。每次 `SPLIT` 或 `MOVE` 都要有目标读者、目标页面、链接处理和内容保全方案。

## 状态 policy

| 状态 | 定义 | 可出现的位置 |
| --- | --- | --- |
| Current | 已发布，并由当前实现、测试和文档共同支持 | Guide、Reference、Concept、README 摘要 |
| Experimental | 已可用，但稳定性、兼容性或支持范围未承诺；必须显式标注 | Guide、Reference、Concept |
| Proposed / Future | 未实现、未发布或仅候选设计 | RFC、issue、roadmap；不得作为用户操作承诺 |
| Historical | 旧提案、里程碑、证据、旧发行物或替代前合同 | RFC/archive/对应 Git tag；页面顶部说明替代入口 |

「开发线已实现但未发布」对用户文档属于 `Proposed / Future`，不是 `Current`。它可以在 RFC 或 Development 文档中作为实施状态出现，但必须明确不能据此宣称稳定能力、提高插件版本门或执行未经授权的硬件操作。

## 导航与迁移护栏

目标分类可以逐步采用 `getting-started/`、`tutorials/`、`how-to/`、`reference/`、`concepts/`、`development/`、`rfcs/` 和 `archive/`，但目录不是迁移的前置条件。只有明确读者目标和内容来源时才创建页面。

所有文档系统都应支持以下阅读路径：

1. README → Quickstart → 离线或 fake 的可见结果；
2. Installation → Configure Bench → `doctor` → `run verify`；
3. Tutorial / How-to → `run plan` → artifacts → report；
4. error → Troubleshooting → Error Reference；
5. navigation/search → 精确 Reference；
6. Concepts → 设计取舍；
7. Development → Driver/Plugin guide → API Reference；
8. RFC / CHANGELOG → 未来设计或发布历史。

迁移先建立目标页面，再转移一类职责，更新入链/出链并验证，最后才归档或删除旧页面。日常 review 只检查本次 diff、直接相关页面和 canonical source；只有用户明确要求或发现系统性问题时才升级为全仓 audit。
