# RFC 索引

RFC 记录设计问题、约束、兼容性和验收门槛。它们不是当前用户行为、CLI 参数或型号支持状态的事实源；这些内容应分别查阅 [Reference](../reference/cli.md)、[run schema](../reference/run-schema.md)、安装插件的 descriptor 和[更新日志](../../CHANGELOG.md)。

## 状态含义

| 状态 | 含义 |
| --- | --- |
| `Draft` | 提案仍可修改，不能据此宣称接口已经提供。 |
| `Accepted` | 合同已冻结，可按实施顺序开发；不等于已发布。 |
| `Implemented（未发布）` | 开发线实现和离线验收完成，但插件不能据此提高发布依赖或声明生产支持。 |
| `Implemented` | 已随正式版本发布，稳定用法应已同步到 Reference。 |
| `Superseded` | 已由更严格或后续合同取代，保留历史原因。 |

## 已接受的通用合同

- [transport 重放与 session 健康](../project/rfcs/WaveBench_transport重放与session健康RFC.md)：`Accepted`；记录共享 session、重放与恢复合同。实现状态仍以正式 release 和当前 Reference 为准。
- [scope 通用扩展接口](../project/rfcs/WaveBench_scope通用扩展接口RFC.md)：`Accepted R1.3`；定义可选 scope 扩展的公共合同。
- [scope 可移植性 RFC 组合说明](../project/rfcs/WaveBench_scope可移植性RFC组合说明.md)：`Accepted R1`；说明编号系列的裁决和相互关系。
- [Source V2 能力、状态与复合输出安全](../project/rfcs/WaveBench_source能力状态与复合输出安全RFC.md)：`Accepted R7`；文内的 R8 候选仍是未来设计，不增加当前 capability。

## 已实现但未发布的记录

下面的记录描述开发线合同或离线验收，不能作为已发布用户能力或插件生产支持的依据。

- [RFC-0002：通道输入状态 V2](../project/rfcs/WaveBench_scope可移植性RFC-0002_通道输入状态.md)
- [RFC-0004：数字通道状态 V2](../project/rfcs/WaveBench_scope可移植性RFC-0004_数字通道状态.md)
- [RFC-0005：可组合状态快照 V2](../project/rfcs/WaveBench_scope可移植性RFC-0005_可组合状态快照.md)
- [RFC-0006：采集状态与平均采集 V2](../project/rfcs/WaveBench_scope可移植性RFC-0006_采集状态与平均采集.md)
- [RFC-0007：统计、FFT 与光标读取 V2](../project/rfcs/WaveBench_scope可移植性RFC-0007_统计FFT与光标读取.md)
- [RFC-0008：有界波形传输裁决](../project/rfcs/WaveBench_scope可移植性RFC-0008_有界波形传输裁决.md)
- [RFC-0009：SINGLE 模式终态 STOP 完成证明](../project/rfcs/WaveBench_scope可移植性RFC-0009_SINGLE模式终态STOP证明.md)
- [标准波形有界二进制传输](../project/rfcs/WaveBench_标准波形有界二进制传输RFC.md)

## 已被取代的记录

- [RFC-0001：消费型文本查询与错误队列](../project/rfcs/WaveBench_scope可移植性RFC-0001_消费型文本查询.md)
- [RFC-0003：截图 framing 与菜单合同](../project/rfcs/WaveBench_scope可移植性RFC-0003_截图framing与菜单.md)

被取代的 RFC 仍保留问题背景，但不能重新引入其中已否决的 API 或安全假设。

## 历史索引

[旧 RFC 索引](../project/rfcs/legacy-index.md)保留迁移前的逐项摘要和旧链接。它是历史导览，不替代本页的状态边界。
