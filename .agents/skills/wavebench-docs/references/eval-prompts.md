# 触发与模式回归用例

> 加载时机：维护 `wavebench-docs` 的 description、隐式触发边界或 reference 路由时加载。

## 应触发

| 请求 | Mode | 预期加载 |
| --- | --- | --- |
| 「审计 WaveBench 的 README、docs 与事实源，给迁移表」 | `audit` | 入口、`audit.md`、`information-architecture.md` |
| 「按已批准的文档审计拆分 run plan 指南」 | `migrate` | 入口、`migrate.md`、`information-architecture.md` |
| 「为新的 run step 编写 WaveBench Reference」 | `write` | 入口、`write.md`、`information-architecture.md` |
| 「评审这个只改文档的 PR」 | `review` | 入口、`review.md`，按需加载信息架构 |

## 不应触发

| 请求 | 正确处理 |
| --- | --- |
| 「执行这个 WaveBench run plan」 | 使用 `wavebench` 的 run/safety 工作流 |
| 「采集示波器 CH1 并生成报告」 | 使用 `wavebench`，先过硬件写入门禁 |
| 「README 里怎么安装？」 | 直接读取并回答，不进入文档开发 workflow |
| 「修复 config parser 的 bug，顺手更新一句报错说明」 | 普通 WaveBench 代码开发；附带的一行说明不触发完整文档工作流 |
| 「把这段中文写自然一点」 | 使用 `tech-doc-style-chinese`，除非明确是 WaveBench 文档开发 |
| 「解释 Diátaxis」 | 直接解释方法，不审计 WaveBench 仓库 |
| 「解释傅里叶变换」 | 普通知识任务 |

## 行为检查

用全新上下文逐条测试，确认：

1. 普通使用和只读问答不会加载本 Skill；
2. 文档开发能自动选择正确 mode；
3. `review` 不默认扩大为全仓 audit；
4. `write` 在结构和事实确定前不会先做措辞润色；
5. 文档任务不会自行连接仪器或执行实时 plan；
6. 中文写作层按需交给 `tech-doc-style-chinese`，且不套用无关项目覆盖规则。
