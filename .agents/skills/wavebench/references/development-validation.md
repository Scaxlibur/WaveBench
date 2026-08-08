# 开发、验证与交接

> 加载时机：涉及 WaveBench 代码、CLI、TUI、报告、技能维护、测试或结果交接时加载。
> 本文件不依赖其他 reference。

## 代码改动

1. 先读取实现、公开契约和聚焦测试。
2. 只修改满足需求的最小范围，避免顺手重构。
3. 为行为变化补充聚焦测试；保持公开 CLI、TUI、报告和发行文案的既有语言约定。
4. 不把本地配置、真实资源、私有协作路径或内部交接规则写入公开文件。
5. 不自动推送、打标签、发布版本或覆盖 `wavebench.toml`。

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

涉及 run plan 时增加 `run check`；涉及插件时增加包检查、安装 dry-run、插件自身测试和 `plugin doctor --load`；涉及真实仪器时必须增加有边界的验收产物和写后状态回读。

技能维护增加：

```bash
.venv/bin/agentskills validate .agents/skills/wavebench
.venv/bin/agentskills read-properties .agents/skills/wavebench
.venv/bin/agentskills to-prompt .agents/skills/wavebench
.venv/bin/python .agents/skills/wavebench/scripts/validate_skill.py
```

`agentskills` 只校验 Agent Skills 基础格式；入口引用、预算、敏感内容和项目特有约束由本技能的校验脚本负责。

## 文档规则

中文 Markdown 使用 `tech-doc-style-chinese` 规则：正文使用直角引号「」，避免第二人称和宣传腔，中文与英文或数字之间留空格；代码、路径、URL、API 路径和配置键保持原样。修改后运行：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/tech-doc-style-chinese/scripts/lint_copy_rules.py" \
  .agents/skills/wavebench
```

## 交接格式

先给结论，再列：

- 检查或改动的范围；
- 精确的验证命令和结果；
- run、capture、报告或日志产物路径；
- 最终源、示波器、电源和 DMM 状态；
- 未恢复字段、失败、跳过和部分产物；
- 能力缺口和剩余风险；
- 是否改动跟踪文件、本地配置、虚拟环境或真实仪器。

不要以笼统的「通过」掩盖失败预检、跳过测试、恢复异常或证据不完整。

## 外部资料

只有用户明确要求最新厂商资料、标准或外部建议时才使用网络搜索。优先官方来源，记录 URL 和访问日期；不发送本地配置、设备序列号、资源地址或实验数据。搜索 MCP 不可用时说明降级路径，不伪造工具结果。
