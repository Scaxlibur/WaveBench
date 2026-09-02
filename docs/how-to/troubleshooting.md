# run plan 排错

本页用于从 `run check`、`doctor`、`run verify` 或 `run plan` 的失败信息找到下一步。先区分错误发生在离线检查、连接读取还是设备写入阶段；不要用重试或 `on_failure = "continue"` 掩盖未知的硬件状态。

## `kind` 不受支持

运行：

```bash
python -m wavebench run schema
```

将 plan 中的 `kind` 与当前输出逐字比较。模板、旧文档或另一个 Git tag 中出现的 step 不一定在当前安装版本中可用。

## 字段名错误或缺少必填字段

同样从 `run schema` 查找该 `kind` 的 `required` 和 `optional` 字段。字段拼写正确也不代表输入有效；数值范围、跨字段约束和 capability 仍由当前 parser 与 descriptor 检查。

## `run check` 未通过安全上限或引用检查

检查 plan 的 `[safety]`、`[restore]`、基线／resume 路径和配置中的安全限制。不要为了通过检查而删除安全门、降低保护或把真实硬件资源写进公开示例。

## `doctor` 或 `run verify` 无法通过

这两条命令会查询真实设备。依次检查：

1. `wavebench.toml` 中的 resource 与当前连接是否一致。
2. 仪器身份是否与所选 driver 或 plugin 相符。
3. access policy 是否允许该 plan 需要的读取或写入。
4. 接线、输入阻抗、输出状态和仪器保护是否符合 plan 前提。

不要因为一次身份查询失败就改用未知资源或跳过预检。

## `run plan` 已开始后失败

保留命令报告的 run 目录，不要立即删除。先查看：

- `run.json` 的 `status`、`error`、`restore` 和 `provenance`（存在时）；
- `summary.csv` 中失败的 step；
- `steps/` 中同一序号的 JSON 记录；
- 仪器当前读回状态和 plan 中的安全门／restore 条款。

随后阅读[运行产物 Reference](../reference/artifacts.md)和[错误处理和日志策略](../project/reference/WaveBench_错误处理和日志策略.md)。需要继续硬件操作前，先确认输出已处于预期安全状态。
