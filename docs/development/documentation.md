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
python scripts/generate_docs.py --check
python .agents/skills/wavebench-docs/scripts/audit_docs.py
git diff --check
```

`generate_docs.py --check` 会验证受管理的 Reference 没有落后于源码；默认运行 `python scripts/generate_docs.py` 会重新生成它们。文档 audit 检查相对链接、锚点、导航完整性、重复标题、明显版本漂移和公开文档中的疑似私有资源。它不判断 Tutorial 是否应拆分、用户旅程是否顺畅或内容应放入 Guide、Reference 还是 Concept；这些由 scoped 文档 review 判断。

## 代码改动的文档影响

CLI、schema、配置、artifact、capability、安全和 plugin API 发生变化时，先找到对应 canonical Reference。可以先运行：

```bash
python scripts/docs_impact.py --base <base-revision> --head HEAD
```

它只列出应由 reviewer 判断的候选页面，不会替代文档 review，也不会扫描无关领域。确认实际受影响页面后，再对这些页面和直接导航入口运行 scoped audit。

| 检查 | CI 状态 | 目的 |
| --- | --- | --- |
| generated Reference drift | 阻断 | 源码和 checked-in 机器事实必须同步。 |
| 文档 audit 的 error | 阻断 | 防止断链、缺失目标和明显私有资源。 |
| 文档 audit 的 warning | 提示 | 交给 scoped review 判断历史页、页面长度和结构问题。 |
| `docs_impact.py` | 提示 | 只给出本次代码 diff 的 canonical 文档候选。 |

推荐顺序为：代码 diff → impact 候选 → 更新 canonical 页面 → 中文表达检查（如需要）→ 机械验证 → scoped Agent review。

## 站点预览与发布

```bash
python -m pip install -e ".[docs]"
mkdocs serve
mkdocs build --strict
```

`mkdocs.yml` 只映射当前的开始使用、教程、How-to、Reference、Concepts、开发和 RFC 入口；archive 与旧入口不在主导航中。GitHub Actions 会在文档或潜在事实源变化时执行严格构建。

当前站点采用 default branch 的 latest 视图。需要稳定版时，建议从发布 tag 或 release branch 构建独立站点版本；在尚无实际发布需求前，不维护历史文档副本。GitHub Pages 发布应在仓库 Pages 策略、域名和权限明确后，由单独的 deploy workflow 处理；本仓库当前只验证构建，不自动发布。
