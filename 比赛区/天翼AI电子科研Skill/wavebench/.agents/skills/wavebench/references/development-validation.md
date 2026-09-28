# 开发、验证与交接

> 加载时机：涉及 WaveBench 代码、CLI、TUI、报告、技能维护、测试或结果交接时加载。
> 本文件不依赖其他 reference。

## 代码改动

1. 先读取实现、公开契约和聚焦测试。
2. 只修改满足需求的最小范围，避免顺手重构。
3. 为行为变化补充聚焦测试；保持公开 CLI、TUI、报告和发行文案的既有语言约定。
4. 不把本地配置、真实资源、私有协作路径或内部交接规则写入公开文件。
5. 推送、打标签、发布版本和覆盖 `wavebench.toml` 须有对应授权，不能从代码修改或测试通过推定。

## 验证分层

按风险选择最窄的验证集合：

以下命令以 POSIX 虚拟环境路径为例；原生 Windows 将 `.venv/bin/python` 替换为
`.venv\Scripts\python.exe`，将 `.venv/bin/ruff` 替换为 `.venv\Scripts\ruff.exe`。

```bash
.venv/bin/python -m pytest -q tests/<focused-test>.py
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
git diff --check
```

- 局部行为修改先运行聚焦测试和相关静态检查；跨模块、公共安全合同或合并评估再运行全量测试。
- 仅修改文档或 Skill 时运行相关文案、链接或 Skill 校验，不自动运行全量 Python 测试；CI 仍执行仓库既有门禁。
- 修改 plan 示例或校验语义时增加离线 `run check`；仅解释 plan 不自动执行实时步骤。
- 插件 metadata、打包、安装或发现行为改变时，按受影响合同选择包检查、安装 dry-run 和加载检查；插件生产开发以插件仓 Skill 为主，不能因为涉及插件一词就安装或加载第三方代码。
- 实际涉及真实仪器写入时，必须保留有边界的验收产物和写后状态回读；fake 测试不需要实机验收。

技能维护增加：

```bash
.venv/bin/agentskills validate .agents/skills/wavebench
.venv/bin/agentskills read-properties .agents/skills/wavebench
.venv/bin/agentskills to-prompt .agents/skills/wavebench
.venv/bin/python .agents/skills/wavebench/scripts/validate_skill.py
```

`agentskills` 只校验 Agent Skills 基础格式；入口引用、预算、敏感内容和项目特有约束由本技能的校验脚本负责。

## 文档规则

涉及 WaveBench 文档体系审计、信息架构迁移、新增或重写页面以及文档 diff 评审时，使用仓库内
`wavebench-docs` Skill；普通代码任务中只更新一两处直接相关说明时，不自动扩大为全仓文档审计。
先确定页面职责和事实源，再处理中文表达。

中文 Markdown 使用 `tech-doc-style-chinese` 规则：正文使用直角引号「」，避免第二人称和宣传腔，中文与英文或数字之间留空格；代码、路径、URL、API 路径和配置键保持原样。修改后运行：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/tech-doc-style-chinese/scripts/lint_copy_rules.py" \
  --term-allowlist docs/tech-doc-term-allowlist.json <changed-markdown-paths>
```

## 交接格式

先给结论，以下字段只报告与本次任务相关的项目；实际接触硬件时不得省略最终状态和未恢复项：

- 检查或改动的范围；
- 精确的验证命令和结果；
- run、capture、报告或日志产物路径；
- 最终源、示波器、电源和 DMM 状态；
- 未恢复字段、失败、跳过和部分产物；
- 能力缺口和剩余风险；
- 是否改动跟踪文件、本地配置、虚拟环境或真实仪器。

不要以笼统的「通过」掩盖失败预检、跳过测试、恢复异常或证据不完整。

## 外部资料

外部检索的适用条件与隐私边界以 Skill 入口的 `External research` 为准，不在开发流程增加另一套联网门槛。
