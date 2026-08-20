# RFC 索引

本目录保存尚在评审或已经形成决策的核心接口提案。RFC 记录问题、约束、兼容性和验收门槛，不替代已实现功能的参考文档。

RFC 使用以下状态：

- `Draft`：提案仍可修改，不得据此宣称接口已经提供；
- `Accepted`：关键合同已经冻结，可以按实施顺序修改代码；
- `Implemented`：合同已经进入正式版本，稳定用法已同步到 `reference/`；
- `Superseded`：由后续 RFC 取代，并保留替代关系。

## 当前 RFC

- [transport 重放与 session 健康 RFC](WaveBench_transport重放与session健康RFC.md)：定义查询重放、结构化传输错误、共享 session 健康状态、恢复授权和版本迁移。
- [scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)：`Draft R1.3`，定义 operation context、binary budget、截图、采集控制、trace、错误策略及恢复验证合同。本分支仅实施默认关闭的内部基础设施，不表示 capability 已公开注册。
- [scope 通用扩展接口 RFC：R1.3 Acceptance Addendum A1](WaveBench_scope通用扩展接口RFC-R1.3-acceptance-addendum.md)：列出公共 capability 注册和插件迁移前必须满足的 P0/P1 验收门。
- [scope 通用扩展接口 RFC：核心实施说明](WaveBench_scope通用扩展接口RFC_核心实施说明.md)：记录默认关闭的核心实现、歧义裁决和公共注册前的剩余验收项。
