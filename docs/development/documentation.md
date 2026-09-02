# 文档工作流

WaveBench 文档采用 docs-as-code：文档和代码一起版本控制、review 和验证。页面职责、事实来源、Core/plugin 边界、状态和生命周期以仓库内的 `wavebench-docs` 文档宪法为准。

## 工作模式

| 模式 | 适用场景 |
| --- | --- |
| `audit` | 建立 inventory、识别旅程问题与重复事实。 |
| `migrate` | 按已接受的迁移图小步移动、拆分或归档页面。 |
| `write` | 新增或实质改写一页，先确定读者目标和 canonical source。 |
| `review` | 只检查本次 diff、直接导航和关联事实源。 |

中文页面在结构、事实和边界确定后，再应用 `tech-doc-style-chinese`。该写作层不负责决定页面类别或信息架构。

## 机械检查

```bash
python .agents/skills/wavebench-docs/scripts/audit_docs.py
git diff --check
```

脚本检查相对链接、锚点、导航完整性、重复标题、明显版本漂移和公开文档中的疑似私有资源。它不判断 Tutorial 是否应拆分、用户旅程是否顺畅或内容应放入 Guide、Reference 还是 Concept；这些由 scoped 文档 review 判断。

## 代码改动的文档影响

CLI、schema、配置、artifact、capability、安全和 plugin API 发生变化时，先找到对应 canonical Reference。不要仅更新 README 或复制一份新参数表；如需中文改写，最后再进行表达层检查。
