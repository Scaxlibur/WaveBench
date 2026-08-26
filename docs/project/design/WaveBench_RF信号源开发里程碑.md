# WaveBench RF 信号源开发里程碑

[领域设计](WaveBench_RF信号源设计.md) · [项目文档分类](../README.md)

本文把 RF 信号源领域设计拆成可交付、可验证的双仓库工作项。它不替代当前程序事实源，也不将离线合同写成真实仪器能力。

## 当前基线

| 范围 | 当前状态 | 说明 |
| --- | --- | --- |
| Core `0.8.25` 开发线 | M0 离线完成 | 已有 `rf_source` kind、配置、只读 Service／CLI／doctor、`rf_source.status` run 路径、artifact 和 descriptor extension；未实现 RF 写入事务。 |
| DSG830 包 `0.2.0` | M0 离线完成；A1 已完成 | 已迁移为 `kind="rf_source"`，含 `rf_out` topology 与严格 snapshot parser；production descriptor 声明只读 `rf_source.idn` 与 `rf_source.snapshot`。 |
| 真实仪器证据 | A1 已完成；A2–A5 未开始 | 真实设备能力不能由 fake transport 替代；A1 仅提升 `rf_source.snapshot`。 |

## 双仓库交付规则

| 规则 | 要求 |
| --- | --- |
| Core 优先 | 新 kind、descriptor extension、capability registry、配置、Service、CLI、run 和 artifact 先在 Core 开发线完成；正式 wheel 验收还需要已发布的 Core。 |
| 插件跟随 | DSG830 已迁移为 `kind="rf_source"`，并把 `Requires-Dist: wavebench` 与 descriptor 版本门同步为 `>=0.8.25,<0.9`；当前双仓库开发依赖匹配的 Core checkout／版本范围。 |
| 测试隔离 | 后续 capability 可以只出现在 fake descriptor 中，用于离线测试；production descriptor 不得提前声明。 |
| 证据提升 | capability 进入 production descriptor 前，必须有对应 A 级实机证据，记录型号、固件、选件、端口、端接和最终 RF OFF 状态。 |
| 失败语义 | 不确定写入不重试；只有 session health 允许时，M2 才可最多执行一次目标端口 RF OFF recovery。 |

## 里程碑总览

| 阶段 | 状态 | Core 交付 | DSG830 交付 | 完成条件 |
| --- | --- | --- | --- | --- |
| Seed | 历史完成 | 无 RF Core 改动 | `0.1.0` 的旧 `source.idn` 种子、无 I/O descriptor、包装与 fake 测试 | 已由 M0 迁移取代，不代表 RF 支持。 |
| M0 | 离线完成；A1 已完成 | `rf_source` 只读领域 | `rf_out` topology 与严格 snapshot parser | A1 复核后，生产包声明 `rf_source.idn` 和 `rf_source.snapshot`。 |
| M1 | 离线进行中 | OFF-only CW 配置 | `:FREQ`／`:LEV` 映射与独立回读 | 已有 typed request／result、Service 和 fake 测试；CLI、run step 与正式离线验收仍待完成。 |
| M2 | 未开始 | RF 输出安全事务 | `:OUTP` ON/OFF 及 readback | 安全配置／端接／protection 不满足时 ON 零写拒绝；失败最多一次 OFF recovery。 |
| M3 | 未开始 | 声明式 AM／FM／PM | 已声明的内部 Sine 调制子集 | 只在 OFF 状态、profile 匹配且 postcondition 成立时写入。 |
| M4 | 未开始 | Pulse／Step Sweep 合同 | 已声明子集、arm／fire／stop 映射 | trigger／fire 只能由专项安全规则与实机证据提升。 |

## Seed：历史种子包

DSG830 `0.1.0` 种子包只包含 `*IDN?`、`close()`、无 I/O descriptor、包元数据与离线测试。它使用普通 `source` 名称空间，是 Core M0 前维持打包与开发环境接入的过渡实现；当前包已迁移，不应作为现状描述。

不允许把下列内容从 Seed 推导出来：

- 历史 `source.idn` 种子本身可证明当前 `rf_source` 的能力；
- 频率、dBm 功率、RF 输出、调制、Pulse 或 Sweep 已可用；
- `source.idn` 种子可进入普通 source 的 Vpp、channel 或 run plan 工作流。

## M0：只读领域与迁移（离线完成）

### Core

- 已添加 `rf_source` plugin kind 和 append-only `InstrumentDescriptor.rf_source_extensions`。
- 已冻结 topology、`port_id`、`RfObserved`、snapshot、protection policy、Protocol 与 descriptor validator 的最小类型合同。
- 已添加 `rf_source.idn`、`rf_source.snapshot` capability 与 OperationSpec；复用 access policy、resource lease、guarded transport 和 session health。
- 已添加独立 `[rf_source]` 与按 `port_id` 配置的安全字段；不复用 `SourceConfig`、`source.terminations` 或 Vpp 限制。
- 已添加只读 `rf-source idn`／`rf-source status`、doctor IDN target，以及 `rf_source.status` 的 run schema、check、verify、intent、lifecycle、dispatch 和独立 artifact namespace。

### DSG830

- 已将种子 package 迁移到 `kind="rf_source"`、`rf_source.*` 和 `[rf_source]`。
- 已声明一个稳定端口 `rf_out`、手册范围和设备 dBm 参考阻抗。
- 已实现严格 snapshot parser，分别覆盖正常响应、未知响应、坏响应与 protection condition；A1 后可由 production 的只读状态入口消费。
- A1 已提升 `rf_source.snapshot`；后续 M1–M4 capability 仍只能存在于 fake descriptor 或离线 driver 测试中。

### 离线完成条件

- descriptor 导入和静态校验没有 I/O；每个状态 query、解析分支和坏响应都有测试。
- Core 的 registry、配置、CLI、doctor、run check／verify／intent 与 artifact 测试通过。
- 插件开发依赖区间与 descriptor 版本门均为 `>=0.8.25,<0.9`；正式 wheel 验收等待已发布的 Core 版本。

## M1：OFF-only CW 配置

Core 已在离线开发中加入单字段 `RfCwRequest`／result、`rf_source.set_frequency`／`rf_source.set_power_dbm` OperationSpec、端口范围检查和 OFF-only Service 事务。所有 CW 写入前必须确认目标 RF 输出为 OFF，且调制、Pulse、Sweep 与 protection 状态没有冲突。CLI、run step、artifact 与完整离线验收仍待完成。

DSG830 driver 已在离线测试中实现已冻结的 `:FREQ` 与 `:LEV` 单次写映射；Core 负责独立 snapshot 回读。输出 ON、越界、缺失安全关键状态或 readback 不确定时，必须零写拒绝或停止后续写入；production descriptor 仍不声明 CW write capability，直到 A3。

## M2：RF 输出安全事务

Core 添加每端口安全预检、`rf_source.output_enable`／`rf_source.output_disable`、端接匹配检查、blocking protection policy 和 run safety gate 的 `rf_source_ports`。RF OFF 不依赖频率、功率、端接或 protection readback；RF ON 必须逐项满足所有前置条件。

DSG830 driver 实现 `:OUTP ON|OFF` 与独立 readback。ON 结果不明、写后 readback 失败或 protection 变化时，不重试 ON；只有 session health 允许时，才最多发送一次目标端口 OFF 并回读。production descriptor 直到 A2 后才可声明 output capability。

## M3：调制

Core 冻结 AM／FM／PM profile、request／result、operation context、CLI、run step 与 artifact 字段。DSG830 先限定到手册可审计的内部 Sine 调制子集。任何输出未 OFF、profile 不支持或 postcondition 不符的请求都必须零写拒绝。

production descriptor 的调制 capability 需要 A4 证据；离线 driver 和 fake descriptor 的完整测试不能替代它。

## M4：Pulse 与 Step Sweep

Core 冻结 Pulse／Sweep profile、`arm`／`trigger`／`fire`／`stop` 的 operation 映射和安全规则。`RfPortSnapshot` 中的 Pulse、Sweep 状态必须可区分，不能将外部 trigger、后面板辅助输出或设备私有模式默认为安全。

DSG830 只进入手册与离线测试均覆盖的 Pulse／frequency-only Step Sweep 子集。fake descriptor 可以覆盖 trigger／fire 事务；production descriptor 只有在 A4 或 A5 对应证据具备后才能声明相关 capability。

## A1–A5：实机证据门

| 证据 | 范围 | 可以提升的 production capability |
| --- | --- | --- |
| A1 | 只读 snapshot | `rf_source.snapshot` |
| A2 | RF OFF/ON、readback 与最终 OFF | `rf_source.output` |
| A3 | CW 环回、频率与 dBm 功率 | `rf_source.cw_configure` |
| A4 | 调制、Pulse、Step Sweep | 对应 M3／M4 capability |
| A5 | 外部 trigger 或同步接线 | trigger／fire／同步相关 capability |

每次证据记录必须独立于代码提交，且不能包含真实资源地址、序列号、原始响应或实验室专用配置。未恢复或无法确认最终 RF OFF 的验收不能用于提升 capability。

### A1：已完成的只读 snapshot 验收

A1 已使用一次性、非 production 的本地 evidence harness 完成并经复核。当时没有临时将
`rf_source.snapshot` 加入 production descriptor，也没有通过 `rf-source status` 绕过 capability 门禁。验收期间，production descriptor 始终保持仅 `rf_source.idn`。

1. 使用独立的本地 TOML 副本，不修改现有 `wavebench.toml`。副本中的 `[rf_source]` 必须指定已核对的
   canonical driver、资源和 `access = "read_only"`；不得使用 `read_write` 或资源猜测。若需网络发现，必须在 harness 之外以有界、单独授权的流程完成，人工复核后才写入副本，且发现结果不进入证据。
2. harness 通过受 guard 的 transport 和独占 resource lease 创建单个 session，只调用手册已审计的 snapshot
   query，且不添加 query 重试：`*IDN?`、`:FREQ?`、`:LEV?`、`:OUTP?`、`:MOD:STAT?`、`:PULM:STAT?`、
   `:SWE:STAT?`、`:STAT:QUES:POW:COND?`。
3. 禁止 `*RST`、错误队列、RF OFF/ON、频率／功率／调制／Pulse／Sweep setter、trigger、capture 和任何
   `write`／`write_bytes`。A1 失败时不在该只读流程中尝试 recovery OFF。
4. 本地证据只保存 A1 标签、时间、Core／插件版本、canonical driver、脱敏的型号／固件／选件信息、`rf_out`
   的类型化 snapshot、已人工确认的实际端接、session 结果和 guard audit 摘要。隔离 TOML 的
   `[a1_evidence]` 必须显式记录端口、有限正数端接和已确认的选件列表；firmware 从同一次 `*IDN?` 的
   受限字段提取，不新增查询。不得从连接器标签、scope coupling 或型号名称推导端接，也不得保存资源、
   序列号、完整 IDN、原始响应或命令日志。
5. 成功条件为：parser 完整成功、目标 RF 输出明确为 OFF、session 健康且关闭成功、audit 显示
   `access=read_only`、query 数量与预期一致、所有写计数和 `instrument_mutation_writes` 均为零。输出为 ON、
   状态未知、保护／解析异常、session 异常或关闭失败均为未通过，不能提升 capability。

本次证据已由人工复核，并在对应插件补丁中把 `rf_source.snapshot` 加入 DSG830 production descriptor。该提升不包含任何 RF 写 capability。

## 推荐实施顺序

1. 已完成 Core M0 的 kind、descriptor、配置、只读 Service／CLI 与 run status 全链路。
2. 已在匹配的 Core `0.8.25` 开发线上迁移 DSG830 的 descriptor、依赖区间、topology 与 snapshot parser；正式 wheel 验收等待 Core 发布版本。
3. 已取得并复核 A1 的只读 snapshot 证据；DSG830 parser 已仅作为 `rf_source.snapshot` 暴露为 production capability。
4. 正在用 fake descriptor 完成 M1 的零写拒绝与 postcondition 测试，并实现 DSG830 的对应离线 SCPI 映射；随后单独推进 M2 的 safety preflight 与 recovery。
5. 取得 A2、A3 证据后，按 capability 而非按「整台仪器已支持」逐项提升 production descriptor；M3／M4 保持独立工作。
