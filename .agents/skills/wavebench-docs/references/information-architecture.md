# 信息架构与事实源

> 加载时机：判断文档类型、目录、页面合同、事实归属、Core/plugin 边界或用户旅程时加载。

## 页面类型

采用 Diátaxis 的四类用户文档，但按 WaveBench 的实际需求拆页，不为填满目录制造空页面。

| Type | Reader outcome | 不应承担 |
| --- | --- | --- |
| Tutorial | 在指导下学习并完成一条端到端流程 | 完整接口枚举、长篇设计论证 |
| How-to | 完成一个已经明确的任务 | 从零教学、完整 schema |
| Reference | 快速查到准确、完整、结构化的事实 | 教程叙事、路线图 |
| Concept | 理解模型、原因和 trade-off | 逐项参数表、发布状态流水账 |

README 是 landing page；RFC、开发里程碑、发布历史和贡献指南是独立文档类型，不强塞进上述四类。

## 页面合同

每篇主要页面先确定以下字段，可放在工作笔记中，不要求把元数据块机械写进所有公开页面：

- `type`
- `audience`
- `reader goal`
- `canonical facts`
- `related pages`

页面骨架按类型选择：

- Tutorial：Learning goal、Prerequisites、单条成功流程、关键步骤预期结果、下一步。
- How-to：任务、必要条件与硬件风险、最短可靠步骤、Verification、常见失败、Reference。
- Reference：Synopsis、Syntax/Schema、Inputs、Outputs、Exact behavior、Side effects、Errors、Compatibility。
- Concept：Problem、Model、How it works、Rationale、Trade-offs、相关 Guide/Reference。

## Canonical sources

| Fact | Canonical source | 文档策略 |
| --- | --- | --- |
| 当前包版本 | `pyproject.toml` 的 `[project].version` | 入口页不手写多份；需要展示时由检查器核对 |
| 正式发布版本 | Git tag / release | `CHANGELOG.md` 解释版本变化，不反向定义 tag |
| 版本变化 | `CHANGELOG.md`，内容受 tag 与发布提交约束 | 不把开发分支进度写成正式发布 |
| CLI 命令与参数 | `src/wavebench/cli_parser.py`、`wavebench --help` | Reference 优先生成或嵌入已验证输出 |
| run plan step 与字段 | `src/wavebench/services/run_plan.py`、`wavebench run schema` | 不在多个指南复制完整 step 表 |
| run template | 模板 registry、`wavebench run template --list` | 指南只选任务所需示例 |
| 配置字段 | `src/wavebench/config.py` 的模型与 `load_config()` | `wavebench.example.toml` 是示例，不是完整 schema |
| capability | `InstrumentDescriptor`、capability/operation registry | 用户页解释模型，精确支持由 descriptor 查询或生成 |
| 仪器支持状态 | Core 内建 descriptor 与已安装 plugin descriptor | 汇总页保持简短，并标明来源与生成时间 |
| artifact 格式 | artifact writer、typed result model、package loader | 按 artifact 家族拆 Reference，机器字段优先生成 |
| 安全约束 | Core safety/service contract 与测试 | 用户摘要链接到稳定 Concept/Reference；不得从旧文档抄写 |
| 厂商 SCPI、型号限制与 quirks | `wavebench-instrument-plugins` 的 descriptor、代码与证据 | Core 文档只说明通用合同和查找方式 |

事实冲突时使用以下优先级：实现和模型 → 可执行 help/schema → 聚焦测试 → descriptor/registry → 对应 tag 的发布记录 → 当前文档。任何降级或无法验证的结论都要明说。

## Core 与 instrument plugin 边界

Core 文档负责通用仪器抽象、CLI、run plan、artifact、安全、capability、插件 API、通用配置、session 与 recovery。

instrument plugin 文档负责具体型号、厂商命令、私有参数、quirks、型号 profile/capability、限制和实机验证状态。

Core 可以展示短小的支持摘要，但不能长期维护 DSG830、RTM2032、DG4202 等型号的完整能力矩阵。需要精确答案时，引导读者查询 descriptor 或插件仓库。

## 生命周期标签

- Current：已发布且由当前实现、测试和文档共同支持。
- Experimental：已经可用但稳定性或兼容性未承诺，页面显式标记。
- Proposed：尚未实现或未发布，只能出现在 RFC、issue 或 roadmap。
- Historical：旧实现、里程碑或证据记录，移入 archive 或对应 Git tag。

用户 Reference 对不可用能力只需说明 `unavailable` 或 `unsupported`，不复制开发过程。

## README policy

根 README 只承担项目 landing page：项目名/tagline、主要入口、用途与价值、核心能力、无硬件 Quickstart、简短支持摘要、安全提醒、Contributing、License 和 Acknowledgements。

README 不维护 RFC 编号、milestone、A1/A2/A4 阶段、descriptor 内部合同、rollback 实现细节、型号级 capability/profile 或易变化的参数限制。需要提及时用一两句摘要链接到权威页面。

## 目标目录

目标形态如下，但只有明确读者任务存在时才创建页面：

```text
docs/
  index.md
  getting-started/
  tutorials/
  how-to/
  reference/
    plugins/
  concepts/
  development/
  rfcs/
  archive/
```

`docs/project/` 是可以逐步消除的无语义中间层。先稳定 taxonomy、导航、页面职责和事实源，再决定 MkDocs Material、GitHub Pages、搜索、版本化文档和生成式 Reference。

## 必须能走通的用户旅程

1. README → Quickstart → 获得离线或 fake 的可见结果。
2. Installation → Configure Bench → `doctor` → `run verify`。
3. Tutorial/How-to → `run plan` → artifacts → report。
4. error message → Troubleshooting → Error Reference。
5. search/navigation → 精确 Reference。
6. Concepts → 理解设计与取舍。
7. Development → Driver/Plugin guide → API Reference。
8. RFC / CHANGELOG → 查询未来设计或演进历史。

任一关键旅程明显断裂时，优先修入口和导航，不先润色深层页面。
