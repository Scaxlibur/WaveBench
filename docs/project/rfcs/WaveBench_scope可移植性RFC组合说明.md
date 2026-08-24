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
入口已经被更严格的核心合同取代；RFC-0005、RFC-0006 和
RFC-0007 仍需要追加式 V2 模型。被取代不表示问题不存在，而是表示不能再实现已否决的
平行 API。

M0 冻结本组合说明和 legacy 黄金基线。本文本身不授权新的核心实现、插件 capability 声明或
真实仪器操作；`Implemented R1（未发布）` 只记录已经提交并完成离线验证的核心代码。仍为
`Draft` 的单项 RFC 必须先完成其列出的文档裁决并进入 `Accepted`，才可以另行安排追加式实现。

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
| [RFC-0002](WaveBench_scope可移植性RFC-0002_通道输入状态.md) | 追加 coupling/termination 分离的输入状态 V2 | `Implemented R1（未发布）` | 纯读取、独立安全判断与 construction barrier；旧 coupling 路径不变 |
| [RFC-0003](WaveBench_scope可移植性RFC-0003_截图framing与菜单.md) | 原 `query_raw_bytes_once()` 被 `query_binary()` 和 screenshot profile 取代 | `Superseded R1` | 核心合同已有；具体插件仍需 framing、菜单和恢复证据 |
| [RFC-0004](WaveBench_scope可移植性RFC-0004_数字通道状态.md) | 追加保留未知值和字段作用域的 digital status V2 | `Implemented R1（未发布）` | 只处理状态；digital waveform 另行取证 |
| [RFC-0005](WaveBench_scope可移植性RFC-0005_可组合状态快照.md) | 追加可组合、字段可缺失的 snapshot V2 | `Implemented R1（未发布）` | M3b 已完成核心模型/profile/Protocol/factory gate/Service；不修改完整 snapshot、partial summary 或旧 CLI |
| [RFC-0006](WaveBench_scope可移植性RFC-0006_采集状态与平均采集.md) | 复用 R1.3 acquisition control，另增 status V2 和 average capture V2 | `Implemented R1（未发布；仅 0006a）` | M4 已完成 profile、纯文本预算、零 I/O gate 与 Service；0006b 等待通用 bounded transaction 前置裁决 |
| [RFC-0007](WaveBench_scope可移植性RFC-0007_统计FFT与光标读取.md) | 拆成统计 selector、FFT status 和 cursor quantity 三项 V2 | `Implemented R1（未发布；0007a/0007b/0007c）` | M5a/M5b/M5c 已完成 statistics/FFT/cursor 的 profile、零 I/O gate 与 Service；不改旧 CLI/artifact |
| [RFC-0008](WaveBench_scope可移植性RFC-0008_有界波形传输裁决.md) | 使用 descriptor profile、`query_binary()` 和核心恢复编排 | `Implemented R1（未发布）` | P0～P3 已完成；插件 opt-in 与实机验收不在本分支 |

## 本轮文档冻结

本轮只完善核心侧 RFC，不新增 `src/`、`tests/`、CLI、run plan schema 或插件 descriptor 改动。
已经标为 `Implemented R1（未发布）` 的 RFC-0002、RFC-0004 和 RFC-0008 仍不得被解释为主包
内建 driver 或任一外部插件已经 opt-in；当前开发线版本 `0.8.24` 也不是可供插件声明最低版本的
正式发行物。

RFC-0005 和 RFC-0006a 的 R1 核心实现已完成但尚未发布，外部插件仍不得据此声明 capability；
RFC-0007a/0007b/0007c 的 R1 核心实现也已完成但尚未发布，外部插件仍不得据此声明 capability；0007c 的
global/indexed profile、factory gate、Service 和离线验收只属于核心，不授权插件 opt-in、版本下限升级或硬件
conformance 分支。RFC-0006b 仍是候选模型，在进入 `Accepted` 前不得创建 capability、Protocol、Service、
CLI、descriptor profile 或插件 conformance 分支。RFC-0001、RFC-0003 的原始
入口已经被取代，不重新实施。

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

除非具体 RFC 另有声明，路径的「排序」均指其封闭 `Literal` 声明顺序，而不是字典序。完整
分区的表示由各模型自行冻结：RFC-0005 的 snapshot V2 只记录全部封闭叶路径；已经把父路径
列入其封闭路径集的模型，例如 RFC-0004 的 `pod`／`shared` 或 RFC-0006 的 `average`／`segmented`，
才可以使用父路径。不得把一种模型的表示规则套用到另一种模型。

模型若允许当前 mode 下不适用的字段，必须另设封闭、稳定的 `not_applicable_fields` 或等价
typed reason；它不能与 `unavailable_fields` 混用。没有该机制的模型中，`None` 只允许表示
unavailable。

### Safe token

除非字段明确承载用户标签、身份文本或 source-defined unit，文档中的 safe token 均使用
`^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,63}$`。它不得包含空白、控制字符、引号、逗号、资源地址或
原始 SCPI 响应片段。无法无损归一化到这一集合的已查询状态，应使用该模型定义的 `"unknown"`，
或使 operation 失败；不得把原始响应写入 artifact。

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

本编号系列中明确要求 strict V2 opt-in 的 capability 必须触发核心 construction barrier。latch
条件是 descriptor capabilities 与核心登记的严格 V2 capability 集合存在交集，不能用某一个
profile 是否非空代替。factory 可以打开
transport，但在 factory 返回、capability/Protocol/profile/backend 校验完成前，guarded
transport 必须拒绝全部仪器 I/O。验证失败后关闭 transport，不发送 IDN、探测 query 或恢复命令。

该门只约束显式 opt-in descriptor。旧 descriptor 的 factory 行为不变。

RFC-0003 的 `scope.screenshot_v2` 是既有 scope R1.3 profile 合同，遵循其专用 validator 和
factory 语义；不能因为本编号系列中的输入／数字状态 V2 使用 strict latch，就把该 latch 规则
反向推广到截图 V2。后续 Draft RFC 若需要 latch，必须在单项 RFC 中显式声明并注册。

### Service、CLI 与 run plan

每项新 capability 至少需要公共模型、Protocol、operation registry、Service、序列化和
capability explain 共同冻结。CLI 只能追加命令，不得让旧命令静默改走 V2。

RFC-0005 R1、RFC-0006a R1、RFC-0007a/0007b/0007c R1，以及当前 `Draft` 阶段的 RFC-0006b 均不新增 V2 CLI 或
run plan step；旧 `scope status`、`acquisition-status`、`capture-average`、`measurement-statistics`、
`fft-status` 和 `cursor-readout` 继续只路由到 legacy Service。若单项 RFC 要新增 CLI 或 artifact，
必须先冻结新命令名、参数、JSON 成功形状和 artifact 版本，不能借用旧命令名或 R1.3 extension envelope。

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

推荐里程碑如下。M0～M5b 是已完成的历史记录；M5c 及后续仍只保留为文档和验收顺序：

1. M0：冻结本组合说明和 legacy 黄金基线；
2. M1：完成 RFC-0001、RFC-0003、RFC-0008 的结案回归；
3. M2：实现 RFC-0002；
4. M3：RFC-0004 和 RFC-0005 已完成核心离线实现；
5. M4：RFC-0006a 已完成只读模型/profile、factory gate、纯文本 budget Service 与离线兼容回归；
6. M5：0007a 已完成完整 statistics 成功值、selector/profile、纯文本 budget、factory gate 与 Service；
   0007b 已完成静态 FFT profile、configured、文本 budget、factory gate 与 Service；0007c 已完成 global/indexed
   profile、单位/path、文本 budget、factory gate 与 Service；
7. M6：为 RFC-0006b 单独接受可复用的 bounded transaction 基础，随后才评审平均采集事务；
8. M7：在每项已接受且已实现后，完成跨版本、发行产物和完整离线验收。

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

M0 冻结本身不把后续 RFC 自动升为已实现；每项仍须在其里程碑完成所接受的模型、Protocol、factory、
Service 和兼容回归后更新状态。RFC-0006a 已在 M4 完成核心离线实现，其 R1 明确不包含 CLI。

## M1 完成记录

RFC-0001、RFC-0003 与 RFC-0008 的核心替代合同已完成离线回归，不新增
`query_text_once()`、`query_raw_bytes_once()` 或扩展 legacy `query_bin_block()`。

- `tests/test_transport_call_classification.py` 将 `query_binary()` 纳入核心 driver 的显式 replay
  静态检查；
- `tests/test_transport_replay_matrix.py` 固定 PyVISA、RsInstrument 与 Serial 的文本／legacy binary
  query 在 `no_replay`、已证明安全的 replay 和 continuation 三种策略下的发送次数；
- `tests/test_scope_binary_contract.py` 固定 PyVISA/RsInstrument bounded binary 的单次发送，以及
  guarded bounded phase 对 replay／continuation 的发送前拒绝，且不调用后端；
- `tests/test_scope_portability_m0.py` 继续覆盖内建 RTM2032、DS1104/DS1000Z 的 legacy 路由，确保
  新 profile 不改变其 fetch/capture 行为。

`scope.error_drain_v1`、screenshot V2 和标准 waveform bounded profile 仍是开发线中
`Implemented（未发布）` 的核心合同。外部插件采用必须等待第一个实际包含完整合同的正式发行版本；
开发树中的 `0.8.23`／`0.8.24` 只作为静态校验下限，不能单独证明可发布的版本门。

## M2 完成记录

RFC-0002 已完成核心离线实现：`ScopeChannelInputStateV2` 保留独立 coupling、termination 和
可解释的 `impedance_ohm` 缺席状态；Protocol、capability、OperationSpec、factory construction
barrier、Service、CLI 和 capability explain 均已注册。

`tests/test_scope_input_state_v2.py` 覆盖模型不变量、版本门、缺方法和额外方法、factory 零 I/O、
V2 安全判断、Service/CLI JSON，以及同时声明 V2 时 legacy high-impedance gate 继续只读
`channel_coupling()`。R1 不把 V2 自动接入标准 fetch/capture，也不授权插件或具体型号声明该能力。

## M3a 完成记录

RFC-0004 已完成核心离线实现：`ScopeDigitalChannelStatusV2` 明确分开逐通道、POD 和 shared
状态；`"unknown"` 表示已成功查询但无法无损映射，`None` 只在精确的 `unavailable_fields` 路径中
表示不可提供。独立 Protocol、capability、OperationSpec、factory construction barrier、Service、CLI
和 capability explain 已注册。

`tests/test_scope_digital_status_v2.py` 覆盖模型、factory、Service、CLI 和 legacy dual-capability
分流。R1 不创建 digital waveform decoder 或 payload 合同；MSO8000 当前没有数字 status/waveform
driver、descriptor capability 或离线数字 fixture，仍不得 opt-in。

## M3b 完成记录

RFC-0005 已完成核心离线实现：`ScopeSnapshotV2` 对六个可空分区和全部封闭叶路径执行精确
unavailable/not-applicable 校验；`ScopeSnapshotProfileV2` 强制 identity、分区身份字段、纯文本
query 上限和条件字段。独立 Protocol、capability、OperationSpec、strict factory construction barrier
和 `ScopeService.snapshot_v2()` 已注册；Service 不运行 legacy identity preflight，也不进入 R1.3
extension service 或旧 `scope status` 路由。

`tests/test_scope_snapshot_v2.py` 覆盖模型、profile、capability、factory、query budget、非 query I/O
拒绝和 legacy route。核心完整离线回归通过；外部 MSO8000 插件在新 core source 下的离线测试通过，
其 descriptor 仍未声明 snapshot V2。R1 不新增 CLI、artifact、run plan 或任何主包／插件 opt-in。

## M4 完成记录

RFC-0006a 已完成核心离线实现：`ScopeAcquisitionStatusV2` 的 average／segmented 父子 availability
路径精确区分 static unavailable 与当前 mode 的 not applicable；`ScopeAcquisitionStatusProfileV2` 强制
`acquisition_type`、`1..32` 的纯文本 query budget、条件分区和 run-state capability 依赖。独立 Protocol、
capability、strict factory construction barrier、portability-V2 `OperationSpec` 和
`ScopeService.acquisition_status_v2()` 均已注册。

Service 只调用 V2 driver 方法一次，传递 profile 的 `readable_fields`，并在一个只允许 `query()` 的受预算
phase 中验证返回值；它不执行 legacy identity preflight、R1.3 acquisition-control Service、error drain、
`*STB?`、`*ESR?`、binary 或 write。旧 `scope acquisition-status`、legacy status 模型、CLI、artifact 和
run-plan 均不变。`tests/test_scope_acquisition_status_v2.py` 覆盖模型、profile、factory、query budget、
non-query I/O 拒绝和 legacy route；核心完整离线回归与外部 MSO8000 插件新 core source 回归均通过。内建
descriptor 和插件仍未声明 status V2，0006b 继续 blocked。

## M5a 完成记录

RFC-0007a 已完成核心离线实现：`ScopeMeasurementSelector` 以 slot 或 item/source 精确 XOR；
`ScopeMeasurementStatisticsV2` 只接受完整、有限的六项统计值；append-only profile 在打开 session 前校验
selector mode、slot/item/source count、`configured=True` 和 R1 buffer 拒绝。独立 Protocol、capability、
strict factory construction barrier、portability-V2 `OperationSpec` 和
`ScopeService.measurement_statistics_v2()` 均已注册。

Service 只调用 V2 driver 方法一次，并在一个只允许 `query()` 的 `1..32` budget phase 内验证 selector echo
与完整结果；它不调用 legacy statistics、legacy identity preflight、error drain、binary 或 write。旧
`scope measurement-statistics` CLI、slot API、nullable legacy result、artifact 和 run-plan 均保持原样。
`tests/test_scope_measurement_statistics_v2.py` 覆盖模型、profile、factory、query budget、buffer 零 I/O 和
legacy route；核心完整离线回归与外部 MSO8000 插件新 core source 回归均通过。内建 descriptor 和插件仍未
声明 statistics V2；0007b/0007c 均已完成独立核心离线实现。

## M5b 完成记录

RFC-0007b 已完成核心离线实现：`ScopeFftStatusV2` 以精确 unavailable paths 表示可证明的静态字段缺失，
并强制 frequency start/stop 成对出现、RBW/sample rate 为有限正数；append-only
`ScopeFftStatusProfileV2` 锁定静态字段闭包与 `1..32` 的纯文本 query budget。独立 Protocol、capability、
strict factory construction barrier、portability-V2 `OperationSpec` 和 `ScopeService.fft_status_v2()`
均已注册。

Service 在打开 session 前拒绝非正 index 或 `configured_fft is not True`，只调用 V2 driver 一次，并在只允许
`query()` 的 budget phase 内验证结果；它不调用 math metadata、legacy FFT、identity preflight、error drain、
binary 或 write。旧 `scope fft-status` CLI、legacy strong-field model、artifact 和 run-plan 均保持原样。
`tests/test_scope_fft_status_v2.py` 覆盖 model/profile、factory、query budget、math metadata 隔离与 legacy route；
核心完整离线回归与外部 MSO8000 插件新 core source 回归均通过。内建 descriptor 和插件仍未声明 FFT V2；
0007c 已完成独立核心离线实现。

## M5c 完成记录

RFC-0007c 已完成核心离线实现：`ScopeCursorReadoutV2` 保留 global/indexed addressing、A/B source、五种
quantity unit、source-defined unit 与精确 unavailable/not-applicable path；append-only
`ScopeCursorReadoutProfileV2` 锁定静态可读字段、conditional fields、寻址和 `1..32` 的纯文本 query budget。
独立 Protocol、capability、strict factory construction barrier、portability-V2 `OperationSpec` 和
`ScopeService.cursor_readout_v2()` 均已注册。

Service 在打开 session 前拒绝非真 `configured_cursor`、非法 index 和 profile/addressing 不匹配；在共享 session
state 存在时只调用 V2 driver 一次，并限制为 query-only budget phase。global cursor 的 `None` index 必须以
not-applicable path 明示，indexed 结果必须精确 echo request；不存在 shared state 的 transport 发送前失败。
旧 `scope cursor-readout` CLI、legacy model/Protocol、artifact 和 run-plan 均保持原样。
`tests/test_scope_cursor_readout_v2.py` 覆盖模型/profile、factory、五种 unit、两个 fixture、query budget、
non-query I/O、missing-state fail-closed 与 legacy route；核心完整离线回归与外部 MSO8000 插件新 core source
回归均通过。内建 descriptor 和插件仍未声明 cursor V2。

## 后续 Draft 验证与接受门

本轮已在单项 RFC 中冻结以下文档语义：RFC-0005 的 identity 新鲜来源、text query 计数、封闭
availability 与独立返回边界；RFC-0006a 的 profile、文本预算、父／子 availability、run-state 条件依赖
与 legacy 路由；RFC-0007a 的完整 statistics 成功值、selector/profile、R1 buffer 拒绝、纯文本 budget 和
无 CLI/artifact 边界；RFC-0007c 的 global/indexed addressing、availability、纯文本 budget、strict latch 和
无 CLI/artifact 边界。

RFC-0005、RFC-0006a、RFC-0007a/0007b 已分别完成 M3b/M4/M5a/M5b 核心离线矩阵；0006b
进入 `Accepted` 前仍须完成：

- RFC-0006b：不修改 RFC-0008 标准 waveform profile 的前提下，另行接受可复用 bounded transaction
  限制、backend gate、ledger 与 construction barrier 的核心内部前置合同。

本轮已完成 M5c 核心代码；M6～M7、外部插件 capability 与硬件验收仍不因上述文档冻结而启动。

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
