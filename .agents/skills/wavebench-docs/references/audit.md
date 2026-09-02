# Audit 模式

> 加载时机：评估整个文档系统、一个目录或一组相关页面，且当前阶段不进行广泛迁移时加载。

## 范围

全仓 audit 至少盘点 `README.md`、`docs/`、`plans/README.md`、`CHANGELOG.md`、相关 CLI/schema/config/artifact/capability/safety 源码入口，以及 `.agents/skills/`。局部 audit 只扩展到修改页的导航入口、直接相关页面和 canonical source。

先运行：

```bash
python .agents/skills/wavebench-docs/scripts/audit_docs.py
```

机械结果只是审计输入。不要把「链接没坏」写成「文档结构合理」。

## Inventory

每篇页面只给一个主要动作：

| File | Type | Audience | Canonical | Problems | Action |
| --- | --- | --- | --- | --- | --- |

`Canonical` 说明页面自己是否为该事实的权威来源；若不是，写出实际来源。`Problems` 优先记录职责混合、重复事实、过时风险、旅程断点、Core/plugin 越界和 current/RFC 混杂，不以措辞偏好充数。

动作含义：

- `KEEP`：职责清楚，事实来源稳定。
- `REWRITE`：路径与主职责可保留，但内容需要按单一读者结果重建。
- `SPLIT`：一个页面承担多个独立目标。
- `MERGE`：与另一页面重复维护同一职责或事实。
- `MOVE`：内容职责合理，目录语义错误。
- `GENERATE`：机器事实可由稳定模型、schema 或 registry 产生。
- `ARCHIVE`：历史仍有价值，但不能继续作为当前说明。
- `DELETE`：无独立价值且由 canonical 页面完整替代。

## 系统级输出

除 inventory 外，列出并排序：

- 最严重的 10 个系统问题；
- 八条用户旅程的断点；
- 重复维护的易变事实；
- 孤儿页面和失效导航；
- 超长 mixed-purpose 页面；
- Core/plugin 边界违规；
- Current、Experimental、RFC 和历史记录的混杂；
- 目标信息架构、事实源表和小步迁移顺序。

结论必须引用 `file:line`、符号名、命令输出或 tag。若只是推断，要明确标注，不把旧文档互相引用当作事实核验。

## 当前基线

首轮仓库审计见 [WaveBench 文档系统审计与迁移提案](../../../../docs/project/design/WaveBench_文档系统审计与迁移提案.md)。后续 audit 应重新扫描当前树，不得把这份日期快照当永久事实源。
