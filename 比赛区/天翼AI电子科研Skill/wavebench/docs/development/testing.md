# 测试 WaveBench

WaveBench 的默认验证必须离线、可重复且不依赖真实仪器。driver 和 service 测试使用 fake transport；真实实验台验收属于单独授权的插件或开发流程。

## 本地检查

按改动影响选择验证集合：

- 局部行为修改：先运行 `python -m pytest -q tests/<focused-test>.py`、相关 Ruff 检查和 `git diff --check`。
- 文档或 Skill 修改：检查变更页面、直接导航、文案与 Skill 格式；不自动运行全量 Python 测试。涉及生成来源或文档工具时增加生成漂移与对应工具测试。
- 跨模块行为、公共安全合同或合并评估：运行下列集成检查；涉及文档站点的变更再做生成 Reference 检查和 `mkdocs build --strict`。

```bash
python -m ruff check .
python -m pytest -q
python .agents/skills/wavebench-docs/scripts/audit_docs.py --quiet-warnings
git diff --check
```

修改 CLI、run schema、配置、artifact、capability、安全语义或插件 API 时，补充对应的聚焦测试，并在文档 review 中核对 canonical Reference。不要用成功的实机记录替代可重复的离线测试。

`.github/workflows/ci.yml` 和 `docs.yml` 定义远端合并检查；本地单个平台通过不能替代 Python／操作系统矩阵。已通过的检查只在新改动、失败或未解决风险需要时重跑，不能把 CI 清单当作每轮编辑的固定流程。
