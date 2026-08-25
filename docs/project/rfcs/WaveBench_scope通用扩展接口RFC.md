# WaveBench scope 通用扩展接口 RFC

> 状态：`Accepted`（核心 `0.8.23` 开发线已实现公共合同）
> 修订：`R1.3`（核心复审增补）
> 证据仓库：WaveBench Instrument Plugins
> 核心评审基线：WaveBench `0.8.22`，`origin/master@006c431`
> 目标版本：WaveBench `0.8.23`
> 同步来源：WaveBench Instrument Plugins `a013891`；核心副本补充实施状态与跨仓库证据链接。

## 摘要与状态边界

本文冻结 WaveBench scope 通用扩展合同。核心 `0.8.23` 开发线已经实现并注册本文定义的
capability、operation、Service、CLI、artifact 和版本门。具体实现状态与插件采用条件见
[核心实施说明](WaveBench_scope通用扩展接口RFC_核心实施说明.md)。

本修订吸收了对 `R1.1` 和前一轮 `R1.2` 复审意见，重点暂定四类此前仍有歧义的安全规则：

1. action-specific `OperationSpec`、binary budget、Service、访问策略、资源租约、会话健康和 artifact；
2. `MESSAGE` binary 的边界、核心强制上限、超限、部分响应和失步处理；
3. 采集运行状态的状态机、单次采集完成证据、超时和恢复事务；
4. 类型化 trace 的首版运算/单位范围，以及三态错误检查的未知能力和继续策略。

核心已经注册下文新增的 operation 和 capability。插件不会因此自动获得新能力；SDS800X HD
当前正式 descriptor 仍只声明 `scope.idn`、
`scope.channel_coupling`、`scope.fetch_waveform`、`scope.capture_waveform`、
`scope.capture_waveforms` 和 `scope.measurement_statistics`。

本文把内容分为三类：

- **现状证据**：已有核心代码、插件离线测试或受控实机观察；
- **规范合同**：核心与 opt-in 插件必须共同遵守的公共接口；
- **后续问题**：不属于 R1.3 公共范围的设计，不得由插件自行扩展。

### R1.3 本轮复审回应

| 核心阻断项 | 本修订的合同处理 | 状态 |
| --- | --- | --- |
| 失败恢复没有 driver 边界 | acquisition/screenshot 均提供 typed snapshot、core-owned baseline、restore result 和 fresh-snapshot verify；恢复阶段、字段顺序、epoch 与异常优先级固定 | 已实现并测试 |
| binary budget 与子授权冲突 | 选择单一 operation context；各阶段顺序授权且不嵌套，所有 binary phase 引用同一 ledger，error phase 不得创建或重置额度 | 已实现并测试 |
| count modulus 不能识别回绕/复位 | 删除 modulus proof；`count_delta_with_epoch` 必须联合未变化 `counter_epoch` 和有效 `state_transition`，否则改用 identity/state proof | 已由 fixture 固定 |
| 旧 `scope.errors` 与 typed drain 混用 | `scope.error_drain_v1` 独占 `max_records+1`；旧 `scope.errors` 保持 `legacy_unstructured`，`terminated/query_count=null`，未来 typed direct drain 另立 operation | 已按兼容边界实现 |

本表记录核心裁决。插件是否迁移仍由各插件 descriptor 和验收证据决定。

核心后续复审指出的 P0/P1 项由第十二节 `R1.3 acceptance addendum` 和配套的
[A1 索引](WaveBench_scope通用扩展接口RFC-R1.3-acceptance-addendum.md)收口：transfer recovery、
capability-method/descriptor 映射、数值上限与 deadline 已写成候选合同；旧 capture 采用唯一
的父 operation 字段闭包；baseline 增加 context/nonce/一次性消费；identity proof 改为静态
profile 事实；trace 的 spectrum/math 开放项排除在首轮公共实施外。第十二节 A1 已在核心
`0.8.23` 开发线通过离线验收，公共 capability 已注册；插件仍须完成自身 conformance 和实机验收。

## 术语与分层

| 术语 | 含义 | 不应混淆的对象 |
| --- | --- | --- |
| transport message | 一次 transport 交换，由 backend 证明起止边界 | TCP `recv()` 的物理分片 |
| application chunk | 一个波形或文件协议定义的逻辑分块 | transport message；一次 query 的任意重试 |
| operation | 由核心注册并受 `OperationSpec` 约束的公共动作 | driver 私有辅助函数 |
| capability | descriptor 声明的可发现能力 | 方法存在但未声明的偶然实现 |
| session health | 当前连接 epoch 的健康状态 | 仪器前面板显示的运行状态 |
| artifact | Service 产生的可审计结果和诊断元数据 | 原始二进制 payload 或完整命令日志 |

transport 只负责可靠地交付字节和结构化 I/O 状态；PNG、波形 preamble、样本字节序和数学
结果属于更高层的 driver/model。应用分块属于波形协议层，不能因为一次 `DATA?` 返回分成
多次底层 read 就自动生成多个 application chunk。

## 当前证据

| 证据编号 | 功能 | 证据 | 当前结论 |
| --- | --- | --- | --- |
| `E-BLOCK-DS` | definite block 截图 | DS1000Z [driver 测试](https://github.com/Scaxlibur/wavebench-instrument-plugins/blob/master/packages/wavebench-rigol-ds1000z/tests/test_driver.py)，离线 fake transport | 现有 `query_bin_block()` 可覆盖该类仪器，尚不是 backend EOM 实机证据 |
| `E-BLOCK-RTM` | definite block 截图、菜单和颜色控制 | RTM2000 [driver 测试](https://github.com/Scaxlibur/wavebench-instrument-plugins/blob/master/packages/wavebench-rohde-schwarz-rtm2000/tests/test_driver.py)，离线 fake transport | 旧 screenshot 参数只对部分设备可映射 |
| `E-MESSAGE-SDS` | raw PNG message | SDS804X HD 固件 `4.8.12.1.1.6.5`、PyVISA TCPIP `INSTR` / VXI-11 受控观察，见[硬件验收](https://github.com/Scaxlibur/wavebench-instrument-plugins/blob/master/packages/wavebench-siglent-sds800x-hd/doc/SDS800X_HD_HARDWARE_ACCEPTANCE.md) | 只有一个仪器族的一次完整 raw PNG 观察，尚未证明通用 backend EOM 合同 |
| `E-CAPTURE` | SINGLE、Stop 轮询、同次多通道读取 | 同一 SDS804X HD 实机记录和[插件测试](https://github.com/Scaxlibur/wavebench-instrument-plugins/blob/master/packages/wavebench-siglent-sds800x-hd/tests/test_driver.py) | 证明 vendor transaction 可行，不证明核心已有独立控制 API |
| `E-TRACE-RTM` | math/FFT 元数据 | RTM2000 [覆盖矩阵](https://github.com/Scaxlibur/wavebench-instrument-plugins/blob/master/packages/wavebench-rohde-schwarz-rtm2000/doc/RTM2000_COVERAGE_MATRIX.md)与离线测试 | 只有一类可复用 trace 证据 |
| `E-TRACE-SDS` | SDS math 函数关闭 | SDS804X HD 受控探测 | 不能为 SDS 构造通用 math/FFT trace |
| `E-ERROR-SDS` | 无文档化错误队列 | CN11G 手册审计和实机边界 | 不能发送猜测命令，也不能返回伪造空队列 |

当前证据支持核心发布 R1.3 合同和插件开始 opt-in 迁移。PyVISA、RsInstrument 与 SDS800X HD
fixture 已覆盖离线 backend、失败恢复和状态机语义；这些证据不替代具体型号的实机验收。

## 目标

- 让 definite block 和 message-bounded binary 使用同一套 replay、session health、access
  policy、租约和审计合同。
- 让核心公共合同不暴露 backend session；当前 Python 插件仍是受信任代码，本文不声称提供
  进程级或运行时沙箱隔离。
- 让截图请求只使用仪器明确声明支持的 request tuple，并记录实际生效请求。
- 区分采集运行阶段、触发模式、平均/分段进度和完整 capture 事务。
- 让模拟、数字、数学、参考和频域轨迹使用同一套 source、坐标轴、单位和数组不变量。
- 让错误检查策略区分「能力明确不支持」「能力未知」「查询失败」和「设备返回错误」。
- 保留现有 capability，通过新增 capability 和核心版本门逐步迁移。

## 非目标

- 不在 transport 中解析 PNG、波形 preamble 或厂商文件格式。
- 不开放 raw SCPI、backend session、插件 parser 回调或任意 terminator。
- 不把 `*OPC?` 定义为物理触发完成条件。
- 不在本文设计数学表达式写入、FFT 配置或通用运算 AST。
- 不废弃现有 `scope.fetch_waveform`、`scope.capture_waveform(s)`、`scope.screenshot` 或
  `ScopeAcquisitionStatus`。
- 不为没有文档化错误队列的仪器猜测命令。
- 未声明新 capability 的插件不提高核心版本下限；声明任一 R1.3 capability 的插件必须要求
  WaveBench `0.8.23` 或更高的 `0.8.x` 版本。

## 插件信任边界

当前 `DriverContext.open_transport()` 返回公共 Protocol，但运行时
`GuardedAuditedTransport.inner` 仍可被受信任 Python 插件访问。因此 R1.3 只规定「公共合同
不提供 backend session，插件代码不得依赖它」，不声称存在安全隔离。真正的 opaque facade、
进程隔离、运行时属性拒绝和负向沙箱测试应由单独的安全设计处理，不能借本 RFC 的措辞假装
已经实现。

## 规范用语与兼容边界

本文中的 `MUST` 表示互操作所需的不变量，`SHOULD` 表示默认实现，`MAY` 表示可选能力。
插件只能声明已经实现并验收的 capability，不得以方法存在为由绕过核心 Service 或 transport gate。

旧核心 + 新插件、新核心 + 旧插件、旧核心 + 旧插件和新核心 + 新插件四种组合都必须在
factory、能力发现和第一次仪器 I/O 前得到确定结果；未知 capability 不能静默降级为已支持。

## 一、核心集成合同：OperationSpec、Service 与 artifact

### 1.1 现有核心字段与候选扩展

当前核心 `OperationSpec` 与外部 access policy 已有以下安全元数据：
`effect`、`lease_mode`、`changed_fields`、`restore_coverage`、`required_verified_fields`、
`verification_fields`、`risk_flags`、`timeout_source`、capability 要求；access policy 由核心
外部的 `access_policy(spec)` 统一判定，不是 `OperationSpec` 自身字段。
R1.3 operation MUST 使用这些字段进入中央 registry；不能只在 driver Protocol 中声明方法。

候选扩展只使用静态、可序列化的安全元数据：

```python
@dataclass(frozen=True)
class OperationSpec:
    # 省略现有字段
    postcondition_fields: tuple[str, ...] = ()
    cleanup_verification_fields: tuple[str, ...] = ()
    binary_response_max_bytes: int | None = None
    binary_operation_max_bytes: int | None = None
    binary_query_max_count: int | None = None
    binary_resynchronization_max_bytes: int | None = None
    error_check_minimum: Literal["required", "if_supported", "disabled"] | None = None
```

当前核心的 `_session_preflight()` 只内建 `scope.identity` verifier；表中其余
`scope.run_state`、`scope.waveform_*`、`scope.display_*` 和 `scope.trace_configuration` 都是
待核心实现的验证器，不是插件可以自行写入 `verified_fields` 的旁路。

`scope.screenshot_profile` 和 profile variant 是 descriptor/profile 事实，不属于连接 epoch 的
`verified_fields`；它们必须先在核心内存中完成静态校验，再作为 operation 输入约束。只有从
仪器读回并由核心 verifier 校验的状态，才能进入 session verification fields。插件不得直接
调用 session state 的内部方法或写入 verified fields。

R1.3 暂定四个独立 binary 限制：`binary_response_max_bytes` 限制每次 payload，
`binary_operation_max_bytes` 限制一个 operation 内所有 binary payload 的累计值，
`binary_query_max_count` 限制 query 次数，`binary_resynchronization_max_bytes` 限制超限或异常后为寻找
已证明边界而额外丢弃的字节数。会产生 binary response 的 operation 中，前三者必须是
有限正整数，resynchronization 必须是有限、非 bool 的非负整数；`0` 表示超限后不做额外
丢弃，直接关闭/毒化。非 binary operation 的对应字段可为 `None`。profile 和 connection 只能
给出更小的对应限制。
核心 Service 在 operation 开始时分别计算有效值，并向 guarded transport 安装 opaque、
短生命周期的
`BinaryQueryBudget`。transport 每次 binary query 都必须验证 budget 与 operation context、phase、
correlation 和 session epoch 匹配；插件只能进一步收紧单次上限，不能提高或重置累计额度。没有 budget 的新
`query_binary()` 调用在发送前拒绝；旧 `query_bin_block()` 兼容入口只保留给 legacy operation，
在 active binary budget phase 中必须在发送前拒绝。

现有 `verification_fields` 只表示按 `restore_coverage` 恢复到 baseline 后必须闭合的字段，
不用于表示读操作的观察结果，也不用于证明有意保留的控制状态。
`postcondition_fields` 声明成功后由 action-specific result verifier 验证的目标状态；
`cleanup_verification_fields` 声明失败或取消后 best-effort cleanup 要验证的目标状态。
后两者的证据只进入 operation result/artifact，不写入 session `verified_fields`；若同一字段还要
恢复 baseline，必须另外列入 `verification_fields`。

`error_check_minimum` 为 `None` 时，该 operation 不接受 error-check override，也不触发
错误队列 I/O；为三态值时，请求可以选择同等或更强的策略，但不能削弱静态最低值。
完整解析规则见第六节。输入/输出 schema、取消、幂等性和并发策略仍可作为后续核心扩展，
但在 R1.3 中不把任意 Python 回调塞入公共合同。前置条件、恢复覆盖、验证字段、
error policy 和 binary budget 必须可序列化、可审计。

字段 verifier 归属如下：

| 字段 | 类型 | verifier / 来源 |
| --- | --- | --- |
| `scope.identity` | 仪器状态 | 现有核心 identity verifier |
| `scope.run_state`、`scope.trigger`、`scope.acquisition` | 仪器状态 | 核心 acquisition verifier |
| `scope.display_menu`、`scope.display_color` | 仪器状态 | 核心 screenshot restore/verification verifier |
| `scope.waveform_source`、`scope.waveform_mode` | 仪器状态 | 核心 waveform-source/mode verifier |
| `scope.query_response_header`、`scope.waveform_format`、`scope.waveform_byte_order`、`scope.waveform_points`、`scope.waveform_transfer_window` | 仪器状态 | 核心 waveform-transfer verifier |
| `scope.trace_configuration` | 仪器状态 | 不属于 R1.3 公共 fetch 范围，留给后续 RFC |
| `scope.screenshot_profile` | descriptor/profile 事实 | 核心 profile validator，不写入 session verified fields |
| `scope.acquisition_control_profile` | descriptor/profile 事实 | 核心静态 acquisition-control profile validator，不写入 session verified fields |
| `scope.trace_profile` | descriptor/profile 事实 | 核心静态 trace profile validator，不写入 session verified fields |
| `scope.error_queue` | 条件性消耗状态 | 核心 error-policy executor；只作为 changed field/artifact，不写入 verified fields |

波形协议字段的最小映射必须保持显式：

| 厂商状态示例 | 核心字段 | changed / verification 要求 |
| --- | --- | --- |
| `CHDR` 响应头/头部模式 | `scope.query_response_header` | 临时改变时两者都必须列出 |
| `CORD` 字节序 | `scope.waveform_byte_order` | 临时改变时两者都必须列出 |
| `WFSU` 格式、宽度、点数和窗口 | `scope.waveform_format`、`scope.waveform_points`、`scope.waveform_transfer_window` | 每个实际改变的字段都必须逐项列出 |

### 1.2 Operation 映射

下表是 R1.3 的最小候选映射，字段完整不等于合同已经冻结。Service 和 CLI 项都是候选入口，
当前不存在，不能在插件侧自行模拟。所有 operation 的 `session_purpose` 为 `normal`；超时后的安全停止由核心另行签发
有界 `recovery` transaction。R1.3 保守地为所有仪器 operation 使用 `exclusive` lease，因为
当前 `ScopeService` 的 session lease 不会按 `OperationSpec.lease_mode` 动态切换。以后若开放
共享只读 session，需要单独证明 backend、仪器和 transaction lock 的并发语义。

| operation | capability | effect / lease | changed_fields | restore_coverage | required_verified_fields | verification_fields | postcondition / cleanup fields | risk_flags | timeout_source | binary response / operation / query / resync limits | error minimum | 最低 access | Service / CLI / artifact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `scope.screenshot_profile` | `scope.screenshot_profile` | `stateful_read` / `exclusive` | `none` | `none` | `scope.identity` | `none` | — / — | `profile_query` | `operation.timeout_ms=5000` | — | — | `read_only` | `ScopeService.screenshot_profile()` / `wavebench scope screenshot profile` / `screenshot.profile` |
| `scope.screenshot_v2` | `scope.screenshot_v2` | `write` / `exclusive` | `scope.display_menu`, `scope.display_color`, `scope.error_queue`, `output.screenshot` | `screenshot-baseline-only` | `scope.identity` | `scope.display_menu`, `scope.display_color` | — / `scope.display_menu`, `scope.display_color` | `front_panel_state`, `binary_response`, `temporary_display_setup` | `operation.timeout_ms=5000` | `8388608 / 8388608 / 1 / 0` | `disabled` | `read_write` | `ScopeService.screenshot_v2(request)` / `wavebench scope screenshot capture` / `screenshot`、`effective_request`、`media_type`、`dimensions`、`framing` |
| `scope.acquisition_run_state` | `scope.acquisition_run_state` | `stateful_read` / `exclusive` | `none` | `none` | `scope.identity` | `none` | — / — | `state_observation` | `operation.timeout_ms=5000` | — | — | `read_only` | `ScopeService.acquisition_run_state()` / `wavebench scope acquisition status` / `acquisition.run_state` |
| `scope.acquisition_start` | `scope.acquisition_control` + `scope.acquisition_run_state` | `write` / `exclusive` | `scope.run_state`, `scope.trigger`, `scope.acquisition`, `scope.error_queue` | `failure-cleanup-only` | `scope.identity` | `scope.trigger`, `scope.acquisition` | `scope.run_state`, `scope.trigger`, `scope.acquisition` / `scope.run_state`, `scope.trigger`, `scope.acquisition` | `trigger`, `acquisition_state`, `recovery_required` | `operation.timeout_ms=30000` | — | `disabled` | `read_write` | `ScopeService.start_acquisition(request)` / `wavebench scope acquisition start` / `acquisition.control`、`effective_trigger_mode`、`postcondition`、`cleanup` |
| `scope.acquisition_single` | `scope.acquisition_control` + `scope.acquisition_run_state` | `acquire` / `exclusive` | `scope.run_state`, `scope.trigger`, `scope.acquisition`, `scope.error_queue` | `failure-cleanup-only` | `scope.identity` | `scope.trigger`, `scope.acquisition` | `scope.run_state`, `scope.trigger`, `scope.acquisition` / `scope.run_state`, `scope.trigger`, `scope.acquisition` | `trigger`, `acquisition_state`, `recovery_required` | `operation.timeout_ms=30000` | — | `disabled` | `read_write` | `ScopeService.acquire_single()` / `wavebench scope acquisition single` / `acquisition.control`、`postcondition`、`completion_proof`、`cleanup` |
| `scope.acquisition_stop` | `scope.acquisition_control` + `scope.acquisition_run_state` | `write` / `exclusive` | `scope.run_state`, `scope.error_queue` | `failure-cleanup-only` | `scope.identity` | `none` | `scope.run_state` / `scope.run_state` | `acquisition_state`, `recovery_required` | `operation.timeout_ms=5000` | — | `disabled` | `read_write` | `ScopeService.stop_acquisition()` / `wavebench scope acquisition stop` / `acquisition.control`、`postcondition`、`cleanup` |
| `scope.trace_metadata` | `scope.trace_metadata` | `stateful_read` / `exclusive` | `none` | `none` | `scope.identity` | `none` | — / — | `analysis_state` | `operation.timeout_ms=5000` | — | `disabled` | `read_only` | `ScopeService.trace_metadata(source)` / `wavebench scope trace metadata` / `trace.metadata` |
| `scope.fetch_trace` | `scope.fetch_trace` | `acquire` / `exclusive` | `scope.run_state`, `scope.waveform_source`, `scope.waveform_mode`, `scope.query_response_header`, `scope.waveform_format`, `scope.waveform_byte_order`, `scope.waveform_points`, `scope.waveform_transfer_window`, `scope.error_queue`, `output.trace` | `trace-baseline-only` | `scope.identity` | `scope.run_state`, `scope.waveform_source`, `scope.waveform_mode`, `scope.query_response_header`, `scope.waveform_format`, `scope.waveform_byte_order`, `scope.waveform_points`, `scope.waveform_transfer_window` | — / — | `acquisition_state`, `temporary_transfer_setup`, `binary_response` | `operation.timeout_ms=60000` | `8388608 / 67108864 / 256 / 65536` | `disabled` | `read_write` | `ScopeService.fetch_trace(source)` / `wavebench scope trace fetch` / `trace`、`metadata`、`integrity`、`error_check` |

R1.3 acceptance addendum 固定下列数值；表中 binary 列依次为 response、operation total、query
count、resynchronization bytes：

```python
SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES = 8_388_608
SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES = 8_388_608
SCOPE_SCREENSHOT_BINARY_QUERY_MAX_COUNT = 1
SCOPE_SCREENSHOT_BINARY_RESYNCHRONIZATION_MAX_BYTES = 0

SCOPE_TRACE_BINARY_RESPONSE_MAX_BYTES = 8_388_608
SCOPE_TRACE_BINARY_OPERATION_MAX_BYTES = 67_108_864
SCOPE_TRACE_BINARY_QUERY_MAX_COUNT = 256
SCOPE_TRACE_BINARY_RESYNCHRONIZATION_MAX_BYTES = 65_536

SCOPE_PROFILE_OPERATION_TIMEOUT_MS = 5_000
SCOPE_SCREENSHOT_OPERATION_TIMEOUT_MS = 5_000
SCOPE_ACQUISITION_OPERATION_TIMEOUT_MS = 30_000
SCOPE_TRACE_OPERATION_TIMEOUT_MS = 60_000
```

这些值是核心上限，不是 driver 默认值；descriptor/profile/connection 只能收紧，不能提高。
`scope.screenshot_v2` 的 R1.1 单次 PNG 上限为 8 MiB，与 trace/waveform 的单响应核心上限一致。
该值覆盖已文档化的 `387,356`-byte TMC definite-block 截图示例，并为常见高分辨率 PNG 保留余量；
它不构成所有型号、固件、颜色或菜单状态的吞吐承诺。单次 query、
零 resynchronization 和 5 秒 operation timeout 不变；具体 descriptor profile 仍须按设备证据收紧。
`scope.fetch_trace` 每次 response 上限为 8 MiB、一次 operation 总上限为 64 MiB、最多 256 次
binary query，并允许最多丢弃 64 KiB 以证明边界。超出 resynchronization 上限或无法证明边界时，
核心固定关闭 transport 并将 session 标记为 `poisoned`，不由 backend 自行选择 close/poison
策略。

`OperationRequest.deadline` 固定为单调时钟的绝对时间。未提供调用方 deadline 时，核心使用上表
operation timeout；调用方提供更早 deadline 时只能收紧，不能延长。每次 I/O 的 timeout 为
`min(connection.timeout_ms, deadline - monotonic_now)`，剩余时间不足 1 ms 时在发送前以
`deadline_exhausted` 拒绝。profile source 固定采用 descriptor-first：descriptor 必须提供安全
上限；仪器查询只能形成 `combined` 交集并收紧，`queried`-only profile 在 R1.3 拒绝。

`ErrorCheckSpec.timing` 的默认值固定为 `before_and_after`；screenshot、acquisition 和 trace
operation 只能使用该默认值或显式收紧为 `before`/`after`，driver 不得另行决定 timing。recovery
phase 固定为 `disabled`。文本 query timeout、binary timeout 和恢复 timeout 都受同一 operation
deadline 限制。

`scope.screenshot_v2` 采用保守的 `write` effect，因为某些设备需要临时写菜单或颜色设置；
只支持 `device` 行为的设备也不能在 profile 中把该 operation 降级为无状态副作用；它仍按
`write` gate 执行。若未来需要真正的 read-only screenshot operation，应新增独立 operation
和 capability，不能由 profile 动态改变 effect。`scope.fetch_trace` 同理，默认按可能修改
transfer 选择处理。

采集控制按动作拆成三个静态 operation：`scope.acquisition_start`、
`scope.acquisition_single` 和 `scope.acquisition_stop`。它们共享
`scope.acquisition_control` capability，但不得用一个带 `action` 参数的 operation 动态改变
`effect`、changed fields、恢复覆盖或最低 access。`stop` 的公共入口和超时 cleanup 入口仍
共享同一 driver 方法，但核心发放的 normal/recovery authorization 必须分别标识用途。

三个控制 operation 的成功 postcondition 与失败 cleanup 必须分开：`start` 成功后有意保留
连续运行状态，`single` 成功后保留新记录已经完成的停止状态，`stop` 成功后有意保留停止
状态。`failure-cleanup-only` 只描述写入失败、等待超时或取消后的 cleanup，不得在成功
路径把 run state 恢复到调用前。`postcondition_fields` 在成功路径证明目标状态，
`cleanup_verification_fields` 在失败路径证明 cleanup 结果；artifact 必须用
`postcondition` 与 `cleanup` 两个字段区分。两者都不得借用现有 `verification_fields`
向 session 伪造 baseline 恢复证据。
对 `start` 和 `single`，失败 cleanup 在 session 仍可安全写入时必须先 STOP，再恢复并
query-back 写入前的 `scope.trigger` 和 `scope.acquisition` baseline。只有 run state、trigger 和
acquisition 全部验证成功时 cleanup 才是 `succeeded`；任一字段未恢复或未证明时，
session 不得回到 `healthy`。

`scope.screenshot_v2`、`scope.acquisition_start`、`scope.acquisition_single`、
`scope.acquisition_stop` 和 `scope.fetch_trace` 应把
`scope.error_drain_v1` 放入 `optional_capabilities`，是否变成当前请求的必需能力由第六节的错误策略
和 `OperationSpec` 最低策略共同决定。profile、运行状态和 metadata 查询默认不触发错误队列，
避免一个只读观测产生额外的 consumptive read。

表中的 `scope.error_queue` 只在有效 error policy 实际执行完整 drain 时产生；如果 policy 为
`disabled`，该字段不发生仪器变化，但 operation artifact 仍必须记录 `error_check.status`。
核心实现可以用保守的静态 changed field，也可以在冻结 action-specific spec 后使用条件字段，
但不得把 consumptive error read 隐藏在普通 query 统计中。

波形 transfer 字段使用协议无关的核心名称；例如某些示波器的 `CHDR` 响应头、`CORD` 字节序、
`WFSU` 格式/宽度/点数/窗口都必须映射到上述 `query_response_header`、`waveform_byte_order`、
`waveform_format`、`waveform_points` 和 `waveform_transfer_window`。只在
`changed_fields` 中列出「transfer」而不在 `verification_fields` 中逐项闭合，不满足本 RFC；
任一项恢复后无法由核心 verifier 证明时，operation 必须 fail-closed，不能因为
`restore_coverage="capture-baseline-only"` 就回到 `healthy`。
同一字段集也适用于现有 `scope.fetch_waveform`、`scope.capture`、
`scope.capture_waveforms` 和 `scope.capture_multiple` 的核心规格；新 RFC operation 不能因为
换成 `ScopeTraceData` 就缩小既有 transfer 恢复/验证要求。

现有 capture 若需要调用旧 `scope.screenshot`，R1.3 只允许「父 operation 字段闭包」方案：
父 `scope.capture_waveform`、`scope.capture_waveforms` 或 `scope.capture_multiple` 的静态
`OperationSpec` 必须显式携带 `ScopeEmbeddedScreenshotContract`，并加入
`scope.display_menu`、`scope.display_color`、`output.screenshot` 的 changed/verification/cleanup
字段，在同一 operation context 内按 screenshot phase 执行
snapshot、capture、restore、verify。R1.3 不定义 composite operation，也不允许在父 operation
外单独开启 screenshot authorization；旧 capture 若没有这组字段闭包，必须在任何 I/O 前以
`unsupported_capability` 拒绝 screenshot 请求。旧 `scope.screenshot_png()` 不能绕过该规则。

嵌入截图的失败语义固定为 `fail_parent`：截图 transport、PNG 校验、after error 或 restore/
verify 失败都会使父 capture operation 失败；已经取得的 waveform 只能作为脱敏诊断摘要，
不得作为成功值返回。父 operation 只执行一次 error policy，artifact 在父记录下增加
`screenshot.status`、`screenshot.failure_reason`、`screenshot.cleanup` 和 `screenshot.verification`，
不创建第二个 operation artifact。
当前核心保留 `scope.screenshot` 的 legacy capture 行为。插件同时声明旧能力和
`scope.screenshot_v2` 时，旧 capture 仍只调用 legacy 路径，不会嵌入 v2 截图；插件只有
`scope.screenshot_v2` 而没有旧能力时，旧 capture 的截图请求会在仪器 I/O 前拒绝，并提示使用
独立 `scope screenshot capture`。只有父 operation 字段闭包完整实现后，才能让旧 capture
直接嵌入 v2 截图。

### 1.3 输入、前置条件与输出

为避免只有 operation 名称而没有可执行边界，R1.3 规定以下最小 schema 和 Python Protocol。
核心 `0.8.23` 已冻结 Python Service、CLI JSON artifact、取消、幂等性和并发边界：

| operation | 输入 | 主操作写入 / binary 前置条件 | 成功输出 |
| --- | --- | --- | --- |
| `scope.screenshot_profile` | 无 | identity 已验证；profile source 可用 | `ScopeScreenshotProfile` |
| `scope.screenshot_v2` | `ScopeScreenshotRequest`、operation 级 error policy | profile 精确匹配；access 允许写入；必要的 baseline 可读 | `ScopeScreenshot` + effective request |
| `scope.acquisition_run_state` | 无 | identity 已验证；session healthy | `ScopeAcquisitionRunState` |
| `scope.acquisition_start` | `ScopeContinuousAcquisitionRequest`、operation 级 error policy | identity 已验证；phase 属于 `stopped/ready/complete`；descriptor 的 `scope_extensions.acquisition_control_profile` 已验证并支持请求 mode；trigger/acquisition baseline 可读；access 允许写入 | `ScopeAcquisitionRunState`，trigger mode 等于请求，phase 为 `ready/arming/waiting/acquiring/rolling` |
| `scope.acquisition_single` | operation 级 error policy | identity 已验证；phase 属于 `stopped/ready/complete`；descriptor 的 `scope_extensions.acquisition_control_profile` 已验证；trigger/acquisition baseline 可读；access 允许采集 | `ScopeAcquisitionCompletion`；失败则 cleanup diagnostics |
| `scope.acquisition_stop` | operation 级 error policy | normal 路径要求 identity 已验证、session healthy 且 phase 非 `unknown/error`；recovery 路径使用独立 core authorization | `ScopeAcquisitionRunState`，postcondition 为 `stopped`；recovery 另记 cleanup |
| `scope.trace_metadata` | 有效 `ScopeTraceRef` | source/index/name 不变量通过；identity 已验证 | `ScopeTraceMetadata` |
| `scope.fetch_trace` | `ScopeTraceRef`、points profile、operation 级 error policy | source 已配置；sequence/segmentation 与 profile 兼容；必要时 acquisition stopped | `ScopeTraceData` + integrity/error artifact |

纯参数错误、能力不支持和 access 拒绝 MUST 在任何仪器 I/O 前返回。phase、baseline
和 query-back 等仪器状态前置条件可以使用核心签发的有界 preflight/verification authorization
执行只读查询，但必须在主 operation 的任何写入或 binary query 前完成。设备返回错误、
传输失败和完成证据不足则归入对应 operation result/exception，不得伪装成参数错误。
`scope.screenshot_v2`、三个 acquisition control operation 和 `scope.fetch_trace` 都从统一的
`OperationRequest.error_check` 解析单 operation 覆盖；若未提供，按第六节优先级解析全局和
`OperationSpec` 策略。driver 方法不再接受另一个独立策略来源。

### 1.4 请求、结果和异常边界

候选核心应为每个 operation 生成可审计的请求和结果，至少包含：

```python
@dataclass(frozen=True)
class OperationRequest:
    operation_id: str
    arguments: Mapping[str, Any]
    deadline: float | None
    correlation_id: str
    error_check: ErrorCheckSpec | None

@dataclass(frozen=True)
class OperationResult:
    value: object
    diagnostics: Mapping[str, Any]
    observed_state: Mapping[str, Any] | None
```

`OperationRequest.deadline` 使用单调时钟的绝对 deadline；artifact 只记录剩余时长或
`deadline_source`，不记录进程时间戳。`correlation_id` 在一次 Service operation 及其
recovery/verification phase 中保持不变，便于把 cleanup 和错误检查归到同一操作。

`operation_id` MUST 使用稳定的小写点分隔名称；版本变化通过 capability/core version gate
处理，不在同一名称下改变输入或输出语义。异常至少区分：

- `unsupported_capability`：descriptor 明确没有能力；
- `unknown_capability`：核心或插件没有完成能力发现；
- `precondition_failed`：前置条件在任何写入和 binary 查询前失败；
- `access_denied`：访问策略不允许；
- `transport_io_error`：传输阶段失败，沿用现有结构化 framing、同步和 retry 字段；
- `completion_unproven`：操作可能已执行，但没有足够证据证明完成；
- `instrument_error`：设备明确返回错误。

### 1.5 恢复与 artifact 规则

所有可能写入仪器的 R1.3 operation MUST 在 `OperationSpec` 中声明 changed fields 和恢复覆盖。
需要恢复 baseline 的字段写入 `verification_fields`；有意保留的成功状态和失败 cleanup
目标分别写入 `postcondition_fields` 和 `cleanup_verification_fields`。主异常不得被恢复异常覆盖；
结果或失败 artifact 应记录：

```text
operation
correlation_id
requested_arguments（去除敏感值）
observed_state_before / observed_state_after
session_health_before / session_health_after
baseline.kind / baseline.context_id / baseline.session_epoch / baseline.nonce_digest /
baseline.fields / baseline.restore_order / baseline.consumption
postcondition.status / postcondition.reason_code / postcondition.observed_fields
completion_proof.proof / completion_proof.original_state / completion_proof.proof_baseline_state /
completion_proof.proof_baseline_stage / completion_proof.observed_states（仅 SINGLE）
cleanup.attempted / cleanup.restore / cleanup.verification / cleanup.error_code / cleanup.observed_fields
error_check
```

`postcondition` 必须由 operation-specific result verifier 产生，不得把 driver 返回非空当作验证。
`scope.acquisition_single` 的 completion artifact 必须保留脱敏 baseline、完整的记录状态序列
和 proof 分支；其他 action 不生成伪造的 completion 字段。

artifact 只记录 framing、长度、媒体类型、状态 token 和摘要，不记录图片、原始波形、真实
resource、序列号或完整命令 payload。

### 1.6 非嵌套 operation context 与阶段授权

本轮增补选择「单个 operation context 下的顺序阶段授权」，不选择嵌套 session
authorization。候选核心内部模型为：

```python
OperationPhase = Literal[
    "preflight",
    "error_before",
    "main",
    "success_restore",
    "error_after",
    "failure_cleanup",
    "cleanup_verification",
]

@dataclass(frozen=True)
class _CoreOperationContext:
    context_id: str
    operation_id: str
    correlation_id: str
    session_epoch: str
    deadline: float
    binary_budget_ledger_id: str | None

BaselineNonce = str

@dataclass(frozen=True)
class _CoreBaselineUseRecord:
    context_id: str
    session_epoch: str
    baseline_nonce: BaselineNonce
    state: Literal["fresh", "passed_to_main", "restore_attempted", "consumed"]
```

operation context 只是核心 coordinator 持有的生命周期和额度账本，不是 session
authorization，创建它不会发送 I/O，也不会占用核心现有的 active authorization 槽位。
核心在 access、capability、lease 和静态 profile 校验通过后，在第一次仪器 I/O 前创建一次
context；同一公共 operation 不得创建第二个 context 来重置 deadline、correlation 或 binary
budget。

baseline 是 context-owned handle，不是 driver 可自行生成的状态快照。核心为每个可恢复操作
生成一次不可预测的 `baseline_nonce`，并把 `context_id`、当前 `session_epoch` 和 nonce 写入
baseline。nonce 只在该 context 的规定阶段内有效；核心在 baseline 传入 main 后标记
`passed_to_main`，首次进入 success/failure restore 后标记 `restore_attempted`，随后标记
`consumed`。任何 context、epoch、nonce、phase 或消费状态不匹配都必须在 driver I/O 前拒绝，
不得以同一连接 epoch 中的旧 baseline 重放。artifact 只记录 nonce 的脱敏摘要和消费状态，
不记录原值；driver 不得复制、替换或持久化 nonce。

需要动态 I/O 授权的每个阶段使用一个独立、有界的 session authorization；`main` 阶段
沿用该公共 operation 已有的 `normal` gate，不在其中再开子授权。候选 phase 与当前
`SessionPurpose` 的映射固定为：`preflight`/`error_before`/`error_after`/`cleanup_verification`
使用只读 `verification` 语义，`success_restore`/`failure_cleanup` 使用有界 `recovery` 语义，
`main` 使用既有 `normal` operation gate，但该 gate 的授权记录仍必须绑定本 context 的
`context_id`、epoch、字段和 deadline；不是重新开启一个脱离 context 的 normal operation。
若核心为 phase 增加专用枚举，必须保持同样的 I/O 白名单和非嵌套约束；driver 永远不接收
或构造 authorization token。

上述阶段遵守以下硬约束：

1. 开启新阶段前，上一阶段 authorization MUST 已关闭；任意时刻最多只有一个 active
   authorization。阶段执行器不得从 active authorization 内再签发子 authorization。
2. 每个 authorization 必须绑定 `context_id`、`session_epoch`、phase、字段白名单、
   allowed I/O、剩余 deadline 和静态 `max_steps`；阶段切换不得改变 access/lease 结论。
3. `BinaryQueryBudget` 的可变账本归 operation context 所有，不归某个阶段 authorization
   所有。只有静态 phase contract 允许 binary query 时，guarded transport 才能在 active
   authorization 中引用同一 `binary_budget_ledger_id`。创建、关闭或重试 phase 都不得
   重建账本、增加额度或退回已消耗的 query count。
4. session 进入 `uncertain/poisoned` 后，binary ledger 立即变为不可用，但已消耗计数保留到
   operation artifact 完成。只有静态声明的 `failure_cleanup`/`cleanup_verification`
   recovery 阶段可继续；它们不得使用 binary I/O。
5. context 在成功结束、主失败及全部允许的 cleanup/verification 结束、取消且无可执行
   recovery，或 session epoch 改变时进入 terminal。进入 terminal 后所有阶段 token 和 binary
   ledger 失效，不得跨 operation 或跨 epoch 重用。

阶段与授权、binary 的固定对应关系为：

| phase | session authorization purpose | 允许的 I/O | binary ledger |
| --- | --- | --- | --- |
| `preflight` | `verification` | 只读状态/profile query | 不可用 |
| `error_before` / `error_after` | `verification` | `scope.error_queue` 的文本 query | 只关联同一 context；禁止 binary 且不扣额度 |
| `main` | 既有 normal operation gate | 由 `OperationSpec` 静态声明的 write/acquire/text/binary | 若允许 binary，只能引用同一 ledger |
| `success_restore` / `failure_cleanup` | `recovery` | 有界文本 STOP、state restore write | 不可用 |
| `cleanup_verification` | `verification` | 有界文本 query-back | 不可用 |

phase 与当前核心 session API 的衔接冻结为一个核心内部 coordinator，而不是让 driver 直接
调用 `SessionTransactionCoordinator.authorize()`：

```python
ScopePhasePurpose = Literal["normal", "recovery", "verification"]

@dataclass(frozen=True)
class ScopePhaseAuthorizationSpec:
    context_id: str
    operation_id: str
    phase: OperationPhase
    purpose: ScopePhasePurpose
    allowed_io: frozenset[str]
    fields: frozenset[str]
    deadline: float
    max_steps: int

class ScopeOperationContextCoordinator(Protocol):
    def authorize_phase(
        self,
        spec: ScopePhaseAuthorizationSpec,
    ) -> Iterator["ScopePhaseAuthorization"]: ...
```

`ScopeOperationContextCoordinator` 的实现规则固定为：`purpose="recovery"` 或
`"verification"` 时，顺序调用当前 `SessionTransactionCoordinator.authorize()`；
`purpose="normal"` 时复用现有公共 operation gate。核心在两条路径都维护同一个
`_CorePhaseAuthorizationRecord(authorization_handle_id, context_id, session_epoch, phase, purpose,
fields, allowed_io, deadline, max_steps)` 侧记录；`authorization_handle_id` 是核心生成的
opaque 一次性 handle，侧记录以该 handle 对应的 authorization object identity 绑定，并在
authorization 关闭后才允许下一 phase。若当前
`SessionAuthorization` 对象没有这些字段，侧记录必须以不可伪造的 opaque authorization
identity 绑定；不能仅用 `operation_id` 或 `session_epoch` 推断 context。核心必须先扩展或
包裹现有 gate，再开始任何新 capability 的实现；driver 只接收已授权的 transport facade，
不接收 `ScopePhaseAuthorization` 或 session token。

因此 `error_before`/`error_after` 即使拥有独立的顺序 authorization，也不会获得新的
binary 权限或额度；它们只携带同一 `context_id`/`binary_budget_ledger_id` 的审计关联，账本
计数保持不变。阶段切换时核心先关闭当前授权，再签发下一阶段授权；若当前核心的
`authorize()` 只能接受 `recovery`/`verification` purpose，则 `main` 继续使用既有 normal
operation gate，其他 phase 按上表映射，不要求核心接受嵌套授权。

首版操作的阶段顺序为：

| operation 类型 | 成功顺序 | 主操作/after 失败顺序 |
| --- | --- | --- |
| acquisition start/single | `preflight -> error_before? -> main -> error_after? -> terminal` | 关闭当前 phase 后 `failure_cleanup -> cleanup_verification -> terminal` |
| screenshot with state changes | `preflight -> error_before? -> main -> error_after? -> success_restore -> cleanup_verification -> terminal` | 关闭当前 phase 后 `failure_cleanup -> cleanup_verification -> terminal` |
| fetch/trace vendor transaction | `preflight -> error_before? -> main -> error_after? -> terminal` | 关闭当前 phase 后执行该 operation 已声明的非嵌套 restore/verification phase，然后 terminal |

`?` 表示 error policy 决定该阶段是否存在，不表示插件可自行跳过。before/after error
executor 只是同一 context 中的独立顺序 phase，不是父/子 authorization。
artifact MUST 记录每个 phase 的开始/结束、allowed I/O、实际 step 数、budget 前后摘要和跳过原因；
不记录 opaque token 本身。

## 二、binary framing 与 message 语义

### 2.1 两层 framing

首版只接受两种可证明的 transport response framing：

```python
class BinaryResponseFraming(str, Enum):
    DEFINITE_BLOCK = "definite_block"
    MESSAGE = "message"
```

- `DEFINITE_BLOCK`：应用层响应以 IEEE 488.2 `#N` 头声明 payload 长度；
- `MESSAGE`：backend 通过 EOI、VISA message END 或等价的明确事件报告一次响应结束。

底层 socket 的多次 `recv()`、串口暂时没有数据、读取 timeout 和换行都不是通用 message
边界。transport 不解析 PNG、波形或其他媒体；内容校验由 driver 完成。

### 2.2 definite block 的精确语法

候选 transport MUST 按以下语法解析：

```text
response := '#' + N + length_field + payload
N        := ASCII digit '1'..'9'
length_field := exactly N ASCII decimal digits
payload  := exactly the declared number of bytes
```

`#0`（indefinite block）、缺失长度位、非 ASCII 数字、长度溢出、声明长度与已消费字节不一致
都必须结构化失败。`#10` 表示零长度 payload 的语义是否允许由 operation profile 决定；不允许
把 `#0` 当作零长度 definite block。解析前必须校验长度上限，不能先按声明长度分配无限内存。

### 2.3 `query_binary` 与上限

候选公共方法为：

```python
# 概念模型：这是核心内部状态，不是公共构造函数。
class _CoreBinaryQueryBudget(Protocol):
    ledger_id: str
    context_id: str
    operation_id: str
    correlation_id: str
    per_response_max_bytes: int
    remaining_operation_bytes: int
    remaining_query_count: int
    resynchronization_max_bytes: int
    transport_trailing: bytes
    expires_at: float  # monotonic clock 的绝对时间


@dataclass(frozen=True)
class BinaryQueryResult:
    data: bytes
    framing: BinaryResponseFraming
    declared_length: int | None
    framing_header_bytes: int
    consumed_bytes: int
    transport_trailing_bytes: bytes
    synchronization: Literal["proven"] = "proven"


def query_binary(
    self,
    command: str,
    *,
    framing: BinaryResponseFraming,
    max_bytes: int,
    replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
) -> BinaryQueryResult: ...
```

上面的 `_CoreBinaryQueryBudget` 是描述核心内部授权状态的概念模型，不是插件可实例化的公共输入；
文中简称 `BinaryQueryBudget`。核心只向 guarded transport 传递不可伪造的 opaque token，不把
这些字段暴露给插件作为可修改对象。
核心 coordinator 为一次 operation context 创建一次 budget ledger。session health 改变时立即
禁止新 binary I/O，但 ledger 保留已消耗计数，直到第 1.6 节定义的 context terminal 时才失效；
transport 不得接受跨 context、跨 operation/correlation、跨 session epoch 或过期的 budget。
`per_response_max_bytes` 限制每次 response；`remaining_operation_bytes` 限制一次 operation
中所有 binary response 的累计 payload；`remaining_query_count` 防止通过大量零长度响应绕过资源
边界；`resynchronization_max_bytes` 限制失败后为证明同步而额外丢弃的字节。这些值都是核心
在 operation 开始前计算的有限整数；前三者为正，resynchronization 可为零。多次 query 不能把额度累加成更大的单次
response 上限，也不能通过重建 driver 对象重置任何额度。

`max_bytes` MUST 是核心签发的 `BinaryQueryBudget.per_response_max_bytes` 范围内的正整数；
它计量单次 response 的 payload 字节数，不包含 framing header。未配置 connection 上限时，
核心只在内部最小值计算中把对应 connection 项视为 `+∞`；operation 的 response 和 total
上限仍必须有限，公共方法不提供无限读取默认值。Service 分别计算：

```text
effective_response_max = min(spec response, profile response, connection response)
effective_operation_max = min(spec total, profile total, connection total)
effective_query_max_count = min(spec query count, profile query count, connection query count)
effective_resynchronization_max = min(spec resync, profile resync, connection resync)
```

profile 或 connection 没有对应限制时只是不进一步收紧，不能取消 spec 的有限值。
Service 在第一个阶段授权前向 operation context 安装与 context/operation/correlation/epoch
绑定的 opaque budget ledger。没有 ledger 或 active phase 不允许 binary I/O 的新
`query_binary()` 调用必须在 `BEFORE_SEND` 以 `NOT_SENT` 拒绝。现有 `query_bin_block()` 保留
为 definite block 兼容入口，只允许没有 active binary budget 的 legacy operation。在已安装 R1.3
budget 的新 operation 中，它必须以 `binary_legacy_entry_unsupported` 在发送前拒绝，不得消耗、
重建或模拟 response/operation/query/resync budget。新 operation 若没有 R1.3 budget，
`query_binary()` 同样在发送前拒绝。

如果 operation 使用 binary profile，则每个参与的 variant 必须提供有限正整数的 response、
operation-total 和 query-count，以及有限非负整数的 resynchronization 限制；没有 profile 层的 operation 直接使用
spec 与 connection 的最小值。
显式 connection 限制使用同一数值规则。上限比较和计数使用整数 bytes，不能用浮点近似
或按样本点数替代。
每个 core-validated profile variant 只能声明一个精确 transport trailing byte sequence；
R1.3 暂定最长 16 bytes，不允许备选集合、正则、回调或「任意空白」。这避免空字符串
与非空 terminator 形成前缀歧义，导致 backend 在 declared payload 后继续阻塞或吞掉下一响应。
MESSAGE 只允许空 transport trailing。核心把已验证的精确 sequence 放入 opaque budget，
插件不得通过 `query_binary()` 参数临时放宽。

实现时必须同时修改 `InstrumentTransport` Protocol、全部 backend、
`GuardedAuditedTransport`、session `_AUTHORIZED_IO` / `_VERIFICATION_IO`、审计计数器和
结构化错误映射。`BinaryQueryBudget` 的创建、消费和失效只能由核心 coordinator 完成；
只给某个 backend 增加方法会绕过核心会话授权，不符合候选合同。

budget 不作为 `query_binary()` 的插件参数。核心 coordinator 在 operation context 中安装
opaque ledger；每个允许 binary I/O 的非嵌套 phase authorization 只引用它的
`ledger_id`。`GuardedAuditedTransport.query_binary()` 同时验证 active phase、context/epoch 和
ledger 绑定，再把已计算的有效上限传给 backend。插件只提交
`max_bytes <= per_response_max_bytes` 的收紧值；它无法看到、构造或替换 token。guarded
transport 在同一 transaction lock 内计算
`effective_query_max = min(max_bytes, per_response_max_bytes, remaining_operation_bytes)`；结果不为正整数或 token
失效时，在发送前以 `NOT_SENT` 拒绝。guarded transport 在发送前还必须原子预留一个
`remaining_query_count`；额度为零时不发送。backend 必须以该有效值执行单响应限制；
成功后再原子扣减 `len(result.data)`，不计 framing header 和已明确限定的 transport trailing。
扣减前 guarded transport 必须再验证 `len(result.data) <= effective_query_max`、下文的
精确 consumed 等式和 transport trailing allowlist；backend 返回越界的「成功」结果必须转为
合同违反并关闭/毒化 session，不得只扣成负额度后交给调用方。
任何失败一旦已发送 query，都不退回预留的 query count。阶段关闭、失败或切换也不退回
任何额度。核心测试必须证明 `error_before -> main -> error_after` 三段使用同一 ledger，
且尝试在新 phase 重建 ledger 会在任何 I/O 前拒绝。

### 2.4 message boundary 的能力证明

`binary.message_boundary` 是「backend 类型 + 具体 resource/session 能力」的联合声明：

| backend | 可声明条件 | 不能作为证据的行为 |
| --- | --- | --- |
| PyVISA | 具体 resource 能报告 EOI/message END，且同一锁内可恢复 read termination | 仅有 `read_raw()` 或一次成功的 timeout 读取 |
| RsInstrument | API 明确报告完整 message/EOI，并能在异常后报告同步状态 | 任意 `query_bin_block()` 成功 |
| TCP socket | 协议本身声明长度、EOM 或受控 message API | `recv()` 返回短块、idle timeout |
| serial | 只有设备和 backend 共同声明可证明的 EOM 才能提供 | 暂时没有数据、换行或固定延迟 |

R1.3 暂不批准任何现有 backend 声明 `binary.message_boundary`；上表是进入 conformance fixture
前必须满足的条件，不是能力白名单。

能力可在 open 时由 backend 静态类型和 resource 属性共同确定；若 backend 无法提供证明，
必须在发送命令前拒绝 `MESSAGE`，不得通过探测命令猜测。`MESSAGE` 交换应在同一资源锁内
临时关闭文本 read termination，完成或失败后恢复原设置；恢复失败必须进入 session health
状态机，不能当作普通 driver 解析错误。
R1.3 acceptance policy 固定为：termination/read-setting 恢复失败、malformed framing、
同步未知或超出 resynchronization ceiling 时关闭该 transport 并标记 `poisoned`；只有已经
证明边界且完成设置恢复的有限失败才允许保留 `uncertain` session。backend 不得以自己的
错误分类或重连策略改变这一默认。

### 2.5 超限、部分响应和失步

每个 backend MUST 在返回前给出同步结论。规则如下：

1. definite block 头声明的长度超过 `effective_query_max` 时，backend 只有在到已声明
   payload 末尾的待丢弃字节数不超过 `resynchronization_max_bytes` 时，才能以有界流式读取
   消费到已证明边界，然后抛出 `TransportIOError(reason_code="binary_limit_exceeded")`。
   声明长度需要更多丢弃字节时必须立即关闭资源并标记 `poisoned`，不得为保持同步而无界读取。
2. message 读取超过上限时，只有在额外消费不超过 `resynchronization_max_bytes` 且 backend
   在此范围内报告已证明的 message END 时，才允许「有界消费后失败」；超过该范围仍未
   到 EOM 时必须终止资源并把 session 标为 `poisoned`。
3. timeout、部分响应、终止符恢复失败和 malformed header 必须映射到现有
   `TransportIOError` 的 `phase`、`response_progress`、`synchronization`、`attempts` 和
   `replay_policy` 字段；不得重试已经发送且同步未知的完整 query。
4. R1.3 候选为现有 `TransportIOError` 增加可选的稳定 `reason_code`、非负非 bool
   `consumed_bytes` 和 `discarded_bytes`。binary `reason_code` 固定为
   `binary_framing_error`、`binary_limit_exceeded`、`binary_truncated`、`binary_timeout` 或
   `binary_transport_trailing_error`。它们进入现有脱敏 error envelope，不新建携带 payload 的公共异常。
   `TransportIOError` 的构造、`with_attempts()` 复制和 envelope 序列化都必须保留这三个
   字段，不得在 retry 计数时丢失细分原因或字节证据。
   有界丢弃成功时 `synchronization=proven`，未证明时为 `unproven`，已确认失步或关闭时为
   `lost`；后两者分别使 session 至少进入 `uncertain` 或 `poisoned`。
5. R1.3 的 `query_binary(command=...)` 不接受 `ReplayPolicy.READ_CONTINUATION_ONLY`，因为该
   签名必然携带一个可能被发送的 command；必须在 `BEFORE_SEND` 阶段以 `NOT_SENT` 拒绝。
   未来若需要 continuation，应设计只接受 core-issued response token 的独立
   `read_binary_continuation(token, max_bytes=...)`，只消费同一响应且绝不重发 command。

definite block 已消费声明 payload 后发现额外字节时，额外字节不得被当作本次 payload，也
不得静默丢弃：只有在它们与 core-validated profile 中某个
`budget.transport_trailing` 精确匹配时，才能作为 `transport_trailing_bytes` 成功返回；
否则同步状态为 `LOST`，session 进入 `poisoned`。下一次 query 只有在
backend 明确保留并授权 continuation token 时才可继续。

成功返回使用 `BinaryQueryResult`，从而显式携带 declared/consumed/transport-trailing
和已证明同步。`data` 只包含 framing 定义的 payload/message。对 definite block，成功必须同时满足：

```text
declared_length == len(data)
framing_header_bytes == 2 + N
transport_trailing_bytes == budget.transport_trailing
consumed_bytes == framing_header_bytes + len(data) + len(transport_trailing_bytes)
```

`framing_header_bytes` 必须是非 bool 整数；definite block 中范围为 `3..11`，必须从实际读到的
`N` 保留，不得从 `declared_length` 反推，因为 length field 可含前导零。

对 MESSAGE，EOM 之前全部字节都属于 `data`；成功必须满足
`declared_length is None`、`transport_trailing_bytes == b""` 和
`framing_header_bytes == 0`、`consumed_bytes == len(data)`。MESSAGE 的媒体内容后缀仍属于 `data`，不得改名为 transport
trailing。任何 backend 在返回前无法
证明已到达响应边界，都不得构造 `BinaryQueryResult`；必须抛出结构化错误并把 session
标记为 `uncertain` 或 `poisoned`。因此 `synchronization="proven"` 是成功类型的先决条件，不是
backend 可以随意填写的默认值。
成功路径还必须写入 transport audit：`response_progress=complete`、
`synchronization=proven`、`consumed_bytes`、`transport_trailing_bytes` 长度和 framing；失败还必须记录
`consumed_bytes`、`discarded_bytes`、`reason_code` 和最终 session health，并继续使用
`TransportIOError`。现有 `query_bin_block()` 兼容入口只为 definite block 提取 `data`，但
仍保留 audit metadata。尾随字节不能用 `rstrip()` 静默删除。continuation token 的独立返回
模型仍是 `[OPEN]`，在此之前成功结果不得暗中留下待续读状态。

### 2.6 内容校验与应用层分块

PNG driver 负责 signature、chunk、IEND、尺寸、MIME type 和 profile 的精确 content-trailing
校验。SDS800X HD 的实测 raw PNG 在 IEND 后有一个尾字节；在 MESSAGE framing 中，
该字节仍位于 `BinaryQueryResult.data`。driver 只能在确认 IEND 后按
`ScopeScreenshotVariant.content_trailing_hex` 精确校验并从规范 PNG 结果中移除，不能把它当作
transport terminator 或 `transport_trailing_bytes`。

波形多块读取另行携带：

```text
record_id / acquisition_id
chunk_index
offset_samples
points
total_points（可选）
is_last
```

只有同一 `record_id`、连续 offset、唯一 chunk index 且最终点数一致时才允许拼接。不同
query 的结果不得无条件拼接；样本格式、字节序和 preamble 解码仍属于 waveform decoder。

## 三、screenshot profile

### 3.1 明确 request tuple

格式、菜单和颜色不是独立笛卡尔积。候选 profile 使用明确支持的 request tuple：

```python
ScreenshotMenuMode = Literal["device", "include", "exclude"]
ScreenshotColorMode = Literal["device", "color", "monochrome", "inverted"]
ScopeScreenshotStateField = Literal["scope.display_menu", "scope.display_color"]

@dataclass(frozen=True)
class ScopeScreenshotRequest:
    format: Literal["png"] = "png"
    menu_mode: ScreenshotMenuMode = "device"
    color_mode: ScreenshotColorMode = "device"

@dataclass(frozen=True)
class ScopeScreenshotVariant:
    request: ScopeScreenshotRequest
    media_type: Literal["image/png"]
    framing: BinaryResponseFraming
    response_max_bytes: int
    operation_max_bytes: int
    resynchronization_max_bytes: int
    changed_fields: tuple[ScopeScreenshotStateField, ...]
    restore_order: tuple[ScopeScreenshotStateField, ...]
    snapshot_max_steps: int
    restore_max_steps: int
    verify_max_steps: int
    query_max_count: Literal[1] = 1
    transport_trailing_hex: str = ""
    content_trailing_hex: str = ""
    width_px: tuple[int, int] | None = None
    height_px: tuple[int, int] | None = None

@dataclass(frozen=True)
class ScopeScreenshotProfile:
    variants: tuple[ScopeScreenshotVariant, ...]
    source: Literal["descriptor", "queried", "combined"] = "descriptor"

@dataclass(frozen=True)
class ScopeScreenshotStateSnapshot:
    captured_fields: tuple[ScopeScreenshotStateField, ...]
    menu_state_token: str | None = None
    color_state_token: str | None = None

@dataclass(frozen=True)
class ScopeScreenshotBaseline:
    context_id: str
    session_epoch: str
    baseline_nonce: str
    snapshot: ScopeScreenshotStateSnapshot
    restore_order: tuple[ScopeScreenshotStateField, ...]

@dataclass(frozen=True)
class ScopeScreenshotRestoreResult:
    status: Literal["completed", "failed", "not_attempted"]
    attempted_fields: tuple[ScopeScreenshotStateField, ...]
    restored_fields: tuple[ScopeScreenshotStateField, ...]
    error_code: str | None = None

@dataclass(frozen=True)
class ScopeScreenshotVerification:
    status: Literal["verified", "mismatch", "unavailable"]
    verified_fields: tuple[ScopeScreenshotStateField, ...]
    mismatched_fields: tuple[ScopeScreenshotStateField, ...]
    error_code: str | None = None

@dataclass(frozen=True)
class ScopeScreenshot:
    data: bytes
    media_type: Literal["image/png"]
    width_px: int
    height_px: int
    requested: ScopeScreenshotRequest
    effective: ScopeScreenshotRequest
    framing: BinaryResponseFraming

@dataclass(frozen=True)
class ScopeEmbeddedScreenshotContract:
    request: ScopeScreenshotRequest
    changed_fields: tuple[ScopeScreenshotStateField, ...]
    verification_fields: tuple[ScopeScreenshotStateField, ...]
    cleanup_verification_fields: tuple[ScopeScreenshotStateField, ...]
    failure_policy: Literal["fail_parent"] = "fail_parent"
    artifact_key: Literal["screenshot"] = "screenshot"

class ScopeScreenshotDriver(InstrumentDriver, Protocol):
    def snapshot_screenshot_state(
        self,
        fields: tuple[ScopeScreenshotStateField, ...],
    ) -> ScopeScreenshotStateSnapshot: ...
    def capture_screenshot(
        self,
        request: ScopeScreenshotRequest,
        *,
        baseline: ScopeScreenshotBaseline | None,
    ) -> ScopeScreenshot: ...
    def restore_screenshot_state(
        self,
        baseline: ScopeScreenshotBaseline,
    ) -> ScopeScreenshotRestoreResult: ...
    def verify_screenshot_state_restored(
        self,
        fields: tuple[ScopeScreenshotStateField, ...],
        baseline: ScopeScreenshotBaseline,
    ) -> ScopeScreenshotStateSnapshot: ...
```

`variants` MUST 非空，每个 request 只能出现一次；R1.3 首版只接受 `png` / `image/png`。
两个 byte 上限 MUST 为正数，`resynchronization_max_bytes` MUST 为非 bool 非负整数；
descriptor variant 的 response/operation/query/resynchronization 值不得超过
`8388608/8388608/1/0`，connection 或更严格 profile 只能进一步收紧；
首版 screenshot 只允许一次
binary response，因此 `query_max_count == 1` 且
`operation_max_bytes == response_max_bytes`。尺寸范围的上下界必须为正数且满足
`min <= max`，固定尺寸使用
`min == max`。请求未精确匹配一个 variant 时在任何 I/O
前拒绝。这样不会把 format、menu、color、framing 和 media type 错当成独立笛卡尔积。
`device` 表示保留仪器当前行为，不等于 `include` 或 `exclude`。R1.3 public profile 必须
来自 descriptor，或来自 descriptor 与仪器查询结果的 `combined` 交集；`queried`-only
profile 在 capability discovery 时拒绝。查询结果不得扩大 descriptor 未声明的安全上限，
结果中的 `effective` 和 variant 必须一致。

声明 `scope.screenshot_profile` 或 `scope.screenshot_v2` 时，
`InstrumentDescriptor.scope_extensions.screenshot_profile` MUST 非空，并在 factory 阶段通过
全部静态不变量；`get_screenshot_profile()` 只能返回 descriptor 的原样事实或更严格的
`combined` 交集。profile provider 缺失、返回 `queried`-only 结果或试图扩大任一上限时，
capability discovery 必须 fail-closed。

state restore 字段使用以下不变量：

- `changed_fields` 唯一，只包含该 request tuple 实际会修改的 menu/color 字段；
- `restore_order` 必须与 `changed_fields` 集合相同且无重复；顺序是 driver 恢复写入的唯一事实源；
- `changed_fields` 为空时，三个 `*_max_steps` 必须全为 `0`；非空时，必须是
  `1..32` 的非 bool 整数，且分别覆盖 snapshot query、restore write 和 verify query 的
  最大实际 step 数；
- snapshot 的 `captured_fields` 必须恰好等于 variant `changed_fields`；menu/color token 必须在
  对应字段存在时非空，其他情况为 `None`。
- core-owned `ScopeScreenshotBaseline.restore_order` 必须是已验证 variant 的精确顺序，driver
  不得自行追加、删除或重排字段。

`transport_trailing_hex` 和 `content_trailing_hex` 都使用小写、偶数长度的精确十六进制，
每个解码后最长 16 bytes；前者只表示 definite-block framing 后的文档化 transport
terminator，MESSAGE 必须固定为 `""`。后者表示已位于 MESSAGE payload 或 definite-block
payload 内的应用内容后缀。driver 必须先完整验证 PNG 到 IEND，再要求 IEND 后字节与
`content_trailing_hex` 精确相等；不得使用 `rstrip()` 或任意 parser。`ScopeScreenshot.data`
只返回从 PNG signature 到 IEND 的规范 PNG，不包含 content trailing；artifact 只记录后缀
长度和 variant 标识。binary response/operation budget 按清理前的完整 payload 计数。

driver 负责格式签名和媒体类型一致性，核心负责上限、artifact 字段和 transport framing。
非 PNG 格式的完整校验由对应 format handler 定义，不能默认为「任意 bytes 都合法」。
成功结果的 `width_px` / `height_px` 必须非空、为正整数并落在 variant 的闭区间内；PNG
handler 必须从已校验的 IHDR 得到尺寸，不能只相信 driver 自报值。

### 3.2 状态副作用与旧接口

若 menu/color 设置会写入前面板状态，`scope.screenshot_v2` 必须记录对应 changed fields、
恢复覆盖和有效 request。只要 variant `changed_fields` 非空，核心必须在任何写入前调用
`snapshot_screenshot_state()`，校验返回值后构造 core-owned
`ScopeScreenshotBaseline(context_id, session_epoch, baseline_nonce, snapshot, restore_order)`，并把同一 baseline 传入
`capture_screenshot()`。driver 只能使用 baseline 中的已验证字段和顺序。
若 `changed_fields` 为空，baseline 固定为 `None`，三个 state recovery 方法不得发生 I/O。

无论截图成功、媒体校验失败或 transport 失败，只要 `changed_fields` 非空，核心都必须按
variant `restore_order` 调用 `restore_screenshot_state(baseline)`，再在独立只读阶段调用
`verify_screenshot_state_restored(changed_fields, baseline)`。restore result 只证明写入已完成；verify 方法必须
返回新查询的 `ScopeScreenshotStateSnapshot`，由核心与 baseline snapshot 逐字段比较并生成
`ScopeScreenshotVerification`。只有全部 changed fields 一致时才能报告恢复成功。主 driver 抛异常不会丢失
core-owned baseline。恢复或验证失败不覆盖主异常，但 session 不得保持 `healthy`，
主截图结果也不得伪装为成功。`changed_fields` 为空时，restore/verify 结果固定为
`not_attempted`/`unavailable` 的无 I/O 记录，不调用这两个需要 baseline 的方法。

`ScopeScreenshotRestoreResult.attempted_fields` 必须遵循 baseline 的 `restore_order` 前缀，
`restored_fields` 只能是已尝试字段的有序子序列；`status="completed"` 只在所有 changed
fields 均已写入时成立。restore/verify 抛出的 transport 或协议异常由核心规范化为
`failed`/`unavailable` 结果，核心不得把该结果当作 query-back 证据。

截图的执行顺序固定为：`preflight snapshot -> main capture -> error_after? ->
success_restore restore -> cleanup_verification verify`。主 capture 抛异常、PNG 校验失败或
`error_after` 失败时，主异常优先级最高，但仍必须转入 `failure_cleanup restore ->
cleanup_verification verify`；没有
changed fields 时整个 restore/verify 分支为零 I/O。核心只有在 restore result 完成且 verify
逐字段匹配时，才把 screenshot operation 标为恢复成功。

snapshot、restore 和 verify 分别只能在 `preflight`、`success_restore|failure_cleanup`
和 `cleanup_verification` 阶段授权中调用；每个授权使用 variant 已验证的 step 上限。
baseline 的 `context_id`、epoch 或 nonce 不匹配、阶段不匹配、nonce 已消费，或 driver 尝试
超出 fields/step 时，必须在越界 I/O 前拒绝。

这里的 snapshot → restore → verify 是可复用的 state-recovery 模式，不是 SDS800X HD 私有
命令合同：不同仪器只需为各自的字段集合定义 typed snapshot/baseline 和 token 编解码，核心
仍负责授权、字段比较、session health 与 artifact。后续 `CHDR`/`CORD`/`WFSU` 等 transfer
状态若需要写入，也应复用同一模式，而不是再发明只返回布尔值的 driver helper。

现有 `scope.screenshot` 仅在 profile 明确包含等价旧参数时适配；旧 driver 忽略
`include_menu` 的行为不能由通用 adapter 静默继承。无法证明参数已生效时必须报告
`unsupported_capability` 或 `precondition_failed`。

现有 DS1000Z driver 会丢弃 `include_menu`，因此不能宣称满足 `exclude`；RTM2000 driver
会写入菜单和颜色设置，因此迁移时必须把这两个字段纳入 changed/restore/verification。
两者不能共享一个不检查 effective request 的旧接口 adapter。

## 四、采集运行状态与控制

### 4.1 组合状态模型

`ScopeAcquisitionStatus` 继续表示平均和分段信息。运行状态使用独立模型，`AUTO/NORMAL/SINGLE`
是 trigger mode，不是 phase：

```python
ScopeAcquisitionPhase = Literal[
    "unknown",
    "stopped",
    "ready",
    "arming",
    "waiting",
    "acquiring",
    "rolling",
    "stopping",
    "complete",
    "error",
]

ScopeTriggerMode = Literal[
    "auto",
    "normal",
    "single",
    "roll",
    "unknown",
]

ScopeContinuousTriggerMode = Literal["auto", "normal", "roll"]
ScopeSingleBaselineStage = Literal["configured_pre_arm", "original_atomic_arm"]
ScopeSingleArmSemantics = Literal["configure_then_arm", "atomic_configure_and_arm"]
ScopeAcquisitionIdentitySemantics = Literal["unique_within_session_epoch", "unknown"]
ScopeAcquisitionSettingField = Literal["scope.trigger", "scope.acquisition"]
ScopeAcquisitionRestoreField = Literal[
    "scope.run_state",
    "scope.trigger",
    "scope.acquisition",
]
ScopeStateToken = str

@dataclass(frozen=True)
class ScopeAcquisitionControlProfile:
    supported_continuous_modes: tuple[ScopeContinuousTriggerMode, ...]
    single_arm_semantics: ScopeSingleArmSemantics
    arm_resets_acquisition_count: bool
    failure_restore_order: tuple[ScopeAcquisitionSettingField, ...]
    snapshot_max_steps: int
    restore_max_steps: int
    verify_max_steps: int
    identity_semantics: ScopeAcquisitionIdentitySemantics
    atomic_arm_preserves_count_mode_semantics: bool = False
    single_mode_readback_allows_terminal_stop: bool = False

@dataclass(frozen=True)
class ScopeAcquisitionControlSnapshot:
    run_state: "ScopeAcquisitionRunState"
    trigger_state_token: ScopeStateToken
    acquisition_state_token: ScopeStateToken

@dataclass(frozen=True)
class ScopeAcquisitionControlBaseline:
    context_id: str
    session_epoch: str
    baseline_nonce: str
    snapshot: ScopeAcquisitionControlSnapshot
    restore_order: tuple[ScopeAcquisitionRestoreField, ...]

@dataclass(frozen=True)
class ScopeBaselineRestoreResult:
    status: Literal["completed", "failed", "not_attempted"]
    attempted_fields: tuple[ScopeAcquisitionRestoreField, ...]
    restored_fields: tuple[ScopeAcquisitionRestoreField, ...]
    error_code: str | None = None

@dataclass(frozen=True)
class ScopeBaselineVerification:
    status: Literal["verified", "mismatch", "unavailable"]
    verified_fields: tuple[ScopeAcquisitionRestoreField, ...]
    mismatched_fields: tuple[ScopeAcquisitionRestoreField, ...]
    error_code: str | None = None

@dataclass(frozen=True)
class ScopeContinuousAcquisitionRequest:
    trigger_mode: ScopeContinuousTriggerMode

ScopeCompletionProof = Literal[
    "count_delta_with_epoch",
    "identity_delta",
    "state_transition",
    "single_mode_readback_then_stopped",
]

@dataclass(frozen=True)
class ScopeAcquisitionRunState:
    phase: ScopeAcquisitionPhase
    trigger_mode: ScopeTriggerMode
    raw_state: str
    acquisition_count: int | None = None
    counter_epoch: ScopeStateToken | None = None
    acquisition_identity: str | None = None

@dataclass(frozen=True)
class ScopeAcquisitionCompletion:
    state: ScopeAcquisitionRunState
    original_state: ScopeAcquisitionRunState
    proof_baseline_state: ScopeAcquisitionRunState
    proof_baseline_stage: ScopeSingleBaselineStage
    proof: ScopeCompletionProof
    baseline_count: int | None = None
    completed_count: int | None = None
    baseline_identity: str | None = None
    completed_identity: str | None = None
    observed_states: tuple[ScopeAcquisitionRunState, ...] = ()
    post_arm_trigger_mode: ScopeTriggerMode | None = None
```

`ScopeAcquisitionControlProfile` 是 descriptor 静态事实，不是 driver 在 operation 中自报的
动态结果。候选 `InstrumentDescriptor.scope_extensions.acquisition_control_profile` 字段在声明
`scope.acquisition_control` capability 时 MUST 非空；核心 factory 在第一次仪器 I/O 前
完成静态校验，并把已验证 profile 传给 Service preflight。R1.3 不允许仪器查询或
driver 返回值扩大 descriptor profile。

profile 不变量为：

- `supported_continuous_modes` 非空、唯一，且只包含 `auto/normal/roll`；
- 三个 bool 字段必须是真正的 `bool`；
- `failure_restore_order` 必须恰好各包含一次 `scope.trigger` 和 `scope.acquisition`；
  顺序是核心恢复授权与 driver 实现的唯一事实源；
- 三个 `*_max_steps` 必须是 `1..64` 的非 bool 整数；snapshot/verify 各至少覆盖 run state、
  trigger 和 acquisition 三次 query，`restore_max_steps` 至少覆盖 STOP 和两个设置恢复写入，
  但不因此允许超出实际授权 step 数；
- `single_arm_semantics="configure_then_arm"` 时
  `atomic_arm_preserves_count_mode_semantics` MUST 为 `false`；
- `single_arm_semantics="atomic_configure_and_arm"` 时，只有
  `atomic_arm_preserves_count_mode_semantics=true` 且 `arm_resets_acquisition_count=false` 时，
  operation verifier 才可以在原始状态确为 `trigger_mode="single"` 时把 count 作为辅助证据；
- `arm_resets_acquisition_count=true` 时任何 arm 路径都不得使用 `count_delta_with_epoch`；
  `identity_delta`、`state_transition` 和满足 RFC-0009 的 terminal STOP proof 仍各自按其条件校验。
- `identity_semantics="unique_within_session_epoch"` 只约束 `identity_delta`；`unknown` 时核心不得接受
  identity proof，但不改变 `state_transition` 或满足 RFC-0009 的 terminal STOP proof 的独立条件。
- `single_mode_readback_allows_terminal_stop=true` 只授权 RFC-0009 定义的终态 STOP proof；
  它不替代 SINGLE arm、count、identity 或失败恢复语义。

核心构造 `ScopeAcquisitionControlBaseline` 时必须把固定的
`("scope.run_state", *profile.failure_restore_order)` 写入 `restore_order`；baseline 中的顺序
与该展开结果不一致时在第一次 recovery I/O 前拒绝。`ScopeAcquisitionControlSnapshot` 的三个
字段必须始终齐全，因此 STOP、trigger 和 acquisition 的恢复/验证边界不会依赖 driver 临时
决定字段集合。

`ScopeStateToken` MUST 是经 driver 规范化的、无换行、不含 resource/序列号的安全 token；不得把
原始 SCPI、仪器地址或未脱敏响应放入 baseline。`context_id` 负责 operation 归属，
`session_epoch` 负责连接世代，`baseline_nonce` 负责一次性消费；三者任一不匹配都必须拒绝，
不得跨 epoch 或 context 重用。`ScopeBaselineRestoreResult` 和 `ScopeBaselineVerification` 是核心可审计的结果，
不是 driver 可以省略的布尔标记。

`ScopeBaselineRestoreResult.attempted_fields` 必须是 baseline `restore_order` 的前缀，
`restored_fields` 必须是其中的有序子序列且不得包含额外字段；`status="completed"` 仅在
所有必需字段均已写入时成立，任何中途失败都必须为 `failed` 并保留已完成字段。
driver 若在 restore/verify 中抛出 transport 或协议异常，核心必须把它规范化为对应的
`failed`/`unavailable` cleanup 结果并保留异常证据；不得因为 Protocol 返回类型存在就假定
调用一定返回。
核心不得把 `restored_fields` 当作 query-back 证据，必须以随后返回的 snapshot 生成
`ScopeBaselineVerification`。

`raw_state` MUST 是短、可打印、无换行 token；无法无损映射时使用 `unknown`，不能把相近
文字硬映射成 `stopped` 或 `complete`。`acquisition_count` 必须是非负、非 bool 整数；
`counter_epoch`（若有）必须是 `1..64` 个 ASCII safe-token 字符，并在同一次 operation
中保持可比较；
`acquisition_identity` 只能是经 driver 校验的短 token，不能包含 resource 或序列号。
`ScopeAcquisitionRunState` 只描述一次观察，不携带历史完成结论；completion proof 只存在于
`ScopeAcquisitionCompletion`，防止普通状态查询伪装成某次 SINGLE 已完成。
成功构造 `ScopeAcquisitionCompletion` 时，首先必须满足所有 proof 共享的终态不变量：
`state.phase` 属于 `complete/stopped`、`observed_states` 非空且最后一项等于 `state`。
然后再验证分支：`count_delta_with_epoch` 仅可作为带联合证据的 proof，要求两个 count 都存在、两个
`counter_epoch` 都存在且相等、满足本节比较规则，并且 `observed_states` 同时证明有效的
状态迁移；它不再是仅凭模差或正向差值的独立完成证明；
`identity_delta` 要求新旧 identity 都是 `1..64` 个 ASCII safe-token 字符且不同，
`state_transition` 要求保留本节的最小观察序列，且不依赖 count；若该分支同时携带 count，
仍必须提供未变化的 `counter_epoch`，否则核心必须忽略 count 并按纯状态迁移验证。任一终态或证据不完整只能抛出
`completion_unproven`，不得返回一个携带「不可用」proof 的成功对象。
`single_mode_readback_then_stopped` 只在 profile 明确 opt-in、SINGLE 写入后的 mode readback
为 `single`、最终 state 为 `stopped/single`、`observed_states` 恰为最终 state，且四个
count/identity proof 字段均为空时成立。该分支的顺序与插件采用门由
[RFC-0009](WaveBench_scope可移植性RFC-0009_SINGLE模式终态STOP证明.md)冻结；普通 state query
不能自行构造它。
`identity_delta` 也不能仅凭两个 token 不同就成立；核心只在已验证 descriptor profile 的
`identity_semantics="unique_within_session_epoch"` 时接受该 proof。`unknown` 或 profile
缺失时，即使 fixture 观察到 token 不同，也只能使用完整 `state_transition`，或拒绝完成证明。
`baseline_count`/`completed_count` 非空时必须分别等于
`proof_baseline_state.acquisition_count`/`state.acquisition_count`；identity 字段也必须与对应状态一致。
`original_state` 必须等于 core-owned baseline 中的 `snapshot.run_state`。不能在 completion 外
额外填一组更有利的 token 来通过 verifier。
forced trigger 是瞬时 action/event，不是可 query-back 的持久 `ScopeTriggerMode`，因此不进入
该枚举。执行强制触发时只在 operation artifact 记录 `trigger_action="force"` 及其完成证据，
不能把仪器随后返回的 mode 伪造成 `forced`。

### 4.2 状态迁移与操作语义

候选状态迁移至少包括：

| 操作/事件 | 允许起始 phase | 预期观察 | 失败语义 |
| --- | --- | --- | --- |
| `scope.acquisition_start`（driver: `start_continuous`） | `stopped`、`ready`、`complete` | 写入后回读 `ready`/`arming`/`waiting`/`acquiring`/`rolling` | 写后回读失败，保留 session health 和 cleanup 结果 |
| `scope.acquisition_single`（driver: `acquire_single`） | `stopped`、`ready`、`complete` | 记录基线，观察新采集状态，再到 `complete/stopped` 且有 proof | 只观察到 arm 不算成功；超时进入失败 cleanup |
| trigger accepted | `arming`、`waiting`、`ready` | `acquiring` 或 `complete` | 外部变化时回读为 `unknown` |
| acquisition complete | 已观察到 `arming`、`waiting`、`ready` 或 `acquiring` 中至少一个新采集状态；或满足 RFC-0009 的 profile-gated SINGLE mode-readback 条件 | `complete` 或 `stopped` 且有 completion proof | 轮询可以跳过瞬时 `acquiring`；只有原本已 `stopped` 不足以证明新采集完成 |
| normal `scope.acquisition_stop`（driver: `stop_acquisition`） | `stopped`、`ready`、`arming`、`waiting`、`acquiring`、`rolling`、`stopping` 或 `complete` | 回读 `stopped` | 幂等；回读失败时保留 `uncertain`/`poisoned` |
| recovery STOP | 核心已签发有界 recovery authorization 的 `healthy/uncertain` session；phase 可为 `unknown/error` | 只写 STOP 并回读 `stopped` | 不得向 `poisoned` session 发送；结果只记入 cleanup，不伪装成 normal success |
| 外部/设备错误 | 任意 | `error` 或 `unknown` | 必须重新查询确认，不能继续普通 I/O |

控制协议建议为：

```python
class ScopeAcquisitionRunStateDriver(InstrumentDriver, Protocol):
    def get_acquisition_run_state(self) -> ScopeAcquisitionRunState: ...

class ScopeAcquisitionControlRecoveryDriver(InstrumentDriver, Protocol):
    def snapshot_acquisition_control(self) -> ScopeAcquisitionControlSnapshot: ...
    def restore_acquisition_control(
        self,
        baseline: ScopeAcquisitionControlBaseline,
    ) -> ScopeBaselineRestoreResult: ...
    def verify_acquisition_control_restored(
        self,
        baseline: ScopeAcquisitionControlBaseline,
    ) -> ScopeAcquisitionControlSnapshot: ...

class ScopeAcquisitionControlDriver(
    ScopeAcquisitionRunStateDriver,
    ScopeAcquisitionControlRecoveryDriver,
    Protocol,
):
    def start_continuous(
        self,
        *,
        trigger_mode: ScopeContinuousTriggerMode,
        baseline: ScopeAcquisitionControlBaseline,
    ) -> ScopeAcquisitionRunState: ...
    def stop_acquisition(self) -> ScopeAcquisitionRunState: ...
    def acquire_single(
        self,
        *,
        baseline: ScopeAcquisitionControlBaseline,
        deadline: float,
    ) -> ScopeAcquisitionCompletion: ...
```

核心必须在主操作写入前调用 `snapshot_acquisition_control()`，校验返回值后自行构造：

```python
ScopeAcquisitionControlBaseline(
    context_id,
    session_epoch,
    baseline_nonce,
    snapshot,
    ("scope.run_state", *failure_restore_order),
)
```

driver 只看到已验证的
baseline，不能构造或修改 core-owned `session_epoch`。`start_continuous()` 和
`acquire_single()` MUST 使用核心传入的同一 baseline，不得在内部替换为无法审计的另一份
baseline。因此，即使主 driver 方法抛出异常，核心仍保留恢复所需的类型化状态。

`restore_acquisition_control()` 的固定语义是：先发送 STOP，再按
`failure_restore_order` 恢复
`scope.trigger` 和 `scope.acquisition`；不得恢复 snapshot 中的运行 phase。
`ScopeBaselineRestoreResult.restored_fields` 只表示恢复写入已完成，不是 query-back 证据。
核心必须随后使用独立只读阶段调用 `verify_acquisition_control_restored()`；成功需要
driver 必须返回新查询的 `ScopeAcquisitionControlSnapshot`，不得直接返回布尔结论。
核心 verifier 据此生成 `ScopeBaselineVerification`：`scope.run_state` 必须已为 `stopped`，
且 trigger/acquisition token 与 baseline snapshot 精确相等。只有
restore 和 verification 全部成功时 session 才能恢复 `healthy`。

`start`/`single` 的失败执行顺序固定为：

1. 在 `preflight` 保存并验证 baseline；关闭 preflight authorization 后执行 `main`。
2. 若 driver 抛异常、deadline/cancel 触发，或主调用已返回但 `error_after` 判定失败，先保存
   该主异常并禁止把它改写为 cleanup 结果。
3. 在 session 为 `healthy/uncertain` 且 epoch 未变化时，关闭当前 authorization，依次开启
   `failure_cleanup` 和 `cleanup_verification` phase；前者调用
   `restore_acquisition_control(baseline)`，后者调用
   `verify_acquisition_control_restored(baseline)` 并由核心比较 snapshot。
4. restore 或 verification 任一步失败，最终结果仍保留第 2 步主异常（若无主异常则报告
   recovery failure），session 保持 `uncertain/poisoned`；若 session 已 `poisoned`，两步均
   记录 `not_attempted`，不得发送 STOP。

成功的 `start`/`single` 不执行上述 baseline restore；它们分别验证声明的 postcondition，
只在失败、取消、超时或 after-error cleanup 路径恢复调用前的 trigger/acquisition 设置。

snapshot、restore、verify 三个方法分别只能在 `preflight`、`failure_cleanup` 和
`cleanup_verification` 阶段授权中调用。`failure_cleanup` 允许文本 write，
`cleanup_verification` 只允许文本 query；两者都不允许 binary I/O。任一方法在未授权阶段被调用，
或 baseline 的 context/epoch/nonce 与当前 context 不同，必须在发送前拒绝；一次 restore 尝试
结束后不得再次使用同一 baseline。

`start_continuous()` 不保留一个未知或 `single` trigger mode。请求必须显式选择
`auto`、`normal` 或 `roll`，并且该 mode 已在核心校验的
`ScopeAcquisitionControlProfile.supported_continuous_modes` 中声明。
核心必须先持有 `ScopeAcquisitionControlBaseline`，再把其 snapshot 传入 driver。driver 写入
目标 mode 并 query-back，然后才发送连续运行 action。成功结果必须同时回读
`state.trigger_mode == request.trigger_mode` 和允许的运行 phase；
仅发送 RUN、保留 `single` 后再采一次，不得报告为 continuous success。

`acquire_single()` 是等待完成的 acquire operation，不是 arm-only 写操作。它必须分开两种 baseline：

- `original_state`：任何 operation 写入前读取，用于 artifact 和失败 cleanup；
- `proof_baseline_state`：用于 completion proof 的最后一个 pre-arm 观察。若仪器可分开「配置
  SINGLE」和「真正 arm/RUN」，driver 必须先写入 SINGLE、query-back，再读取同 mode 的
  count/identity，并标记 `proof_baseline_stage="configured_pre_arm"`。

`proof_baseline_stage="configured_pre_arm"` 必须对应
`single_arm_semantics="configure_then_arm"`；`proof_baseline_stage="original_atomic_arm"` 必须对应
`single_arm_semantics="atomic_configure_and_arm"`。核心 result verifier 必须与已验证 descriptor
profile 比对，不得信任 driver 自行选择更宽松的 stage。

如果仪器的单条命令不可分地同时配置并 arm，则
`proof_baseline_stage="original_atomic_arm"` 且 `proof_baseline_state == original_state`。该路径只有在
original 已为 `trigger_mode="single"`，且 `ScopeAcquisitionControlProfile` 同时声明
`single_arm_semantics="atomic_configure_and_arm"`、
`atomic_arm_preserves_count_mode_semantics=true` 和 `arm_resets_acquisition_count=false` 时，
才能把 count 作为辅助证据；最终 proof 仍必须同时满足未变化的 `counter_epoch` 和有效
`state_transition`。否则必须改用 identity delta、完整的 state transition，或满足 RFC-0009 的
profile-gated mode-readback terminal STOP proof。

真正 arm 后，Service/driver 在同一 deadline 内等待新 acquisition 完成。只有看到有效
identity 变化、R1.3 暂定的最小状态序列，或满足 RFC-0009 的 profile-gated mode-readback terminal
STOP 条件后，才能成功返回 completion proof；调用前本来就是 `stopped` 不能单独作为完成条件。没有 completion proof 时
返回 `completion_unproven`，不得返回成功 waveform。

R1.3 暂定的最小 `state_transition` proof 为：SINGLE 写入并 query-back 后至少观察一次
`arming`、`waiting` 或 `acquiring`；也可以观察 `ready`，但必须同时回读
`trigger_mode="single"`，且 `(phase, trigger_mode)` 不得与 `proof_baseline_state` 相同。随后必须观察
`complete` 或 `stopped`。如果仪器把
写后第一个查询直接返回 `stopped`，且 count/identity 均不变或不可用，则 completion
unproven，除非满足 [RFC-0009](WaveBench_scope可移植性RFC-0009_SINGLE模式终态STOP证明.md)
规定的 profile-gated mode-readback proof。后续若跨厂商 fixture 证明该序列仍不通用，应保持
`[OPEN]`，不得由单个插件放宽。

arm-only API 不属于 R1.3；未来如确有非阻塞需要，应新增 `scope.acquisition_arm_single`，其
effect 为 `write`、成功输出只证明已 arm，不能复用 `scope.acquisition_single` 的成功合同。

count 比较使用 `proof_baseline_state` 的同一 acquisition mode 基线。新 count 大于基线只可作为
`count_delta_with_epoch` 的辅助条件；它还必须满足 `counter_epoch` 非空且未变化，并与有效
`state_transition` 联合，不能单独证明完成。`counter_epoch` 缺失、改变或无法证明连续时，
必须改用 `identity_delta` 或完整的 `state_transition` proof。count 因
`ScopeAcquisitionControlProfile.arm_resets_acquisition_count=true` 而下降或归零时，不能使用
  `count_delta_with_epoch`。当前首版不接受 modulus，也不尝试用模差区分真实回绕、计数器复位或仪器重启；任何
`completed_count <= baseline_count` 都使 count 辅助条件失效。mode 改变、仪器重启、前面板重置或
疑似回绕会使原 baseline 失效，不能沿用旧 count。

`stop_acquisition()` MUST 幂等：已经 `stopped` 时可直接成功，但仍应返回观察到的状态；非
`stopped` 时必须写入并 query-back。一个 session 同时只允许一个 control operation，且控制
操作使用 exclusive lease。

### 4.3 deadline、取消和恢复

等待 deadline 来自 operation request；若调用方未给出，R1.3 使用第 1.2 节固定的
`SCOPE_ACQUISITION_OPERATION_TIMEOUT_MS`。调用方只能提供更早的绝对 deadline，不能延长它；
每次轮询和 STOP I/O 继续取 connection timeout 与剩余 deadline 的较小值。核心通过显式
`operation_timeout_ms` 和绝对 monotonic deadline 实现该规则。

超时或取消时：

1. 保留主异常和最后一次观察状态；
2. 在 session `healthy` 或 `uncertain` 且核心已授权 recovery transaction 时，best-effort
   执行 `STOP` 并 query-back；
3. 若 session 已 `poisoned`，普通 STOP I/O 必须继续被 gate 拒绝，应关闭并重新建立连接，
   不得从插件直接访问 backend session；
4. cleanup 的成功、失败和最终 `SessionHealth` 写入 artifact，但不能覆盖原始 timeout/cancel
   异常。

recovery STOP 不复用 normal operation 的 `phase != unknown/error` 前置条件：核心可以在
`healthy/uncertain + unknown/error phase` 下签发有界 recovery authorization，但必须限制 I/O 种类、字段、
step 数和 deadline，并要求 STOP 后只读 query-back。`poisoned` session 永远不能获得该授权。
recovery authorization 不继承主请求的 `ErrorCheckSpec`，错误队列检查固定为
`disabled / not_applicable`；否则 before drain 可能拦住本应执行的安全 STOP，也会超出授权的
STOP + query-back I/O 白名单。
normal `scope.acquisition_stop` 仍要求 healthy session 和可识别 phase，不能借 recovery 规则
绕过普通 access/capability gate。

现有 `capture_waveform(s)` 仍是 vendor transaction。核心不能仅凭三项控制方法重新拼装它，
因为通道配置、一次 acquisition 的多通道一致性、transfer 临时状态和恢复仍属于 driver
合同。

多通道 capture MUST 证明所有通道来自一次 trigger transaction 和同一停止记录。优先使用
同一 `acquisition_identity`；无法提供 identity 时，必须由同一 SINGLE 写入计数、count delta
和逐通道读取期间未再次触发的 audit evidence 组成 `shared_acquisition_proof`。部分通道失败时
artifact 应记录 `completed_channels`、`failed_channel`、`shared_acquisition_proof` 和是否发生
重采集，禁止为了补齐缺失通道而隐式重新触发。

## 五、类型化 trace source 与数据不变量

### 5.1 source、轴和 operation

候选模型保留 `spectrum` 字面量，用受限的 FFT operation 描述产生方式；这比把 FFT
伪装成模拟 channel 更明确。但 R1.3 acceptance scope 不注册或迁移 `spectrum`、`math` 和
`fft_phase`；它们只作为未来 trace-extensions RFC 的非读取 metadata 预留。是否把
`spectrum` 拆为独立 `fft` kind、单位模型如何复用核心，均不阻塞本轮 M1 内部基础设施，
但也不能据此宣称 R1.3 全部 trace 合同已冻结。

```python
ScopeTraceKind = Literal["analog", "digital", "math", "reference", "spectrum"]
ScopeAxisKind = Literal["time", "frequency", "index", "unknown"]
ScopeAxisUnit = Literal["s", "Hz", "1", "unknown"]
ScopeTraceUnit = Literal[
    "v",
    "mv",
    "db",
    "dbm",
    "1",
    "unknown",
]
ScopeTraceMagnitudeSemantics = Literal["absolute", "relative", "linear", "unknown"]
ScopeTraceOperation = Literal[
    "identity",
    "reference_copy",
    "fft_magnitude",
    "fft_phase",
    "device_other",
    "unknown",
]

@dataclass(frozen=True)
class ScopeTraceRef:
    kind: ScopeTraceKind
    index: int | None = None
    name: str | None = None
```

`index` 和 `name` MUST 恰有一个有效值。公共编号按 kind 固定：analog、math、reference 和
spectrum index 使用 one-based `1..65535`；digital index 使用 zero-based `0..15`，并与 bit N
一致；所有 index 都不能是 `bool`。具体仪器 profile 只能收紧范围，不能改变基准。`name`
必须包含 `1..64` 个 Unicode code point、去除首尾空格后不变、全部可打印且不得含控制字符。
厂商 token 只存在于 driver，不进入公共模型。

```python
@dataclass(frozen=True)
class ScopeAxisMetadata:
    kind: ScopeAxisKind
    unit: ScopeAxisUnit
    start: float | None
    increment: float | None
    points: int

@dataclass(frozen=True)
class ScopeTraceMetadata:
    source: ScopeTraceRef
    x_axis: ScopeAxisMetadata
    y_unit: ScopeTraceUnit
    y_semantics: ScopeTraceMagnitudeSemantics
    value_encoding: Literal["real", "digital_bitmask"]
    y_increment: float | None = None
    y_origin: float | None = None
    y_resolution_bits: int | None = None
    operation: ScopeTraceOperation = "unknown"
    inputs: tuple[ScopeTraceRef, ...] = ()
    digital_channels: tuple[int, ...] = ()
    fetchable: bool = False

@dataclass(frozen=True)
class ScopeTraceData:
    metadata: ScopeTraceMetadata
    values: np.ndarray

ScopeTraceTransferField = Literal[
    "scope.run_state",
    "scope.waveform_source",
    "scope.waveform_mode",
    "scope.query_response_header",
    "scope.waveform_format",
    "scope.waveform_byte_order",
    "scope.waveform_points",
    "scope.waveform_transfer_window",
]

@dataclass(frozen=True)
class ScopeTraceTransferStateSnapshot:
    captured_fields: tuple[ScopeTraceTransferField, ...]
    run_state_token: ScopeStateToken | None = None
    waveform_source_token: ScopeStateToken | None = None
    waveform_mode_token: ScopeStateToken | None = None
    query_response_header_token: ScopeStateToken | None = None
    waveform_format_token: ScopeStateToken | None = None
    waveform_byte_order_token: ScopeStateToken | None = None
    waveform_points_token: ScopeStateToken | None = None
    waveform_transfer_window_token: ScopeStateToken | None = None

@dataclass(frozen=True)
class ScopeTraceTransferBaseline:
    context_id: str
    session_epoch: str
    baseline_nonce: str
    snapshot: ScopeTraceTransferStateSnapshot
    restore_order: tuple[ScopeTraceTransferField, ...]

@dataclass(frozen=True)
class ScopeTraceTransferRestoreResult:
    status: Literal["completed", "failed", "not_attempted"]
    attempted_fields: tuple[ScopeTraceTransferField, ...]
    restored_fields: tuple[ScopeTraceTransferField, ...]
    error_code: str | None = None

@dataclass(frozen=True)
class ScopeTraceTransferVerification:
    status: Literal["verified", "mismatch", "unavailable"]
    verified_fields: tuple[ScopeTraceTransferField, ...]
    mismatched_fields: tuple[ScopeTraceTransferField, ...]
    error_code: str | None = None

@dataclass(frozen=True)
class ScopeTraceProfile:
    fetchable_kinds: tuple[Literal["analog", "digital", "reference"], ...]
    max_points: int
    restore_order: tuple[ScopeTraceTransferField, ...]
    snapshot_max_steps: int
    restore_max_steps: int
    verify_max_steps: int
    source_index_max: int = 65535

class ScopeTraceTransferRecoveryDriver(InstrumentDriver, Protocol):
    def snapshot_trace_transfer_state(
        self,
        fields: tuple[ScopeTraceTransferField, ...],
    ) -> ScopeTraceTransferStateSnapshot: ...
    def restore_trace_transfer_state(
        self,
        baseline: ScopeTraceTransferBaseline,
    ) -> ScopeTraceTransferRestoreResult: ...
    def verify_trace_transfer_state_restored(
        self,
        baseline: ScopeTraceTransferBaseline,
    ) -> ScopeTraceTransferStateSnapshot: ...

class ScopeTraceMetadataDriver(InstrumentDriver, Protocol):
    def get_trace_metadata(self, source: ScopeTraceRef) -> ScopeTraceMetadata: ...

class ScopeTraceDriver(
    ScopeTraceMetadataDriver,
    ScopeTraceTransferRecoveryDriver,
    Protocol,
):
    def fetch_trace(
        self,
        source: ScopeTraceRef,
        *,
        points: str | int = "dmax",
        baseline: ScopeTraceTransferBaseline | None,
    ) -> ScopeTraceData: ...
```

`ScopeTraceProfile` 是 descriptor 必须提供的静态事实；`fetchable_kinds` 只能是
`analog`、`digital`、`reference` 的非空唯一子集，`max_points`、`source_index_max` 和三个
`*_max_steps` 是有限正整数。`restore_order` 必须唯一，并覆盖 profile 允许临时改变的所有
transfer fields；每个 operation 的 baseline 只保留实际 changed fields 的有序子集，不能由
driver 临时决定顺序。
它必须位于 `InstrumentDescriptor.scope_extensions.trace_profile`；driver 或运行时 metadata
不得新增 fetchable kind、提高 points/index 上限或改变编号基准。
R1.3 不把 `math`、`spectrum` 或 `fft_phase` 放入可注册的 fetch profile；它们可以继续出现在
`ScopeTraceMetadata` 的非读取结果中，但必须为 `fetchable=false`。

`ScopeTraceTransferStateSnapshot.captured_fields` 必须唯一，且每个字段对应的 token 恰好在
字段存在时非空；`restore_order` 必须与实际 changed field 集合相同且无重复。核心在
`preflight` 读取 snapshot 并构造带 context/epoch/nonce 的 `ScopeTraceTransferBaseline`，在
`success_restore` 或 `failure_cleanup` 调用 restore，再在 `cleanup_verification` 调用 verify。
verify 方法必须返回 fresh snapshot，由核心逐字段生成 `ScopeTraceTransferVerification`；不得
用 `ScopeTraceTransferRestoreResult` 的写入记录代替 query-back。context、epoch、nonce、phase
或一次性消费状态不匹配时，必须在 transfer I/O 前拒绝。上述接口同样覆盖 `CHDR`、`CORD`、
`WFSU` 或其他等价 transfer 状态，不允许只为某一厂商保留私有恢复 helper。
`attempted_fields` 必须是 baseline `restore_order` 的前缀，`restored_fields` 只能是已尝试字段
的有序子序列；restore/verify 抛出的异常由核心规范化为 `failed`/`unavailable`，且不得覆盖
主 fetch 异常。

### 5.2 R1.3 trace acceptance scope

R1.3 首轮公共 trace 只接受 descriptor 明确声明的 `analog`、`digital` 和 `reference`，以及
已冻结的 `time`/`index` 轴和现有核心可表达的单位。`ScopeTraceProfile.fetchable_kinds` 是
唯一可注册边界；`spectrum`、`math`、`fft_phase`、频率轴和新增单位即使仍保留在模型字面量中，
也必须 `fetchable=false`，不得进入 capability registry 或插件迁移。后续 RFC 冻结 kind、
axis、unit 和序列化后，才能扩展 profile；这项排除不影响本 RFC 对 binary/恢复基础设施的
内部实现资格。

### 5.3 数组、单位和语义约束

- `points` MUST 等于 `len(values)`；values MUST 是一维、非空数组。
- axis 的 `points >= 1`。`time`、`frequency` 和 `index` 的 `start`、`increment` 必须
  finite，`increment > 0`，计算出的最后一个坐标也必须 finite；`unknown` 的 `start` 和
  `increment` 必须为 `None`。
- 未来 `frequency` 轴 profile 仅接受 one-sided、严格递增、非负频率；DC 可从 `0 Hz` 开始。
  负频率、中心化双边 FFT 和降序轴需要后续 axis profile，不能通过负 increment 偷渡；该轴
  不属于 R1.3 公共 fetch scope。
- `analog`、`math`、`reference` 和 `spectrum` 的未来实现使用 finite、real、`float64` 值；核心应复制
  并设置只读，不能把 driver 的可变数组直接暴露给调用方。首版明确拒绝 complex dtype，
  FFT 的复数结果必须选择 magnitude 或 phase 语义后再进入模型。
- `digital` 使用无符号整数 bitmask（首版上限 `uint16`），并在 metadata 中说明有效 bit
  与 `digital_channels` 的映射；`digital_channels` 必须非空、唯一且位于 `0..15`，bit N 对应
  digital channel N。R1.3 首版的 fetchable digital source 只支持
  `ScopeTraceRef(kind="digital", index=N)` 的单线语义：`digital_channels == (N,)`，
  每个样本只能是 `0` 或 `1 << N`。具名 bus/group 和多 bit 同步 bitmask 需要独立
  source kind/profile，在此之前只能保持 `fetchable=false` 或继续使用旧
  `ScopeDigitalWaveform`。非 digital trace 的 `digital_channels` 必须为空，不把数字值编码成浮点电压。
- `time` 轴使用 `s`，`frequency` 轴使用 `Hz`，`index` 轴使用 `1`；未知轴只能使用
  `unknown`，不能同时声称精确的 start/increment 换算。`kind` 与 unit 不匹配时必须拒绝，
  不能把 `Hz` 当作任意显示标签。
- `y_unit` 只冻结首版 token：`v`、`mv`、`db`、`dbm`、`1` 和 `unknown`。其中
  `dbm/absolute`、`db/relative`、`v|mv/linear` 沿用核心
  `MagnitudeUnit` / `MagnitudeSemantics` 规则；`1` 仅由新增的 digital-bitmask verifier
  校验，不能冒充现有 `MagnitudeUnit`。电流 `a`、相位 `degree` 和百分比 `percent` 需要先
  扩展核心单位模型，不能在 R1.3 中以任意字符串或未经校验的新增 token 进入公共
  `ScopeTraceData`。无法证明时使用 `unknown/unknown`。
- `digital_bitmask` 的 y unit 固定为 `1`，semantics 为 `unknown`；`spectrum` 的 dB/dBm
  结果必须明确 absolute/relative，不能只给一个 dB 字符串。`fft_phase` 在相位单位扩展
  被核心接受前只能作为设备私有 metadata（或以 `unknown` 单位返回），不得宣称跨仪器可比较。
- `analog` 和 `digital` 的 `operation` MUST 为 `identity` 且 `inputs` 为空；`reference` 的
  `identity` 也必须没有 input，`reference_copy` 恰有一个 input；`fft_magnitude` 和
  `fft_phase` 恰有一个 input。R1.3 不冻结通用
  `add`、`subtract`、`multiply`、`divide`、`differentiate` 或 `integrate`；这些运算及其
  输入计数、单位代数移入独立的 trace operation/unit-algebra RFC。
- `math` 在 operation catalog 和单位语义冻结前只能使用 `device_other` 或 `unknown`；核心
  不得据此推导可移植的算术语义，且 `device_other`/`unknown` 的 `inputs` 必须为空。
  `reference` 只有 `identity`（设备原生 reference）或
  `reference_copy`（明确复制另一 source）两种首版语义。
- 未来 `spectrum` 的 operation MUST 为 `fft_magnitude` 或 `fft_phase`，且 x 轴 MUST 为
  `frequency`。`get_trace_metadata()` 可以为尚不可读取的 math/source 返回
  `device_other`/`unknown`，但必须标记 `fetchable=false`；`fetch_trace()` 只接受
  `fetchable=true` 且满足对应 kind/operation 不变量的 metadata。`device_other` 和 `unknown`
  不得进入成功的 `ScopeTraceData`，也不能声称结果可跨仪器比较。
- `y_increment`、`y_origin` 同时为 `None` 或同时为 finite float；`y_increment` 不得为零。
  `y_resolution_bits` 为 `None` 或 `1..64` 的非 bool 整数。`digital_bitmask` 的三项 y
  scaling 字段必须全部为 `None`；real trace 若提供 resolution，必须同时提供 increment/origin。
- `fetchable=true` 的 R1.3 kind 组合只有：analog/reference + time + `v|mv/linear`，以及
  digital + time + `1/unknown`。math、spectrum、`fft_phase`、frequency、未知轴、未知单位或
  `device_other/unknown` operation 只能返回 `fetchable=false` metadata；不能进入成功的
  `ScopeTraceData`。

### 5.4 迁移和读取前置条件

现有模型的单向迁移建议如下：

| 现有模型 | 候选 trace 映射 | 约束 |
| --- | --- | --- |
| `WaveformData` | `analog` | 保留原 `fetch_waveform`；反向适配只允许 analog |
| `ScopeDigitalWaveform` | `digital` | 单通道可映射为 `index=N`；多通道 bitmask 保留旧模型，不伪装成单线 trace |
| `ScopeDerivedWaveformMetadata` | `math` 或 `reference` metadata | 不把 `source_kind` 丢失 |
| `ScopeFftStatus` + 频域数据 | `spectrum` | `ScopeFftStatus` 继续兼容；R1.3 只保留 `fetchable=false` metadata，频率轴单独表达留给后续 RFC |

`fetch_trace()` 是 query/read operation，但若需要临时改变 source、transfer window 或停止
采集，必须声明相应 `changed_fields` 和恢复覆盖。默认前置条件为：source 已配置、必要时
acquisition 已停止、sequence/segmentation 状态与 source 合同一致、points 属于 descriptor
`ScopeTraceProfile.max_points`、
错误检查策略已解析。前置条件失败必须发生在任何 transfer 写入或 binary query 前。
对只声明普通非分段记录的 `fetch_trace` 和现有 `fetch_waveform`，sequence ON 必须返回
`precondition_failed` 或 `unsupported_state`；SDS804X HD 已提供零 waveform 写入、零 binary
query 的实机拒绝证据，但该规则仍需第二个厂商 fixture。

`ScopeTraceMetadataDriver.get_trace_metadata()` 和 `ScopeTraceDriver.fetch_trace()` 是 R1.3
正式候选 Protocol 方法，不再是仅供说明的自由函数。声明 `scope.trace_metadata` 时，driver
MUST 实现 `ScopeTraceMetadataDriver`；声明 `scope.fetch_trace` 时，driver MUST 同时实现
`ScopeTraceDriver`、`ScopeTraceTransferRecoveryDriver`，并由 descriptor 提供已验证的
`ScopeTraceProfile`。若 fetch operation 没有 changed transfer fields，`baseline` 固定为
`None`；只要任一 transfer field 可能改变，核心必须传入本 context 的
`ScopeTraceTransferBaseline`，driver 不得自行 snapshot 或替换 baseline。

`fetch_trace()` 的成功路径固定为：`preflight snapshot -> main transfer/fetch ->
error_after? -> success_restore restore -> cleanup_verification verify`。主 fetch、binary 校验或
after error 失败时，保留主异常并执行 `failure_cleanup restore -> cleanup_verification verify`；
restore/verify 任一步失败不得覆盖主异常，且 session 不能恢复为 `healthy`。恢复结果和 fresh
snapshot 必须写入父 operation artifact，不能只记录「transfer 已恢复」布尔值。

`fetch_trace()` 属于 `core_v1` 错误策略执行路径，因此 driver 签名不再携带
`check_errors`；核心在调用前后负责第六节的错误检查。旧 `fetch_waveform(...,
check_errors=bool)` 通过 legacy adapter 迁移，不能把两个执行者混入同一 transaction。

## 六、错误检查策略与 artifact

### 6.1 策略模型

现有 `scope.check_errors: bool` 保持兼容。候选配置扩展为：

```python
ErrorCheckPolicy = Literal["required", "if_supported", "disabled"]
ErrorCheckTiming = Literal["before", "after", "before_and_after"]
InstrumentErrorPolicy = Literal["fail", "record_and_continue"]

@dataclass(frozen=True)
class ErrorCheckSpec:
    policy: ErrorCheckPolicy
    timing: ErrorCheckTiming = "before_and_after"
    max_records: int = 16
    on_instrument_error: InstrumentErrorPolicy = "fail"
```

候选 `ErrorCheckSpec` 包含 `policy`、`timing`、`max_records` 和 `on_instrument_error`；上面的
默认值是 R1.3 acceptance candidate；当前核心配置仍不因本文改变。`max_records` MUST 为非 bool 整数，范围固定为
`1..256`；`256` 是核心硬上限，仪器、插件和调用方只能收紧。首版只提供完整 drain：
读到「无错误」终止 token 或达到上限；不提供会绕过检查的 `none`，也不提供无法证明队列
完整性的 `one`。自动 clear 和 peek 都不进入首版合同。
下表的 capability 对 R1.3 `core_v1` 路径专指 `scope.error_drain_v1`；只有旧
`scope.errors` 不构成「明确支持」。

| 策略 | capability 明确支持 | capability 明确不支持 | capability 未知 |
| --- | --- | --- | --- |
| `required` | 按 timing 执行；空队列是成功 | 在 I/O 前拒绝 | 在能力发现完成前拒绝 |
| `if_supported` | 实际执行检查 | 不发送探测命令，记录 `status=skipped` 和 `reason_code=unsupported` | 在 I/O 前拒绝，记录 `status=rejected` 和 `reason_code=unknown_capability` |
| `disabled` | 不发送错误队列查询 | 不发送 | 不发送 |

`check_errors=true` 映射为 `required`，`false` 映射为 `disabled`。`if_supported` 不能简单
等价于 `false`：有 capability 时必须查询，查询失败也必须报告真实 failure，而不是
`unsupported`。普通 transport/protocol/instrument response error 不因 `disabled` 而被吞掉。
`disabled` 完全忽略 `scope.error_drain_v1` 和旧 `scope.errors` capability，不执行错误检查，
也不存在 unavailable 拒绝；
这条规则保留 SDS800X HD 的 `check_errors=false` 行为。

`on_instrument_error` 不是任意调用方可自由组合的容错开关。核心在解析策略后根据
`OperationSpec.effect` 做静态校验：R1.3 只允许 `observe` 或 `stateful_read` operation 使用
`record_and_continue`；`write` 和 `acquire` 只能使用 `fail`。recovery transaction 按第 4.3 节固定为
`disabled`，不执行错误队列 I/O。因此
错误检查不能把一个已经发生设备错误的写操作伪装成成功。

配置优先级从强到弱为：`OperationSpec` 最低策略、单 operation 显式覆盖、仪器全局策略、
旧布尔配置映射。策略强度为 `required > if_supported > disabled`；低优先级或调用方参数不能
削弱 `OperationSpec` 的最低要求。未配置新字段时继续使用旧布尔映射，默认行为不改变。

`if_supported` 的未知能力固定使用 `unknown_capability`，在任何 I/O 前拒绝，不得由插件选择性
试探。`on_instrument_error="record_and_continue"` 在 R1.3 只允许用于 `observe` 或
`stateful_read`；`write` 和 `acquire` 必须使用 `fail`，recovery operation 必须使用 `disabled`，
核心在零 I/O 前拒绝不相容配置。`on_instrument_error` 只处理成功读出的设备错误记录；
错误队列查询本身的 transport/session/protocol failure 始终中止当前 operation，不得被
`record_and_continue` 吞掉。

每次 operation 只能有一个错误检查执行者，由 descriptor capability 和 driver 合同在
factory 阶段固定：

- `legacy_driver`：核心只做 capability gate，把旧 bool 交给 driver；核心不得再 drain 一次，
  该路径只适用于已有 operation，artifact 必须标记 `executor=legacy_driver` 和
  `status=legacy_unstructured`，不得伪造 R1.3 `checks`；
- `core_v1`：descriptor 必须声明 `scope.error_drain_v1`，factory 必须验证下文
  `ScopeErrorDrainDriver` Protocol。核心按 timing 调用唯一的 `drain_errors()`；新版主 operation
  driver 签名不带 bool，或兼容
  adapter 固定传 `check_errors=False`，driver MUST NOT 再查询队列。

只声明旧 `scope.errors` 不能自动适配为 `scope.error_drain_v1`；`list[str]` 无法证明终止 token、
查询次数或类型化记录。R1.3 operation 在有效策略不是 `disabled` 时只允许
`core_v1`；现有 operation 才可在版本门内继续使用 `legacy_driver`。迁移测试必须按 operation
断言错误队列查询次数，避免 core 和 driver 双重 drain。现有公共
`scope.errors` 本身是消耗性 `stateful_read`，其 `OperationSpec` 也必须增加
`changed_fields=("scope.error_queue",)` 和 legacy artifact；其 `OperationRequest.error_check` 必须为
`None`，不得在读错误队列前后递归套用 `ErrorCheckSpec`。其旧 `list[str]` 成功值保持兼容，
若未来需要公开类型化 direct-drain 结果，应新增 operation，不得改变旧返回类型。
旧 driver 即使在内部读到某个厂商终止 token，核心也不得由 `list[str]` 反推通用终止证据或
真实 query 次数。

现有 `scope.errors` 的候选兼容规格固定为：`effect="stateful_read"`、
`changed_fields=("scope.error_queue",)`、`verification_fields=()`、
`OperationRequest.error_check=None`，成功值仍为 `list[str]`。核心只记录
`legacy_unstructured` operation-specific artifact；`terminated` 与 `query_count` 始终为
`null`。该规格不继承 `scope.error_drain_v1` 的 phase、`max_records+1` 或 typed result。

直接调用旧 `scope.errors` 时，operation-specific artifact 固定使用：

```json
{
  "executor": "legacy_driver",
  "capability": "scope.errors",
  "status": "legacy_unstructured",
  "requested_limit": 16,
  "returned_record_count": 3,
  "terminated": null,
  "query_count": null
}
```

`requested_limit` 只记录旧公共参数，`returned_record_count` 只记录返回列表长度；两者都不是
完整 drain 证明。artifact 不复制旧原始字符串。`terminated` 和 `query_count` MUST 为
`null`，不得由列表内容或长度猜测。

`scope.error_drain_v1` 是内部受管子事务 capability，不是第九个公共 operation，不加入
operation registry，不提供独立 Service/CLI 入口。「子事务」只表示其结果归属当前
operation context，不表示嵌套 session authorization。核心 error-policy executor 必须在上一阶段
authorization 已关闭后，为同一 context 签发独立的 `error_before` 或 `error_after`
authorization。该阶段继承 context 的 correlation ID、session epoch、exclusive lease、
access 结论和剩余 deadline，语义为
`effect=stateful_read`、`changed_fields=("scope.error_queue",)`、只允许文本 query，
`max_steps=max_records+1`。它不得超出 context deadline、改变 lease/access、创建 binary
budget ledger，也不得生成独立 operation artifact；结果只写入当前 operation 的
`error_check.checks`。签发前若 session 已非 healthy 或剩余 deadline 不足，该阶段不得开始。

### 6.2 错误记录与生命周期

候选 driver 边界和公开错误记录为：

```python
@dataclass(frozen=True)
class DriverErrorRecord:
    code: str | int | None
    message: str
    severity: Literal["info", "warning", "error", "fatal", "unknown"]
    source: str

@dataclass(frozen=True)
class ErrorDrainResult:
    records: tuple[DriverErrorRecord, ...]
    terminated: bool
    query_count: int
    overflow_record: DriverErrorRecord | None = None

class ScopeErrorDrainDriver(InstrumentDriver, Protocol):
    def drain_errors(self, *, max_records: int) -> ErrorDrainResult: ...

@dataclass(frozen=True)
class ErrorRecord:
    code: str | int | None
    message: str
    message_redacted: bool
    severity: Literal["info", "warning", "error", "fatal", "unknown"]
    source: str
    observed_at_utc: str
    correlation_id: str | None
```

`drain_errors()` 是 `scope.error_drain_v1` 唯一允许的 driver 方法，不带 clear/peek 参数。
core 为它签发的非嵌套 `error_before|error_after` phase authorization 只允许文本
`query` I/O，不允许 write、binary query 或再开启另一 authorization。
driver 读到文档化的「无错误」token 时设 `terminated=true`，该 token 不进入 `records`。
R1.3 首版规定每次 queue query 只解析一条错误记录或一个终止 token，不接受
一次返回多条记录的无类型文本 batch。`query_count` 是真实仪器查询数，范围为
`1..max_records+1`；核心 authorization 的
`max_steps` 也必须固定为 `max_records+1`。最后一个额外 step 只用于证明终止；若它仍返回错误记录，
driver 必须把该条保存在 `overflow_record`，返回 `terminated=false`，核心以
`error_queue_incomplete` 失败。该记录不塞入受 `max_records` 限制的 `records`，但必须经过与
其他记录完全相同的 scrubber 后进入失败 artifact，不得静默丢弃。
分支不变量为：

- `terminated=true`：`overflow_record is None` 且 `query_count == len(records) + 1`；
- `terminated=false`：`len(records) == max_records`、`query_count == max_records + 1` 且
  `overflow_record is not None`。

任何 transport/session/protocol 失败直接抛出结构化异常，不构造
伪造的 `ErrorDrainResult`。
核心必须校验 `len(records) <= max_records`，并把 `query_count` 与 guarded transport
在当前 authorization 中的实际 query 增量对账；不一致是 driver contract violation，不得生成成功
artifact。

`observed_at_utc` 由核心在成功解析该条记录后生成，必须是带 `Z` 的 RFC 3339 UTC 时间；
deadline 仍使用不序列化的单调时钟，两者不能混用。公开 `ErrorRecord` 不含 raw 响应。
`DriverErrorRecord.message` 是受信任进程内输入，不得直接序列化。核心必须删除控制字符，
并用已知 resource、IP/USB 地址、IDN 序列号、本地路径和凭据模式的统一 scrubber 做脱敏。
脱敏后的公开 `message` 必须是 `1..512` 个 Unicode code point；只要替换过内容，
`message_redacted=true`。若 scrubber 不能证明输出安全，固定改为 `instrument reported an error`
并设 `message_redacted=true`，不得把原文作为 fallback。`source` 是 `1..64` 个 ASCII safe-token
字符；字符串 code 也必须使用同一 safe-token 约束，整数 code 不接受 bool。
未经清洗的 raw 只可保存在受控本地诊断中，并通过脱敏 evidence ID 关联；不得进入公开
artifact、异常消息或 `ErrorRecord`。

R1.3 的完整 drain 合同只适用于 `scope.error_drain_v1`：最多公开 `max_records` 条错误，
不自动 clear，并区分空队列、队列不可用和查询本身失败。
读取最多 `max_records` 条错误后，必须再执行第 `max_records+1` 次证明 query。
该 query 返回「无错误」终止 token 才表示 drain 完整；若仍返回错误记录，必须按上文
`overflow_record` 分支记录并以 `error_queue_incomplete` 失败，不能少发一次证明 query，
也不能把截断列表当作完整检查。
现有 `scope.errors(limit) -> list[str]` 不受本段 `max_records+1` 规则约束，也不得用于
R1.3 `required/if_supported` 错误策略。

before 阶段发现已有记录时，默认主 operation 不发送并报告
`preexisting_instrument_error`；这些记录不归属于新的 `correlation_id`。只有在 operation
effect 为 `observe` 或 `stateful_read`，且显式选择 `record_and_continue` 时，核心才可记录后
继续该非变更 operation；`write`、`acquire` 和 recovery 仍必须在发送前失败。after 阶段发现
记录时，满足同一条件的非变更 operation 可继续，
变更 operation 报告 `instrument_error`，并保留已经发生的
副作用和 cleanup 状态。错误队列查询发生 transport、session 或 protocol failure 时始终是
`failed` 并中止，不能降级成 unsupported；设备明确返回的 instrument error 仍按
`on_instrument_error` 处理。批量通道共享一次 operation correlation，不把同一组设备错误任意
复制到每个通道。

after 阶段发现不允许继续的 instrument error 时，已成功返回的主 driver 调用必须转为
operation failure。对 `write` 和 `acquire` operation，执行顺序固定为：after drain 完成并保存
`instrument_error` 主异常 → 执行 `OperationSpec` 声明的 failure cleanup → 执行
`cleanup_verification_fields` 验证 → 写入最终 session health 和 artifact。cleanup 或验证失败
不得覆盖 after 的 `instrument_error`，但必须使 session 保持 `uncertain/poisoned`。
对 `scope.acquisition_start` 和 `scope.acquisition_single`，这意味着必须按第 1.2/4.3 节尝试
STOP、恢复 trigger/acquisition baseline 并 query-back；不得因为主 driver 曾返回成功就把
仪器继续运行状态留作公共 success。若 after drain 本身使 session 不再允许普通 cleanup I/O，
只能使用已声明的有界 recovery authorization；不得绕过 session gate。

### 6.3 固定 artifact 结构

每个使用 R1.3 错误策略的 operation 至少写入：

```json
{
  "executor": "core_v1",
  "policy": "if_supported",
  "capability": "scope.error_drain_v1",
  "supported": false,
  "status": "skipped",
  "reason_code": "unsupported",
  "timing": "before_and_after",
  "max_records": 16,
  "on_instrument_error": "fail",
  "checks": [],
  "attempted_phases": [],
  "completed_phases": [],
  "omitted_phases": [],
  "last_drain_terminated": null,
  "main_operation_sent": true,
  "diagnostic_evidence_id": null
}
```

`executor` 固定为 `core_v1` 或 `legacy_driver`。`status` 固定为 `completed`、`skipped`、
`disabled`、`rejected`、`failed` 或 `legacy_unstructured`。
`reason_code` 固定为 `empty`、`records`、`unsupported`、`unknown_capability`、
`preexisting_instrument_error`、`instrument_error`、`error_queue_incomplete`、`query_failed`
或 `not_applicable`。`rejected` 只用于能力或配置预检在错误队列 I/O 前拒绝；已开始 drain 后
遇到队列不完整、查询失败或不允许继续的仪器错误时使用 `failed`。

`checks` 不是任意 JSON；每个已执行阶段都必须使用以下固定结构：

```json
{
  "phase": "before",
  "status": "completed",
  "reason_code": "empty",
  "query_count": 1,
  "terminated": true,
  "records": [],
  "overflow_record": null
}
```

`phase` 固定为 `before` 或 `after`；check 级 `status` 固定为 `completed` 或 `failed`，
`reason_code` 固定为 `empty`、`records`、`error_queue_incomplete` 或 `query_failed`。
`query_count` 是包含终止 token 查询在内的实际队列 query 次数，必须是
`1..max_records+1` 的非 bool 整数；
`records` 只包含已清洗的 `ErrorRecord`，不包含终止 token，长度不得超过
`max_records`。check 还必须包含 `overflow_record`，值为脱敏后的 `ErrorRecord` 或 `null`；
它必须与 `ErrorDrainResult` 的分支不变量一致。`terminated=true` 仅表示该阶段确实
读到了文档化的「无错误」终止 token。
未执行的 phase 不生成占位条目。

`attempted_phases` 按时间顺序列出至少发送过一次队列 query 的 phase；`completed_phases`
只列出以 `terminated=true` 完成的 phase。`last_drain_terminated` 是最后一个已尝试 phase
的终止结论，没有查询时为 `null`。这三个字段不会把「before 已完整 drain，但主操作
未发送，因此 after 不适用」错写成 drain 不完整。`main_operation_sent` 只表示主 operation
是否已发送任一仪器命令，不包含 before check。`diagnostic_evidence_id`
为 `null` 或脱敏 opaque safe-token，只关联受控本地证据，不得嵌入原始响应、资源字符串或
本地路径。

`omitted_phases` 每项固定为
`{"phase":"after","reason_code":"main_operation_failed|session_unhealthy|cancelled"}`。主 operation 在
after 之前失败、取消，或把 session 变为 `uncertain/poisoned` 时，核心 MUST NOT 为补齐
timing 继续查询错误队列；必须把 after 写入 `omitted_phases`。若主操作失败但 session
仍 `healthy`，after 也默认 omitted，避免用消耗性查询干扰主异常恢复；未来若需「失败后检查」，
应单独设计策略。error-check 顶层只聚合实际执行的 phase，主失败、取消和 session health
仍由 operation artifact 作为主结果，不被 error-check status 覆盖。

顶层聚合规则固定为：

| 情形 | 顶层 `status / reason_code` | `main_operation_sent` |
| --- | --- | --- |
| `disabled` | `disabled / not_applicable` | 按主 operation 实际情况 |
| `if_supported` 且明确 unsupported | `skipped / unsupported` | 主 operation 可继续 |
| `required` 且 unsupported，或能力未知 | `rejected / unsupported` 或 `rejected / unknown_capability` | `false` |
| 任一 phase 队列查询失败或未终止 | `failed / query_failed` 或 `failed / error_queue_incomplete` | 按失败发生前实际情况 |
| before 有记录且策略/操作不允许继续 | `failed / preexisting_instrument_error` | `false`；after 不适用 |
| after 有记录且策略/操作不允许继续 | `failed / instrument_error` | `true` |
| before 或 after 有记录，且非变更操作允许继续 | `completed / records` | 按实际情况 |
| 所有实际 phase 均空 | `completed / empty` | 按实际情况 |
| 旧 driver 自行查错 | `legacy_unstructured / not_applicable` | 按主 operation 实际情况 |

聚合优先级为：查询失败/队列不完整 > 不允许继续的 instrument error > 已记录并继续 >
空队列。因此「before 有记录并继续，after 为空」固定聚合为 `completed / records`；
「after 有记录但 observe 允许继续」也是 `completed / records`。不能用模糊的 `failed`
吞掉具体原因。

`legacy_driver` 使用同一顶层 key 集，但 `capability="scope.errors"`、`checks=[]`、
phase 数组全空，且不得填写伪造的 query count/termination。当旧 bool 为 `false` 时仍使用
`disabled / not_applicable`，而不是 `legacy_unstructured`。直接调用 `scope.errors` 时
`error_check=null`，另写 operation-specific `error_drain` artifact，不递归产生 before/after checks。

当前 SDS800X HD 没有文档化错误队列，插件继续要求显式 `check_errors=false`，
不声明 `scope.errors` 或 `scope.error_drain_v1`。

artifact 中的 `supported` 使用 `true` 或 `false` 表示能力已评估；使用 `null` 表示能力未评估或未知。
`disabled` 路径即使未评估也必须使用 `status=disabled` 和 `reason_code=not_applicable`，不得因为未知而拒绝。
只有 `required/if_supported` 因能力未知而在 I/O 前拒绝时，`supported=null` 才与
`status=rejected` 和 `reason_code=unknown_capability` 一起出现。公开 artifact 使用结构化 code、经过 scrubber 和长度限制的 message、
`message_redacted`、UTC 时间和
correlation，不复制可能包含资源或设备私有内容的原文。

## 七、能力发现、版本门与旧接口

### 7.1 descriptor 与 capability-method contract

R1.3 acceptance addendum 为 descriptor 增加一个可选、可序列化的 scope 扩展字段；字段名和
必需关系冻结如下：

```python
@dataclass(frozen=True)
class ScopeDescriptorExtensions:
    screenshot_profile: ScopeScreenshotProfile | None = None
    acquisition_control_profile: ScopeAcquisitionControlProfile | None = None
    trace_profile: ScopeTraceProfile | None = None

@dataclass(frozen=True)
class InstrumentDescriptor:
    # 保留现有字段；新增字段不改变旧 descriptor 的默认语义。
    scope_extensions: ScopeDescriptorExtensions | None = None
```

核心 factory 在 capability discovery 阶段把 descriptor 的 `scope_extensions` 与 driver
Protocol 一起校验；缺失所需字段或方法时，在第一次仪器 I/O 前返回
`unsupported_capability`，不能只因 Python 方法恰好存在就注册 capability。R1.3 的中央
`CAPABILITY_METHODS` 等价映射固定为：

| capability | descriptor 前置事实 | required Protocol | required method(s) | 可注册 operation |
| --- | --- | --- | --- | --- |
| `scope.screenshot_profile` | `scope_extensions.screenshot_profile` 非空 | `ScopeScreenshotProfileDriver` | `get_screenshot_profile()` | `scope.screenshot_profile` |
| `scope.screenshot_v2` | screenshot profile 非空且已验证 | `ScopeScreenshotDriver` + `ScopeScreenshotProfileDriver` | `capture_screenshot()`、state snapshot/restore/verify | `scope.screenshot_v2` |
| `scope.acquisition_run_state` | 无额外 profile | `ScopeAcquisitionRunStateDriver` | `get_acquisition_run_state()` | `scope.acquisition_run_state` |
| `scope.acquisition_control` | `scope_extensions.acquisition_control_profile` 非空 | `ScopeAcquisitionControlDriver` | `start_continuous()`、`stop_acquisition()`、`acquire_single()`、recovery 三方法 | `scope.acquisition_start/single/stop` |
| `scope.trace_metadata` | `scope_extensions.trace_profile` 非空 | `ScopeTraceMetadataDriver` | `get_trace_metadata()` | `scope.trace_metadata` |
| `scope.fetch_trace` | trace profile 非空且含请求 kind | `ScopeTraceDriver` | `fetch_trace()`、transfer snapshot/restore/verify、`get_trace_metadata()` | `scope.fetch_trace` |
| `scope.error_drain_v1` | capability 明确声明且策略允许 | `ScopeErrorDrainDriver` | `drain_errors(max_records=...)` | 只作为受管 error phase，不新增公共 operation |

核心 registry 的方法映射可直接编码为：

```python
SCOPE_CAPABILITY_METHODS = {
    "scope.screenshot_profile": ("get_screenshot_profile",),
    "scope.screenshot_v2": (
        "get_screenshot_profile",
        "capture_screenshot",
        "snapshot_screenshot_state",
        "restore_screenshot_state",
        "verify_screenshot_state_restored",
    ),
    "scope.acquisition_run_state": ("get_acquisition_run_state",),
    "scope.acquisition_control": (
        "get_acquisition_run_state",
        "start_continuous",
        "stop_acquisition",
        "acquire_single",
        "snapshot_acquisition_control",
        "restore_acquisition_control",
        "verify_acquisition_control_restored",
    ),
    "scope.trace_metadata": ("get_trace_metadata",),
    "scope.fetch_trace": (
        "get_trace_metadata",
        "fetch_trace",
        "snapshot_trace_transfer_state",
        "restore_trace_transfer_state",
        "verify_trace_transfer_state_restored",
    ),
    "scope.error_drain_v1": ("drain_errors",),
}
```

对应的 profile provider Protocol 为：

```python
class ScopeScreenshotProfileDriver(InstrumentDriver, Protocol):
    def get_screenshot_profile(self) -> ScopeScreenshotProfile: ...
```

`scope.screenshot_profile`、`scope.trace_metadata` 和 `scope.fetch_trace` 不再只有文字建议
方法；上表中的 Protocol 是 capability 注册的必要接口。`ScopeAcquisitionControlProfile`、
`ScopeScreenshotProfile` 和 `ScopeTraceProfile` 的安全上限以 descriptor 为准，driver 的
运行时返回值只能 query-back 验证，不能扩大或替换 descriptor 事实。任何 capability 的
required Protocol、profile 或方法不满足时，factory MUST 在零 I/O 阶段 fail-closed；未声明
capability 的额外方法不产生隐式能力。

R1.3 公共 capability（核心 `0.8.23` 已注册）：

```text
scope.screenshot_profile
scope.screenshot_v2
scope.acquisition_run_state
scope.acquisition_control
scope.trace_metadata
scope.fetch_trace
scope.error_drain_v1
```

兼容要求：

1. 现有 capability、方法和模型不删除、不改名；
2. 新核心 + 旧插件保持现状；
3. 使用新 transport 或新 capability 的插件提高 wheel 和 descriptor 的核心下限；
4. 旧核心 + 新插件在 factory 和第一次仪器 I/O 前明确拒绝；
5. 新增可选 capability 不自动要求升级 `wavebench.instrument.v2`；
6. 在 `ScopeDescriptorExtensions`、`CAPABILITY_METHODS`、operation registry、Service、CLI 和
   artifact schema 未同时冻结前，插件不得声明新能力。

## 八、conformance 测试矩阵与证据要求

| 层级 | 必测内容 | 当前状态 |
| --- | --- | --- |
| OperationSpec/Service | capability、access、lease、action-specific changed/restore/postcondition/cleanup、transfer 字段闭包、artifact | 已实现公共 operation、稳定 Service 和版本化 artifact |
| capability/descriptor gate | `ScopeDescriptorExtensions`、`CAPABILITY_METHODS`、required Protocol、缺 profile/method 零 I/O 拒绝 | 已实现；新 capability 要求核心下限 `0.8.23` |
| binary model | `#N` 精确语法、`#0` 拒绝、response/operation/query/resync budget、成功 metadata、尾部和 continuation | definite/message、超限、截断和尾部向量已覆盖；continuation 保持拒绝 |
| backend | PyVISA/RsInstrument/TCP/serial 的 message 能力证明、终止设置恢复 | PyVISA/RsInstrument definite-block 已实现；VISA `INSTR` EOM fixture 已覆盖；SocketIO/serial 拒绝 `MESSAGE` |
| guarded transport | access、计数、healthy/uncertain/poisoned、固定常量、超限后失步、close/poison 默认 | 已覆盖失败恢复和合同违反测试 |
| plugin trust boundary | 公共 Protocol 不暴露 session；禁止插件依赖 `.inner` 的代码审计 | 当前不是沙箱；opaque facade 不在 R1.3 范围 |
| screenshot | request tuple、PNG signature/IEND、媒体类型、尺寸、transport/content 尾部分层、父 capture fail-parent | 独立 `screenshot_v2` 已实现；旧 capture 对新 capability 采用零 I/O 拒绝，旧 capability 保持兼容 |
| acquisition | allowed phase、完成式 SINGLE、baseline/observed states、identity semantics、终态 proof、成功 postcondition、失败 recovery | 公共控制和 SDS conformance fixture 已通过 |
| transfer/trace | typed transfer snapshot/restore/verify、context nonce、analog/digital/reference profile、字段逐项闭合 | 公共模型与 Service 已实现，SDS transfer fixture 已通过 |
| errors | disabled 零 I/O、未知 fail-closed、`scope.error_drain_v1`、完整 drain、唯一执行者、聚合 reason | core v1 与 legacy artifact 已实现 |
| compatibility | 新旧核心/插件四组合、factory 拒绝、CLI/artifact | capability 组合、版本门和旧插件回归已覆盖 |
| opt-in hardware | 至少两种 framing、两个厂商状态机、两种 trace axis | 仍由每个插件单独完成，不属于核心离线验收结论 |

### 8.1 必备 fixture 与失败向量

插件声明新 capability 前，至少需要带有 backend、resource class、固件版本和证据文件
链接的 fixture：

- definite block：合法 `#N`、`#0`、非法长度位、截断、response/operation/query budget 超限、
  resync ceiling、精确 consumed 等式、`BinaryQueryResult` 和 transport trailing；
- message：分片读取、EOM、超限后有界 drain/poison、termination 恢复失败和下一次 query；
- acquisition：descriptor profile 缺失/非法、continuous mode 支持集、configure-then-arm 与
  atomic-arm 两条 baseline 路径、count reset、counter epoch 缺失或变化与异常回绕拒绝、每个 action 的 allowed/rejected phase、
  调用前已 stopped、count 不变、跳过 acquiring 的最小状态序列、外部前面板改状态、
  after instrument error cleanup 和 timeout 后 recovery STOP 失败；
- transfer restore：typed `ScopeTraceTransferBaseline`、`CHDR`/`CORD`/`WFSU` 或等价状态的
  逐字段 changed/verification、context/nonce 重放拒绝、恢复失败和 healthy/poisoned 判定；
- screenshot：baseline/query-back/成功与失败恢复、transport/content trailing 负向向量、
  nonce 重放、旧 capture 父字段闭包和 fail-parent 语义；
- trace：capability/descriptor gate、kind-specific index、R1.3 analog/digital/reference fetch、
  spectrum/math 排除、负频/降序拒绝、scaling、非 finite 数值、只读数组和 points 不一致；
- errors：三种 policy、disabled + unsupported、能力未知、`scope.error_drain_v1` factory gate、
  `max_records+1` 终止证据、查询失败、driver/core 双读负向测试、聚合 reason 和脱敏；
- compatibility：旧核心 + 新插件与新核心 + 旧插件的能力发现和第一次 I/O 行为。

图片、原始波形、真实 resource、序列号和完整命令日志不进入仓库；只保留 framing、长度、
状态迁移、固件版本和数值摘要。

## 九、里程碑

| 里程碑 | 范围 | 退出条件 | 状态 |
| --- | --- | --- | --- |
| M1 | Operation context、phase authorization、OperationSpec/artifact 内部骨架 | context/phase/ledger/legacy artifact 与 transfer recovery model 的 feature-gated 测试通过 | 已完成 |
| M2 | binary framing 与 backend capability | definite/message fake、四维 budget、有界 resync、失步、termination 恢复测试通过 | 已完成 |
| M3 | screenshot profile/v2 | definite block 和 raw message 两种 fixture 通过 | 已完成 |
| M4 | acquisition run state/control | descriptor profile validator、continuous mode、SINGLE proof、幂等 STOP、after-error cleanup 和 timeout recovery 通过 | 已完成 |
| M5 | trace source/axis | R1.3 范围内 analog、digital、reference fixture 通过 | 已完成 |
| M6 | error policy、版本门和迁移 | A1 P0/P1、`scope.error_drain_v1`、artifact、兼容组合和 CLI/Service 冻结 | 已完成 |
| M7 | opt-in 实机 | 核心离线回归通过，具体型号和 resource/backend 另行授权 | 按插件执行 |

各里程碑应分别提交；不得把 transport、scope model、Service 和插件迁移压成一个不可回滚
改动。

## 十、已否决方案

- 插件直接访问 `transport.session` 或 backend；当前只能以「公共合同不提供」约束，不能声称
  Python 运行时沙箱隔离；
- 给 `query_bin_block()` 增加含义模糊的 `raw=True`；
- 使用换行、idle timeout 或 `rstrip()` 推断 PNG 成功结束；
- 在 transport 内置 PNG parser；
- 忽略 screenshot 的 menu 参数；
- 把 trigger status 填入现有 `ScopeAcquisitionStatus`；
- 用 `*OPC?` 统一判断物理触发完成；
- 把 math/reference/FFT 编成负数或大号 channel；
- 扩宽 `WaveformData.channel` 为任意字符串；
- 为没有错误队列的仪器返回空列表；
- 超过 `max_bytes` 后在 healthy session 中留下未消费响应。

## 十一、R1.3 已冻结安全结论与后续问题

以下安全不变量已经随 R1.3 接受，并由第十二节的验收门限定。后续问题不得反向扩大已经注册
capability 的语义，也不表示具体插件已经完成 opt-in 或实机验收：

1. 采集 start、完成式 single、stop 是三个 action-specific operation；共享 capability 不改变
   各自 effect、postcondition、失败 cleanup 或最低 access。descriptor 的
   `ScopeAcquisitionControlProfile` 是 continuous mode、SINGLE arm 语义和 count 比较的唯一
   静态事实源；arm-only 需要独立 operation。
2. binary response、operation-total、query-count 和 resynchronization 分别使用有限上限；核心通过单个
   operation context 下的非嵌套阶段 authorization 引用同一 `BinaryQueryBudget` ledger，
   插件不能构造、提升、重置或跨 context 复用。
3. `query_binary()` 成功返回 `BinaryQueryResult`，显式携带 framing、长度、
   `transport_trailing_bytes` 和精确 consumed 等式，以及
   `synchronization=proven`；失败使用现有结构化 `TransportIOError`。
4. `disabled` 错误策略始终零错误队列 I/O；`if_supported` 只跳过明确 unsupported，未知能力
   fail-closed。`core_v1` 以 `scope.error_drain_v1` 和终止证据为门；旧 `scope.errors`
   只保持 `legacy_unstructured` 语义，不得被升级为类型化 drain。一个 operation 只能由
   core 或 legacy driver 其中一方执行错误检查。
5. R1.3 的可读取 trace 不包含通用算术，只支持已列出的 kind/axis/unit/operation 组合；
   one-sided FFT、编号基准和 scaling 不变量不能留给插件自行解释。
6. `CHDR`/`CORD`/`WFSU` 或等价的 transfer 状态必须在核心规范化为逐项
   `changed_fields` + `verification_fields`；该要求覆盖 `scope.capture_multiple` 等现有别名。
7. 旧 capture 调用 screenshot 只允许父 operation 字段闭包；R1.3 不注册 composite operation。
   没有完整字段闭包时必须在 I/O 前拒绝，截图失败或恢复失败必须使父 capture 失败，不能
   通过嵌套 authorization 或部分错误记录继续返回成功。
8. 所有可恢复的 stateful write 都必须通过 core-owned snapshot、有界 restore 和独立 verify
   阶段闭合；主 driver 抛异常或 after error 不能让 baseline 丢失。
9. 每个 operation 只能在同一 operation context 中顺序使用非嵌套 phase authorization；
   error drain phase 不得创建或重置 binary budget ledger。
10. 首版不使用 modulus 单独证明 SINGLE 完成；count 只能在未变化的 `counter_epoch` 和
   有效 `state_transition` 联合时作为辅助证据，任何回绕、复位或非严格递增都必须转用
   `identity_delta`/`state_transition`。旧 `scope.errors` 不提供 R1.3 终止证明。

后续独立问题：

1. 更通用的 operation 输入/输出序列化、取消、幂等性和并发元数据若有需求，应由独立 RFC
   追加；当前已注册 operation 的输入、结果和 artifact 形状保持不变。
2. 当前批准的 backend/resource 已有固定 resynchronization fixture；其他 backend 只有补齐
   同等 fixture 后才能 opt-in。超出上限时 close + `poisoned` 仍是统一默认。
3. 当前只认可已验证的 PyVISA/RsInstrument VISA `INSTR` 路径；新增 resource class 或 API
   的 message END 证明属于后续 backend 扩展。
4. `READ_CONTINUATION_ONLY` 的 core-issued continuation token 和返回模型如何授权。
5. 更多 screenshot profile variant 仍待独立 fixture；旧 capture 与 V2 的拒绝/分流语义已经
   冻结，profile 来源仍为 descriptor 或 descriptor/query 的 `combined` 交集。
6. poisoned session 的 recovery/reopen API 仍属 transport 生命周期设计；在该 API 冻结前，
   新 capability 不得从 poisoned session 继续 I/O。
7. 暂定的 completion state-transition 序列及 counter-epoch 联合条件能否通过第二个厂商
   fixture，是否需要进一步收紧；更丰富的计数器世代语义仍待单独 RFC。
8. `spectrum` 是否作为独立 `ScopeTraceKind`，以及单位校验复用哪些现有核心模型；该项已
   明确排除在 R1.3 公共 fetch scope 外，移入后续 trace-extensions RFC。
9. 当前批准 backend 的 binary operation/profile 映射已实现；其他 backend 必须复用相同
   operation 常量、profile 收紧规则和超限 close/poison 默认。
10. error queue 的未来 peek/clear operation 仍待独立设计；R1.3 timing 默认固定为
    `before_and_after`，未知能力不得增加 skip 分支。

R1.3 公共范围已经冻结。仍待后续 RFC 的问题不得扩展当前 capability；插件只有完成对应
fixture、版本门和实机验收后，才能在正式 descriptor 中声明新能力。

## 十二、R1.3 acceptance addendum（A1）

本 addendum 是 R1.3 的验收门，不是第二套并行规范。核心 `0.8.23` 开发线已经完成本节规定的
离线验收并注册公共 capability。若本节与正文存在歧义，以本节的 acceptance gate 为准；正文
仍是跨仪器模型的唯一事实源。

### 12.1 允许先行的内部工作

公共注册前，核心只允许在内部或 feature-gated 分支实现以下内容：

| 内部组件 | 必须具备的约束 | 明确禁止 |
| --- | --- | --- |
| operation context | 一次 operation 一个 context；绑定 correlation、epoch、deadline 和 ledger | 为重试或错误检查创建第二个 context |
| phase coordinator | `normal`、`recovery`、`verification` 顺序授权；active authorization 最多一个 | 从 driver 或 active authorization 内嵌套签发 |
| binary ledger | response/total/query/resync 四项固定上限，跨 phase 不重置 | 用 error phase 或重建 driver 增加额度 |
| typed state models | acquisition、screenshot、transfer 的 snapshot/baseline/restore/verify 模型 | 暴露 session token、用布尔值替代 fresh snapshot |
| legacy artifact | 旧 `scope.errors` 的 `legacy_unstructured` 记录 | 将旧 `list[str]` 升级为 typed drain 证明 |
| fake/conformance fixture | 覆盖失败恢复、nonce 重放、phase 越界、binary 超限和 capability gate | 在实机或插件 descriptor 上开启新 capability |

该限制用于记录公共注册前的历史门禁。现在仍适用于没有通过插件自身验收的具体 driver；未声明
新 capability 的旧插件 descriptor 和核心版本下限保持不变。

### 12.2 P0 公共接口验收门

在任何新 capability 注册或插件迁移前，核心必须逐项验收：

1. **transfer recovery**：`ScopeTraceTransferRecoveryDriver`、
   `ScopeTraceTransferStateSnapshot`、`ScopeTraceTransferBaseline`、restore result 和
   fresh-snapshot verification 已实现；descriptor `ScopeTraceProfile.restore_order` 和 step
   上限已验证；`fetch_trace` 在 changed transfer fields 非空时必须传入同一 context 的
   baseline，并按 `CHDR`/`CORD`/`WFSU` 等逐字段恢复和核对。
2. **capability/descriptor**：`ScopeDescriptorExtensions` 字段、中央
   `CAPABILITY_METHODS` 映射和各 required Protocol 已实现；缺失 profile/method 时在零 I/O
   阶段拒绝，额外方法不产生隐式 capability。
3. **numeric and deadline constants**：截图 `8388608/8388608/1/0`、trace
   `8388608/67108864/256/65536`、operation timeout `5000/30000/60000 ms` 已作为核心常量
   实现；profile/connection 只能收紧，超出同步界限统一 close + `poisoned`。
4. **error timing**：默认 `before_and_after`、recovery 固定 `disabled`、每次 I/O 受绝对
   monotonic deadline 限制，并有对应 artifact 和负向测试。

### 12.3 P1 语义验收门

- **嵌入 screenshot**：R1.3 只采用父 capture operation 字段闭包，不注册 composite operation。
  没有完整 changed/verification/cleanup 字段时在 I/O 前拒绝；截图或恢复失败使父 capture
  失败，不能只记部分错误。
- **baseline handle**：screenshot、acquisition、transfer baseline 必须包含
  `context_id`、`session_epoch`、core-generated opaque `baseline_nonce` 和 restore order；
  nonce 按 `fresh -> passed_to_main -> restore_attempted -> consumed` 一次性消费，重放在 I/O
  前拒绝，artifact 只留摘要。
- **identity proof**：`ScopeAcquisitionControlProfile.identity_semantics` 必须为
  `unique_within_session_epoch` 才能使用 `identity_delta`；该条件只约束 identity proof。
  `state_transition` 和 RFC-0009 的 profile-gated terminal STOP proof 仍按各自条件校验。
- **phase API bridge**：核心通过 `ScopeOperationContextCoordinator.authorize_phase()` 包裹
  当前 normal gate 与 `SessionTransactionCoordinator.authorize()`，并用 sidecar/扩展记录绑定
  context、phase、fields、allowed I/O、deadline 和 max steps；driver 不接收 session token。
- **trace exclusion**：R1.3 公共 fetch 仅包含 analog/digital/reference；spectrum、math、
  fft_phase、frequency axis 和新增单位移入后续 RFC，不得以未决模型开始插件迁移。

### 12.4 Addendum 退出条件

A1 按以下证据决定 capability registry 和插件迁移边界：

1. 核心内部 fixture 覆盖上述 P0/P1 gate，且新旧四组合在 factory 和第一次 I/O 前行为明确；
2. 至少两个独立仪器族或 backend fixture 证明 transfer restore、截图恢复、acquisition proof
   和 binary framing/limit 语义；
3. Service、CLI、descriptor、`CAPABILITY_METHODS`、artifact schema 和版本门由核心团队逐项
   评审并冻结；
4. R1.3 Draft 的待决问题中仍保留的 trace-extensions、continuation 和 reopen 设计不再被
   当前 capability 合同隐式引用。

上述退出条件已由核心 `0.8.23` 开发线的离线 fixture、两类 backend wrapper、SDS800X HD
conformance fixture、稳定 Service/CLI/artifact 和版本门满足。具体插件仍不得把核心通过等同于
本型号实机通过。
