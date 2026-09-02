# Review 模式

> 加载时机：评审文档 PR、提交或工作区 diff 时加载。默认不是全仓 audit。

## Review scope

优先检查：

1. 本次修改的页面；
2. 它们直接依赖的 canonical Reference 或源码；
3. 导航入口与入链页面；
4. 被引用的命令、配置、plan、schema 和 artifact；
5. 与改动声明直接相关的测试或 descriptor。

只有发现系统性重复、迁移跨目录或用户明确要求时，才升级为全仓 audit。

## Review order

- 页面 type、audience 和 reader outcome 是否一致；
- 当前行为、Experimental、RFC 与历史是否清楚分离；
- 易变事实是否回到唯一 canonical source；
- Core/plugin 边界是否正确；
- 链接、导航、文件路径和锚点是否有效；
- 命令、参数、schema、capability、artifact 和示例是否与实现一致；
- 中文页面最后使用 `tech-doc-style-chinese` 检查表达层。

先读 diff，确定本次修改的 Markdown，再把这些路径显式传给机械 audit。例如：

```bash
python .agents/skills/wavebench-docs/scripts/audit_docs.py README.md docs/index.md
git diff -- README.md docs plans .agents/skills
git diff --check
```

脚本仍以完整文档图解析链接和入链，但只报告指定文件的问题。发现跨目录迁移或系统性重复时，再不带路径运行全量 audit。

## Findings

按会导致错误操作、错误事实、断链或长期漂移的风险排序。每条 finding 给出紧凑的 `file:line`、影响、事实依据和最小修正方向。

不要把个人措辞偏好、未改动页面的旧问题或完整仓库愿望清单混进 PR review。没有实质问题时明确说明，并列出尚未验证的命令、外链或硬件行为。
