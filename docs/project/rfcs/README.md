# RFC 索引

本目录保存尚在评审或已经形成决策的核心接口提案。RFC 记录问题、约束、兼容性和验收门槛，不替代已实现功能的参考文档。

RFC 使用以下状态：

- `Draft`：提案仍可修改，不得据此宣称接口已经提供；
- `Accepted`：关键合同已经冻结，可以按实施顺序修改代码；
- `Implemented`：合同已经进入正式版本，稳定用法已同步到 `reference/`；
- `Superseded`：由后续 RFC 取代，并保留替代关系。

## 当前 RFC

- [Source V2 能力、状态与复合输出安全 RFC](WaveBench_source能力状态与复合输出安全RFC.md)：
  `Accepted R6`，核心 `0.8.24` 开发线已实现 P0、M1–M4、M4.5、C1、M5-A 至 M5-D 与 C2；
  M6-A 的 Harmonic、内部 AM、WIDTH Pulse 子项已实现，其余高级配置与插件发布门仍按里程碑逐阶段实现。
- [transport 重放与 session 健康 RFC](WaveBench_transport重放与session健康RFC.md)：定义查询重放、结构化传输错误、共享 session 健康状态、恢复授权和版本迁移。
- [scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)：`Accepted R1.3`，定义 operation context、binary budget、截图、采集控制、trace、错误策略及恢复验证合同。公共合同已进入核心 `0.8.23` 开发线；插件按 capability 单独 opt-in。
- [scope 通用扩展接口 RFC：R1.3 Acceptance Addendum A1](WaveBench_scope通用扩展接口RFC-R1.3-acceptance-addendum.md)：记录公共 capability 注册采用的 P0/P1 验收门和离线完成证据。
- [scope 通用扩展接口 RFC：核心实施说明](WaveBench_scope通用扩展接口RFC_核心实施说明.md)：记录 backend、公共 Service/CLI、artifact、版本门、旧 capture 分流和插件迁移边界。
