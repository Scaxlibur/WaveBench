# 贡献 WaveBench

WaveBench 的代码、文档、schema 和测试在同一仓库中维护。提交应保持范围小、可验证，并明确是否改变 CLI、配置、capability、artifact、安全语义或插件 API。

## 开始前

1. 阅读相关 Reference、Concept、RFC 和测试，而不是从旧文档推断当前行为。
2. 对硬件相关改动先确认 access policy、capability、恢复边界和测试策略。
3. 不在普通测试或文档验证中连接真实仪器、修改 `wavebench.toml` 或提交实验数据。

## 提交与合并前

按[测试说明](testing.md)选择检查：局部修改先做聚焦验证，文档或 Skill 修改检查相关内容，跨模块或合并评估执行集成检查。远端 CI 仍按仓库 workflow 执行，不要求每个编辑步骤重跑全量测试。

新增或修改用户可见行为时，更新唯一 canonical Reference，并用[文档工作流](documentation.md)进行 scoped review。插件专用流程见[插件开发](plugin-development.md)。

尚未发布的示波器观察入口及执行验收合同见[开发线示波器观察](scope-observation.md)。
