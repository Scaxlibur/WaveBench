# RFC 索引

本目录保存尚在评审或已经形成决策的核心接口提案。RFC 记录问题、约束、兼容性和验收门槛，不替代已实现功能的参考文档。

RFC 使用以下状态：

- `Draft`：提案仍可修改，不得据此宣称接口已经提供；
- `Accepted`：关键合同已经冻结，可以按实施顺序修改代码；
- `Implemented（未发布）`：开发线实现和离线验收已经完成，但尚未进入正式发行版，插件不得据此提高版本下限；
- `Implemented`：合同已经进入正式版本，稳定用法已同步到 `reference/`；
- `Superseded`：由后续 RFC 取代，并保留替代关系。

## Scope 可移植性编号系列

[scope 可移植性 RFC-0001～RFC-0008 组合说明](WaveBench_scope可移植性RFC组合说明.md)
记录八份提案的规范优先级、共同 unknown/unavailable 语义、追加式兼容合同、版本组合和
实施顺序。编号与外部插件提出的问题一一对应，但核心裁决不照搬已被否决的早期 API。

| RFC | 状态 | 核心裁决 |
| --- | --- | --- |
| [RFC-0001：消费型文本查询与错误队列](WaveBench_scope可移植性RFC-0001_消费型文本查询.md) | `Superseded R1` | 使用 `ReplayPolicy.NO_REPLAY` 与 `scope.error_drain_v1`，不新增 `query_text_once()` |
| [RFC-0002：通道输入状态 V2](WaveBench_scope可移植性RFC-0002_通道输入状态.md) | `Implemented R1（未发布）` | 追加 coupling/termination 分离模型和 V2 安全判断，不修改旧 coupling 安全门 |
| [RFC-0003：截图 framing 与菜单合同](WaveBench_scope可移植性RFC-0003_截图framing与菜单.md) | `Superseded R1` | 使用 `query_binary()`、screenshot profile 和 `scope.screenshot_v2` |
| [RFC-0004：数字通道状态 V2](WaveBench_scope可移植性RFC-0004_数字通道状态.md) | `Implemented R1（未发布）` | 追加字段可缺失且保留作用域的 digital status；waveform 另行取证 |
| [RFC-0005：可组合状态快照 V2](WaveBench_scope可移植性RFC-0005_可组合状态快照.md) | `Implemented R1（未发布）` | M3b 已完成核心模型、profile、Protocol、factory gate 与 Service；不改旧 CLI/artifact |
| [RFC-0006：采集状态与平均采集 V2](WaveBench_scope可移植性RFC-0006_采集状态与平均采集.md) | `Implemented R1（未发布；0006a）；Accepted R1（0006b-0 内部前置）` | M4 已完成 status V2；M6 已冻结通用 bounded transaction，average public surface 仍为 Draft |
| [RFC-0007：统计、FFT 与光标读取 V2](WaveBench_scope可移植性RFC-0007_统计FFT与光标读取.md) | `Implemented R1（未发布；0007a/0007b/0007c）` | M5a/M5b/M5c 已完成 statistics/FFT/cursor 的 profile、零 I/O gate 与 Service；不改旧 CLI/artifact |
| [RFC-0008：有界波形传输裁决](WaveBench_scope可移植性RFC-0008_有界波形传输裁决.md) | `Implemented R1（未发布）` | 采用 descriptor profile、`query_binary()`、四维预算和核心恢复编排 |

本系列中的 `Draft` 不表示接口已经存在，也不授权开始代码、插件 capability 或硬件工作。`Superseded`
表示原提案入口已由更严格合同取代，不表示原始安全问题可以忽略。

## 基础与专题 RFC

- [Source V2 能力、状态与复合输出安全 RFC](WaveBench_source能力状态与复合输出安全RFC.md)：
  `Accepted R6`，核心 `0.8.24` 开发线已实现 P0、M1–M4、M4.5、C1、M5-A 至 M5-D 与 C2；
  M6-A、M6-B 与 M6-C 已完成核心离线合同；真实插件 opt-in、实机验收与发布门仍按里程碑逐阶段实施。
- [transport 重放与 session 健康 RFC](WaveBench_transport重放与session健康RFC.md)：定义查询重放、结构化传输错误、共享 session 健康状态、恢复授权和版本迁移。
- [scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)：`Accepted R1.3`，定义 operation context、binary budget、截图、采集控制、trace、错误策略及恢复验证合同。公共合同已进入核心 `0.8.23` 开发线；插件按 capability 单独 opt-in。
- [scope 通用扩展接口 RFC：R1.3 Acceptance Addendum A1](WaveBench_scope通用扩展接口RFC-R1.3-acceptance-addendum.md)：记录公共 capability 注册采用的 P0/P1 验收门和离线完成证据。
- [scope 通用扩展接口 RFC：核心实施说明](WaveBench_scope通用扩展接口RFC_核心实施说明.md)：记录 backend、公共 Service/CLI、artifact、版本门、旧 capture 分流和插件迁移边界。
- [标准波形有界二进制传输 RFC](WaveBench_标准波形有界二进制传输RFC.md)：`Implemented R1（未发布）`，标准 `scope.fetch_waveform` 和 `scope.capture*` 可在 descriptor 显式 opt-in 时复用 R1.3 bounded binary context；外部插件 conformance 与实机验收仍单独进行。
