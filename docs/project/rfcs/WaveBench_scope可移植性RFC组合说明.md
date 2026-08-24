# WaveBench scope 可移植性 RFC-0001～RFC-0008 组合说明

> 状态：`Accepted R1`
> 核心基线：WaveBench `0.8.24` 开发线
> 范围：核心接口裁决、兼容边界与实施顺序
> 证据来源：WaveBench Instrument Plugins 中的 MSO8000 提案
> 相关规范：[transport 重放与 session 健康 RFC](WaveBench_transport重放与session健康RFC.md)、[scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)、[标准波形有界二进制传输 RFC](WaveBench_标准波形有界二进制传输RFC.md)

## 摘要

本系列把外部插件提出的 RFC-0001～RFC-0008 转换为 WaveBench 核心侧的厂商无关裁决。
插件 RFC 是问题和设备证据的来源，不是核心公共接口的事实源。核心只有在对应文档进入
`Accepted`、实现通过离线验收并随正式版本发布后，外部插件才可以依赖新增合同。

八份提案不能按原始草案逐项照搬。RFC-0001、RFC-0003 和 RFC-0008 的早期 transport
入口已经被更严格的核心合同取代；RFC-0002、RFC-0004、RFC-0005、RFC-0006 和
RFC-0007 仍需要追加式 V2 模型。被取代不表示问题不存在，而是表示不能再实现已否决的
平行 API。

M0 只冻结本组合说明和 legacy 黄金基线。M1 及后续里程碑可以在本分支按本文顺序修改核心
代码；仍为 `Draft` 的单项 RFC 只授权其对应里程碑中的追加式实现，不授权插件提高版本下限或
连接真实仪器。

## 规范优先级

出现歧义时按以下顺序解释：

1. 已接受的 transport、scope R1.3 和标准波形有界二进制传输核心 RFC；
2. 本系列中已经进入 `Accepted` 或 `Implemented` 的编号 RFC；
3. 本系列中仍为 `Draft` 的候选模型；
4. 外部插件仓库中的设备提案和厂商证据。

`Draft` 中的类型名、capability 名和字段仍可调整。插件不得只根据候选代码块调用尚未发布的
方法。

## RFC 状态

| RFC | 核心裁决 | 当前状态 | 核心开发范围 |
| --- | --- | --- | --- |
| [RFC-0001](WaveBench_scope可移植性RFC-0001_消费型文本查询.md) | 原 `query_text_once()` 被统一 replay 合同和 `scope.error_drain_v1` 取代 | `Superseded R1` | 只保留回归、迁移说明和插件采用条件 |
| [RFC-0002](WaveBench_scope可移植性RFC-0002_通道输入状态.md) | 追加 coupling/termination 分离的输入状态 V2 | `Draft R1` | 新模型、新 capability 和新安全门；旧 coupling 路径不变 |
| [RFC-0003](WaveBench_scope可移植性RFC-0003_截图framing与菜单.md) | 原 `query_raw_bytes_once()` 被 `query_binary()` 和 screenshot profile 取代 | `Superseded R1` | 核心合同已有；具体插件仍需 framing、菜单和恢复证据 |
| [RFC-0004](WaveBench_scope可移植性RFC-0004_数字通道状态.md) | 追加保留未知值和字段作用域的 digital status V2 | `Draft R1` | 只处理状态；digital waveform 另行取证 |
| [RFC-0005](WaveBench_scope可移植性RFC-0005_可组合状态快照.md) | 追加可组合、字段可缺失的 snapshot V2 | `Draft R1` | 不修改现有完整 `ScopeSnapshot` 和 partial summary |
| [RFC-0006](WaveBench_scope可移植性RFC-0006_采集状态与平均采集.md) | 复用 R1.3 acquisition control，另增 status V2 和 average capture V2 | `Draft R1` | 读取与平均事务分阶段实施 |
| [RFC-0007](WaveBench_scope可移植性RFC-0007_统计FFT与光标读取.md) | 拆成统计 selector、FFT status 和 cursor quantity 三项 V2 | `Draft R1` | 三项独立注册、独立验收 |
| [RFC-0008](WaveBench_scope可移植性RFC-0008_有界波形传输裁决.md) | 使用 descriptor profile、`query_binary()` 和核心恢复编排 | `Implemented R1（未发布）` | P0～P3 已完成；插件 opt-in 与实机验收不在本分支 |

## 共同术语

### 未提供、未知与失败

V2 读取模型统一区分三类结果：

- `None`：当前结果没有该字段值；原因必须由模型定义为静态 unavailable 或当前配置下
  not applicable，不能只留下无解释的空值；
- `"unknown"`：查询已完成，但设备返回的状态不能无损映射到公共枚举；
- operation failure：声明可读的 query 发生 I/O、协议或解析失败。

一次查询失败不得转换为 `None` 或 `"unknown"`。未知值不是错误吞并机制。

带 `unavailable_fields` 的模型还必须满足：

- 路径使用稳定的点分隔字段名；
- 路径已排序且不重复；
- 每个静态不可提供的叶字段都能由路径解释；
- 非空字段不得同时列入 `unavailable_fields`；
- 某个完整分区不可提供时，按具体模型的封闭路径规范记录父路径或全部叶路径；同一模型只能
  选择一种规范表示，不能同时记录父路径和子路径，也不能制造虚假的叶字段默认值。

模型若允许当前 mode 下不适用的字段，必须另设封闭、稳定的 `not_applicable_fields` 或等价
typed reason；它不能与 `unavailable_fields` 混用。没有该机制的模型中，`None` 只允许表示
unavailable。

### 状态作用域

逐通道、POD、整机和当前 operation 的状态不得压入同一个含义不明的标量。共享阈值、
全局数字显示大小、全局 acquisition type 和逐通道显示状态必须保留各自作用域。

### 可证明完成

状态为 `STOP`、文本 query 返回成功或 `*OPC?` 完成，只能证明各自协议事件。除非 descriptor
profile 和设备证据明确建立等价关系，否则不能推出平均累积完成、物理触发完成或波形
记录已经更新。

## 共同兼容合同

### 追加式公共 API

1. 不修改现有 `ScopeDriver` 方法签名。
2. 不把现有强制字段改为可空，从而改变旧模型的类型语义。
3. V2 模型、Protocol、capability 和 Service 方法全部追加。
4. 新字段只追加到 descriptor 扩展末尾，并提供保持旧行为的默认值。
5. driver 额外实现方法不产生隐式 capability。
6. capability 未声明、方法缺失或 profile 无效时，在目标 operation 的第一次仪器 I/O 前拒绝。

旧 `ScopeSnapshot`、`ScopeDigitalChannelStatus`、`ScopeAcquisitionStatus`、
`ScopeFftStatus`、`ScopeCursorReadout`、`WaveformData`、`CaptureResult` 和
`MultiCaptureResult` 的字段与成功语义保持不变。

### Legacy 路径

没有声明新 capability/profile 的 descriptor 始终进入 legacy 路径：

- 不要求旧 driver 或 fake 补空方法；
- 不增加新的 transport 关键字；
- 不改变既有 CLI 文本、JSON、run plan 或 artifact；
- 不改变 DS1000Z、DS1104 和 RTM2032 的现有读取路径；
- 不把旧 `scope.errors` 升级成类型化 error drain。

### Construction barrier

任何新 V2 capability 的 opt-in 都必须触发核心 construction barrier。latch 条件是
descriptor capabilities 与核心登记的严格 V2 capability 集合存在交集，不能用某一个
profile 是否非空代替。factory 可以打开
transport，但在 factory 返回、capability/Protocol/profile/backend 校验完成前，guarded
transport 必须拒绝全部仪器 I/O。验证失败后关闭 transport，不发送 IDN、探测 query 或恢复命令。

该门只约束显式 opt-in descriptor。旧 descriptor 的 factory 行为不变。

### Service、CLI 与 run plan

每项新 capability 至少需要公共模型、Protocol、operation registry、Service、序列化和
capability explain 共同冻结。CLI 只能追加命令，不得让旧命令静默改走 V2。

本系列不自动增加 run plan step。只有在 operation 的持久化结果、恢复语义和旧 reader
兼容性已经单独评审后，才允许扩展 run plan schema。

## 核心与插件版本

| 组合 | 预期行为 |
| --- | --- |
| 旧核心 + 旧插件 | 保持原版本行为 |
| 新核心 + 旧插件 | 未 opt-in，继续使用 legacy 模型和方法 |
| 旧核心 + 新插件 | 正常安装由 wheel 依赖拒绝；强制安装仍须在仪器 I/O 前失败 |
| 新核心 + 新插件 | 只开放 descriptor 明确声明且已经验收的能力 |

新增合同的最低版本必须指向「第一个实际发布且包含完整合同的核心版本」。开发树中的版本字符串
不能单独作为发布证据。如果同一版本号可能对应不含合同的既有 artifact，发布前必须改用可区分的
更高版本。

只要新合同保持追加式，`wavebench.instrument.v2` 可以继续使用。若实现需要删除旧字段、
改变旧方法签名或改变旧成功返回语义，必须另立不兼容 API RFC，不能借本系列静默修改。

## 依赖与实施顺序

~~~text
transport R1 + scope R1.3
  ├─ RFC-0001：消费型文本与 error drain 裁决
  ├─ RFC-0003：截图 framing/profile 裁决
  └─ RFC-0008：标准 waveform bounded binary

共同 unknown/unavailable 语义
  ├─ RFC-0002：输入状态 V2
  ├─ RFC-0004：数字状态 V2
  ├─ RFC-0005：snapshot V2
  ├─ RFC-0006a：acquisition status V2
  └─ RFC-0007a/b/c：统计、FFT、光标

RFC-0002 + RFC-0006a + RFC-0008
  └─ RFC-0006b：average capture V2
~~~

推荐里程碑：

1. M0：冻结本组合说明和 legacy 黄金基线；
2. M1：完成 RFC-0001、RFC-0003、RFC-0008 的结案回归；
3. M2：实现 RFC-0002；
4. M3：分别实现 RFC-0004 和 RFC-0005；
5. M4：实现 RFC-0006a；
6. M5：分别实现 RFC-0007 的三项 capability；
7. M6：实现 RFC-0006b 的核心事务；
8. M7：完成跨版本、发行产物和完整离线验收。

每个里程碑应拆成可独立回滚的小提交，不把模型、factory、Service、CLI 和插件采用压入同一个
提交。

## M0 冻结记录

本组合说明以 `Accepted R1` 冻结以下共同边界：编号映射、替代关系、unknown/unavailable
语义、legacy 不变量、construction barrier、四种核心／插件组合和 M1～M7 顺序。

离线黄金基线位于 `tests/test_scope_portability_m0.py`，覆盖：

- RTM2032 与 DS1104/DS1000Z 的 canonical ID、alias、完整 legacy capability tuple、版本范围、
  extension 缺席和 capability explain；
- 两个内建 descriptor 的 standard fetch/capture 继续调用 legacy driver 方法；
- 旧／新核心与旧／新 descriptor 的四组合，其中旧核心强制加载新 descriptor 时在 factory 和
  仪器 I/O 前由版本门拒绝；
- 新 descriptor 只公开显式 capability/profile，不因额外方法或 profile 获得其他 capability。

该冻结不把 RFC-0002、RFC-0004、RFC-0005、RFC-0006 或 RFC-0007 从 `Draft` 升为已实现；每项
仍须在其里程碑完成模型、Protocol、factory、Service、CLI 和兼容回归后更新状态。

## 共同验收门

每项新合同至少覆盖：

- dataclass 的类型、范围、有限数值、互斥字段和稳定序列化；
- capability 与 required Protocol 的一一对应；
- descriptor opt-in、缺方法、缺 profile 和额外方法的零 I/O 行为；
- legacy descriptor、旧 fake 和内建 driver 回归；
- I/O/解析失败不会伪装成 unavailable；
- 四种核心/插件版本组合；
- wheel/sdist 构建和隔离安装；
- 完整 `pytest`、Ruff、中文文档规则和 `git diff --check`。

状态写入或 acquisition operation 还必须覆盖 core-owned baseline、阶段顺序、deadline、失败
恢复、fresh verification、异常优先级和 poisoned 后零追加 I/O。

## 不属于核心文档完成的范围

以下项目继续由插件仓库和受控实机验收负责：

- 具体错误队列的结束 token 与厂商错误格式；
- screenshot 命令的实际 framing、菜单和颜色语义；
- 数字 waveform 的 LOW/HIGH 编码与 WORD 字节序；
- 平均采集完成证据；
- reference/history 语义和扩展 cursor/FFT 模式；
- waveform 的 MAX、DMAX、分块、多通道和 capture 硬件验收。

核心模型发布不等于某个型号已经具备对应 capability。型号、固件、resource/backend 和
请求范围必须分别验收，不能由单通道短记录或离线 fake 外推。
