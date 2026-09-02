# Migrate 模式

> 加载时机：已有被接受的 audit 或明确迁移决策，需要移动、拆分、合并、归档或生成文档时加载。

## 迁移门槛

迁移前确认：

- 页面主类型、受众和 reader outcome 已确定；
- 每项易变事实已有 canonical source；
- 旧页面的 `KEEP/REWRITE/SPLIT/MERGE/MOVE/GENERATE/ARCHIVE/DELETE` 已决定；
- 入口、入链、出链和外部可见 URL 已盘点；
- 本轮切片有可观察的完成标准。

没有这些条件时退回 audit，不靠边移动文件边猜信息架构。

## 小步顺序

1. 先建立目标页或生成器，保留旧入口。
2. 搬运一类职责，核对事实与技术内容没有丢失。
3. 更新直接导航、交叉链接和代码示例引用。
4. 运行文档 audit、相关 `--help`/schema 检查和聚焦测试。
5. 再将旧页改为短跳转、归档或删除。

一轮优先处理 2～4 个能验证设计的页面。通常顺序为 README、docs index、Quickstart、最大 mixed-purpose guide；不要一次性重写整个 `docs/`。

## 链接与历史

- GitHub Markdown 没有真正重定向；移动高入链页面时，优先保留短的旧路径说明，直到外部入口已迁移。
- RFC 的不可变决策与实现历史分开。不要为了目录整齐改写已经接受的历史裁决含义。
- `CHANGELOG.md` 只记录正式 tag；迁移说明不能把开发分支写成发布版本。
- 归档页面顶部标明历史范围、替代页面和不可作为当前事实源的边界。

## 生成式 Reference

先选择稳定、离线、确定性的源。生成结果必须记录生成命令、源文件或 schema 标识，并由 CI 检查漂移。不要在首轮同时发明生成框架、文档站和版本系统；先从 `run schema` 或 CLI help 的一个窄 Reference 证明流程。

## 验证

至少执行：

```bash
python .agents/skills/wavebench-docs/scripts/audit_docs.py
git diff --check
```

再按改动增加离线 CLI、schema、示例 plan 或聚焦测试。除非另有明确授权，迁移文档不连接真实仪器，不覆盖 `wavebench.toml`，不生成或提交真实实验数据。
