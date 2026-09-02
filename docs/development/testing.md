# 测试 WaveBench

WaveBench 的默认验证必须离线、可重复且不依赖真实仪器。driver 和 service 测试使用 fake transport；真实实验台验收属于单独授权的插件或开发流程。

## 本地检查

```bash
python -m ruff check .
python -m pytest -q
python .agents/skills/wavebench-docs/scripts/audit_docs.py --quiet-warnings
git diff --check
```

修改 CLI、run schema、配置、artifact、capability、安全语义或插件 API 时，补充对应的聚焦测试，并在文档 review 中核对 canonical Reference。不要用成功的实机记录替代可重复的离线测试。
