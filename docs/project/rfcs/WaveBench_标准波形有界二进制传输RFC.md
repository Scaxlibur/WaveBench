# WaveBench 标准波形有界二进制传输 RFC

> 状态：`Implemented R1（未发布）`
> 修订：`R2`
> 核心基线：WaveBench `0.8.24`，`master@dc7ce5b`
> 相关规范：[scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)、[transport 重放与 session 健康 RFC](WaveBench_transport重放与session健康RFC.md)
> 证据来源：WaveBench Instrument Plugins 中的 MSO8000 `RFC-0008`
> 目标版本：包含本实现的下一个 `0.8.x` 发布；外部插件在该版本发布前不得提高版本下限或声明新范围

## 摘要

WaveBench `0.8.24` 已有 `query_binary()`、精确 transport trailing、四维 binary budget、
operation context 和 session poison 合同。这些能力已用于 R1.3 screenshot 和 trace
operation，但标准 `scope.fetch_waveform`、`scope.capture_waveform` 和
`scope.capture_waveforms` 仍直接调用 driver 的 legacy 方法。该路径没有 binary operation
context，无法为每个响应声明精确 trailing，也无法在发送前安装单响应、操作
总量、查询次数和重同步预算。

本 RFC 记录标准波形 Service 在 descriptor 显式 opt-in 时复用已有 bounded binary
context。不给 `query_bin_block()` 增加 `expect_termination`、`max_bytes` 或其他平行
关键字；不改动没有 opt-in 的旧插件和内建 driver。

本文记录核心合同和实施边界。核心实现已在当前分支完成，但不表示外部插件已经完成
conformance 或实机验收；在 P4 前，插件不得据此恢复波形或采集 capability。

## 问题与证据边界

MSO8104 在 WaveBench `0.8.24`、LAN/PyVISA、固件 `00.02.02` 上已返回有效的
10 字段 preamble，其中声明 `1000` 个 BYTE 点。后续 `:WAVeform:DATA?` 在 legacy
`query_bin_block()` 中等待 PyVISA 默认终止语并超时。核心无法证明响应边界，因此
把 session 标记为 `poisoned`，并在发送前拒绝 driver 后续的 transfer-state restore。

该证据只支持以下结论：

- legacy 路径无法表达该型号、固件和 transport 组合的空 trailing；
- 增加 timeout 不能证明同步，也不能把失败查询变成可重放查询；
- 全局关闭 termination 等待会改变其他 RIGOL 和 R&S driver 的已有行为；
- capability 只能在新核心合同发布且该型号重新完成实机验收后恢复。

该证据不证明 MSO8000 全系列、其他固件、USB/GPIB 或其他 backend 具有相同
trailing 行为。核心 profile 必须保持精确声明，不能从型号名称或单次成功读取外推。

## 当前核心基线

| 范围 | `0.8.24` 现状 | 本 RFC 的处理 |
| --- | --- | --- |
| `BinaryQueryResult` | 已校验 framing、声明长度、consumed bytes、精确 trailing 和 `synchronization=proven` | 直接复用 |
| `BinaryQueryLedger` | 已管理 response、operation total、query count、resynchronization 和 deadline | 直接复用 |
| `GuardedAuditedTransport.query_binary()` | 必须在带 opaque budget 的有效 phase 中调用 | 作为 bounded 路径的唯一 binary 入口 |
| `ScopeOperationContextCoordinator` | 已在 R1.3 操作中生成单一 ledger 并顺序授权 phase | 扩展到 opt-in 的标准波形操作 |
| `scope.fetch_trace` | 已使用 bounded binary 和 core-owned transfer baseline | 作为实施参考，不作为标准 capture 的替代品 |
| 标准 waveform/capture | Service 直接调用 legacy driver 方法 | 只在 descriptor opt-in 后使用 bounded 路径 |
| `query_bin_block()` | 在无 budget 的 legacy 操作中保持旧行为；在已安装 budget 的 phase 中发送前拒绝 | 保持该边界，不新增平行配置 |

R1.3 文档曾描述 legacy `query_bin_block()` 在新 operation 内消耗同一 ledger。当前安全边界是在
budget phase 中拒绝 legacy 入口；R1.3 的历史候选描述已同步为该规则，避免两份公开合同冲突。

## 目标

- 让 opt-in 的标准 `fetch` 和 `capture` 在发送 binary query 前获得有限 budget。
- 让 descriptor 精确声明 definite block 后的 transport trailing bytes。
- 让单通道、多通道和分块传输共享一个 operation-total 和 query-count ledger。
- 让 core-owned baseline 在主读取失败时仍可用，并保留主异常的优先级。
- 保持旧插件、内建 driver、CLI、run plan、成功返回对象和旧 artifact 的兼容行为。
- 在 capability 恢复前分别完成核心离线合同、插件 conformance 和受控实机验收。

## 非目标

- 不删除或改名现有 `scope.fetch_waveform`、`scope.capture_waveform` 和
  `scope.capture_waveforms` capability。
- 不把 `scope.fetch_trace` 适配成标准 capture；它不承担现有 acquisition、多通道
  partial result 和采集包语义。
- 不开放 raw SCPI、backend session、调用方自定义 terminator 或 parser callback。
- 不为 `query_bin_block()` 增加仪器特例、大小预算或 trailing 关键字。
- 不在本 RFC 中实现自动重连、断点续传或 binary continuation token。
- 不从一个 MSO8104 实机证据外推其他型号、固件或 transport。
- 不在核心合同发布前修改任何外部插件的 wheel/descriptor 版本下限。

## 已实现合同

### 1. Legacy 边界

`InstrumentTransport.query_bin_block()` 保持当前签名和行为：

```python
def query_bin_block(
    self,
    command: str,
    *,
    replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
) -> bytes: ...
```

强制规则：

1. 无 bounded profile 的旧 operation 仍可调用该方法；
2. active phase 含 `BinaryQueryBudget` 时，该方法必须在发送前以
   `binary_legacy_entry_unsupported` 拒绝；
3. 核心不得在捕获 `TypeError` 后删除新关键字并回退到 legacy 调用；
4. driver 声明 bounded profile 后仍调用 `query_bin_block()` 属于合同违反，不得自动降级。

### 2. Descriptor profile

公共模型为：

```python
@dataclass(frozen=True, slots=True)
class ScopeWaveformBinaryOperationProfile:
    operation_kind: Literal["fetch", "capture_single", "capture_multiple"]
    response_max_bytes: int
    operation_max_bytes: int
    query_max_count: int
    resynchronization_max_bytes: int
    restore_order: tuple[ScopeWaveformTransferField, ...]
    snapshot_max_steps: int
    restore_max_steps: int
    verify_max_steps: int

@dataclass(frozen=True, slots=True)
class ScopeWaveformBinaryProfile:
    operations: tuple[ScopeWaveformBinaryOperationProfile, ...]
    framing: BinaryResponseFraming = BinaryResponseFraming.DEFINITE_BLOCK
    transport_trailing_hex: str = ""

@dataclass(frozen=True, slots=True)
class ScopeDescriptorExtensions:
    # 保留已有字段与位置顺序。
    waveform_binary_profile: ScopeWaveformBinaryProfile | None = None
```

profile 规则：

- `waveform_binary_profile is None` 表示使用旧路径，不表示默认空 trailing；
- profile 仅适用于 descriptor 已声明的标准波形 capability，不产生隐式 capability；
- 首版只接受 `DEFINITE_BLOCK`，不把 timeout、换行或暂时无数据解释为 message boundary；
- core 将该 framing 绑定到 opaque binary ledger；driver 请求其他 framing 时在发送前拒绝；
- `transport_trailing_hex` 是小写、偶数长度、最长 16 bytes 的精确序列；空字符串只
  表示已验证的空 trailing，不表示「不检查」；
- `operations` 中每个 `operation_kind` 最多出现一次，并与 descriptor 已声明的 capability
  精确对应；`fetch`、`capture_single` 和 `capture_multiple` 不能互相推导；
- 每个 operation profile 的 response、operation-total 和 query-count 上限必须是有限
  正整数，`resynchronization_max_bytes` 必须是有限的非 bool 非负整数；
- 每个 operation profile 的 `restore_order` 必须唯一，且只覆盖该操作可能改动的
  transfer fields。不得因 `capture_multiple` 会改动 run state，就让纯 `fetch` 执行多余的
  run-state restore 写入；
- 声明 `waveform_binary_profile` 的 descriptor `wavebench_min_version` 必须为 `0.8.24` 或更高版本；
  外部 wheel 的 `Requires-Dist` 也必须使用相同或更高的下限；
- core `OperationSpec`、descriptor profile 和 connection limit 三者取逐项最小值；profile 不能
  扩大核心硬上限；
- bounded profile 只能与核心已验证的 backend/resource 能力组合使用。第三方 transport 仅
  实现公共 `query_binary()` 不足以证明它在读取前获得 trailing 和 resynchronization
  上限；没有明确 bounded-backend 能力时必须在 binary command 发送前拒绝；
- 没有有效 profile 或必需方法时，bounded driver 必须在第一次仪器 I/O 前 fail
  closed。该保证需要下文的 opt-in factory construction barrier，不能只依赖 factory 返回后的
  capability validator。

`ScopeWaveformTransferField` 是标准 waveform 的独立恢复字段集：

```text
scope.run_state
scope.acquisition
scope.trigger
scope.timebase
scope.channel_display
scope.channel_vertical
scope.waveform_source
scope.waveform_mode
scope.query_response_header
scope.waveform_format
scope.waveform_byte_order
scope.waveform_points
scope.waveform_transfer_window
```

`ScopeWaveformTransfer*` 是与 `ScopeTraceTransfer*` 分离的冻结 snapshot、baseline、restore-result
和 verification 模型；现有 trace 类型的导入、类型身份和语义不变。`fetch` 可以只声明实际会改动的
字段；`capture_single` 和 `capture_multiple` 必须覆盖上述完整字段集，确保 time range、vertical scale、
采集和 transfer setup 不会在成功后遗留仪器状态。

### 3. Driver 与恢复边界

bounded profile 路径保留现有 capability 名称，但不修改已发布的 `ScopeDriver` 方法参数。
核心根据 profile 使用独立、可选的 driver Protocol：

```python
class ScopeWaveformTransferRecoveryDriver(Protocol):
    def snapshot_waveform_transfer_state(
        self,
        fields: tuple[ScopeWaveformTransferField, ...],
    ) -> ScopeWaveformTransferStateSnapshot: ...

    def restore_waveform_transfer_state(
        self,
        baseline: ScopeWaveformTransferBaseline,
    ) -> ScopeWaveformTransferRestoreResult: ...

    def verify_waveform_transfer_state_restored(
        self,
        baseline: ScopeWaveformTransferBaseline,
    ) -> ScopeWaveformTransferStateSnapshot: ...

class ScopeBoundedWaveformFetchDriver(
    ScopeWaveformTransferRecoveryDriver,
    Protocol,
):
    def fetch_waveform_bounded(
        self,
        channel: int,
        points: str,
        *,
        baseline: ScopeWaveformTransferBaseline,
    ) -> WaveformData: ...

class ScopeBoundedWaveformCaptureDriver(
    ScopeWaveformTransferRecoveryDriver,
    Protocol,
):
    def capture_waveform_bounded(
        self,
        channel: int,
        points: str,
        *,
        time_range_s: float | None,
        vertical_scale_v_per_div: float | None,
        baseline: ScopeWaveformTransferBaseline,
    ) -> WaveformData: ...

class ScopeBoundedMultiWaveformCaptureDriver(
    ScopeWaveformTransferRecoveryDriver,
    Protocol,
):
    def capture_waveforms_bounded(
        self,
        channels: list[int],
        points: str,
        *,
        time_range_s: float | None,
        vertical_scale_v_per_div: float | None,
        on_channel_start: Callable[[int | None], None] | None,
        on_waveform: Callable[[int, WaveformData], None] | None,
        baseline: ScopeWaveformTransferBaseline,
    ) -> dict[int, WaveformData]: ...
```

上述方法不加入所有 `ScopeDriver` 的必需方法集。只有 descriptor profile 非空时，
factory 才使用专用 validator 检查已声明 capability 对应的 bounded 方法和全部恢复方法。
旧 `fetch_waveform()`、`capture_waveform()` 和 `capture_waveforms()` 签名保持不变；旧 driver 和 fake
不需要补空实现。profile-specific method mapping 已作为 `wavebench.instrument.v2` 的 additive
extension 冻结：只有 profile 非空的 descriptor 才改用新方法集。

validator 的候选分流为：

| capability | profile 为 `None` | profile 非空 |
| --- | --- | --- |
| `scope.fetch_waveform` | 要求 `fetch_waveform()` | 要求 `fetch_waveform_bounded()` 和恢复方法 |
| `scope.capture_waveform` | 要求 `capture_waveform()` | 要求 `capture_waveform_bounded()` 和恢复方法 |
| `scope.capture_waveforms` | 要求 `capture_waveforms()` | 要求 `capture_waveforms_bounded()` 和恢复方法 |

新核心不先按旧 `CAPABILITY_METHODS` 拒绝 opt-in driver，再尝试 bounded validator。这三项必须由
同一 profile-aware validator 一次裁决；其他 capability 继续使用现有全局映射。额外方法不产生隐式
capability。

bounded driver 的强制规则：

1. 主方法使用 `query_binary(framing=DEFINITE_BLOCK, max_bytes=expected_payload_bytes,
   replay=NO_REPLAY)`；driver 不传入 trailing 或 resynchronization 参数；
2. preamble、分块边界和样本换算仍属于 driver，transport 只解析 framing 和字节边界；
3. 主方法不再在 `finally` 中自行 restore。核心在独立 phase 中持有 baseline、调用 restore 并
   执行 fresh verify；
4. 单通道、多通道和所有 application chunk 必须共用同一 context、baseline、deadline 和
   binary ledger；
5. driver 无权构造、替换或持久化 baseline nonce，也无权重置 budget；
6. bounded 方法不接收 `check_errors`；错误检查只由 core R1.3 executor 管理，避免 core 和
   driver 双重消耗错误队列。

当配置要求错误检查时，bounded profile 路径必须声明并实现 `scope.error_drain_v1`。
未声明该 capability 的 driver 只能在有效策略为 `disabled` 时执行；核心不使用 legacy
`scope.errors` 伪造类型化 drain 证据。

opt-in factory 使用 construction barrier。当 descriptor profile 非空时，
`DriverContext.open_transport()` 可以建立 transport 和 session，但在 factory 返回、bounded Protocol/
profile 验证和 backend/resource 能力验证完成前，guarded transport 必须以
`factory_construction_pending` 在发送前拒绝全部仪器 I/O。验证
失败时关闭已打开的 transport，不发送 IDN 或其他探测命令。该 barrier 只对 opt-in
descriptor 生效，不改变旧插件 factory 的已发布行为。首版只接受核心 PyVISA 或
RsInstrument 的可证明 VISA `INSTR` bounded-binary 路径；Serial、SocketIO 和第三方 duck transport
在 factory 验证阶段拒绝。

### 4. Service 编排

`ScopeService` 根据 descriptor profile 选择路径：

| 操作 | 无 bounded profile | 有 bounded profile |
| --- | --- | --- |
| `fetch_waveform` | 保持当前直接 driver 调用 | `preflight snapshot -> error_before? -> main -> error_after? -> restore -> fresh verify` |
| `capture_waveform` | 保持当前 driver-owned transaction | 同一 context 内完成 acquisition 和有界波形读取，再 restore/verify transfer state |
| `capture_waveforms` | 保持当前多通道部分结果语义 | 一次 acquisition、一个 baseline、一个 ledger；逐通道读取不重建 context；提供 callback 时每个请求通道恰好回调一次，且回调 waveform 必须与最终 map 一致 |

路径选择必须发生在旧 `scope.errors` 前置 capability gate 之前。legacy 分支继续把
`ScopeConfig.check_errors=true` 映射为对 `scope.errors` 的要求，并保持 driver-owned 查错。bounded
分支不要求 `scope.errors`，而是固定映射：

```text
ScopeConfig.check_errors = true  -> ErrorCheckSpec(policy="required")
ScopeConfig.check_errors = false -> ErrorCheckSpec(policy="disabled")
```

bounded 分支的 `required` 必须由 `scope.error_drain_v1` 满足；无该 capability 时在 before-drain 和
主操作 I/O 前拒绝。首版不从插件或 profile 接受另一个默认策略，也不用同时声明的
legacy `scope.errors` 代替 typed drain。

标准 `OperationSpec` 的公开 operation 名、capability 要求、effect、changed fields 和成功
返回对象保持不变。bounded executor 使用私有 overlay，固定 60 秒 operation deadline，并冻结
`8 MiB / 64 MiB / 256 queries / 64 KiB resynchronization` 的四维 core ceiling。该 ceiling
只作用于 profile opt-in 路径，不能改变 DS1104/DS1000Z、RTM2032 或其他 legacy driver 的行为；
descriptor 只能收紧，不能扩大。无法分块且超过单响应上限的未来设备需要新的兼容性与内存证据。

### 5. 失败、poison 与异常优先级

强制状态规则：

1. binary query 未证明同步或已失步时，session 进入 `poisoned`，binary ledger 失效；
2. `poisoned` 后不签发 restore 或 verification authorization，不向 backend 发送 STOP、回写、
   IDN 或探测查询；
3. transport 主异常必须保留为最终异常的 primary cause；「因 session 已 poisoned 而未恢复」
   只进入 cleanup diagnostics，不能覆盖 `TransportIOError`；
4. binary response 已证明边界，但 payload 长度、preamble、数值换算或 application chunk 校验
   失败时，核心仍执行有界 restore 和 fresh verify；
5. 主操作成功但 restore/verify 失败时，不返回波形成功值；session 按已有 health
   合同保持 `uncertain` 或 `poisoned`；
6. 失败后不自动重连、不重放 binary query、不从中间 chunk 继续。

## 兼容性合同

### 核心与插件组合

| 组合 | 预期行为 | 拒绝点或证据 |
| --- | --- | --- |
| 旧核心 + 旧插件 | 保持原版行为 | 不读取新 profile |
| 新核心 + 旧插件 | profile 为 `None`，继续调用 legacy driver 方法 | 现有命令序列、参数和返回对象不变 |
| 旧核心 + 新 bounded 插件 | 正常安装被 wheel `Requires-Dist` 拒绝；强行安装后不允许仪器 I/O | 旧核心可能在 entry-point 构造新 slotted descriptor 字段时直接报 plugin-load error，未必能进入 descriptor 版本比较 |
| 新核心 + 新 bounded 插件 | 使用 profile、context、`query_binary()` 和 core-owned recovery | conformance 与实机验收通过后才声明 capability |

四组表描述已按 `wavebench.instrument.v2` additive extension 实现。无论是 wheel 依赖拒绝，
还是强制安装后的 descriptor load error，旧核心组合都不得进行仪器 I/O。

### 旧 driver、transport 和 fake

- 现有 DS1104/DS1000Z 和 RTM2032 descriptor 不自动增加 profile，首个核心实现不得改变它们的
  binary 读取、float-list 读取或 screenshot 行为。
- 旧 duck-typed transport 和 fake 只在 descriptor opt-in 后才需要 `query_binary()` 和新 baseline 方法。
  没有 opt-in 的旧测试不传入新关键字。
- 新核心读取 profile 时使用明确的可选字段语义；不得把旧 descriptor 中缺少该字段
  解释为已验证的空 trailing。
- 已 opt-in 但 backend 没有 `query_binary()`、无法执行有界 definite-block 读取，或不支持该
  resource class 时，必须在 binary command 发送前返回结构化错误；不回退到
  `query_bin_block()`。
- `InstrumentTransport` 保留 `query_bin_block()` 和已有 `replay` 关键字。本 RFC 不删除方法，
  也不改变旧调用的默认重放策略。

### 公共调用面

- CLI 命令、run plan step、`ScopeService.fetch_waveform()`、`CaptureResult`、
  `MultiCaptureResult` 和 `WaveformData` 的公共形状保持不变。
- capability 名称保持不变；profile 只选择实现合同，不产生 `_v2` 或 `_bounded`
  平行 capability。
- 成功返回不改为 `ScopeExtensionOperationResult`。phase、budget 和 cleanup 证据使用现有
  context diagnostics；若后续加入 capture metadata，只能以可选的 additive 字段进入，并为旧
  reader 保留回归测试。
- 不根据 profile 改变 operation effect、access policy、lease mode 或高阻安全门。

### 版本和 API

- `ScopeDescriptorExtensions` 的新字段放在字段末尾，保留现有位置参数顺序。
- 已发布的 `ScopeDriver` 方法名、参数和返回 model 保持不变。`ScopeWaveformBinaryProfile` 与独立
  bounded waveform Protocol 组是 `wavebench.instrument.v2` 的 additive extension；profile 为
  `None` 时始终保留旧方法集。
- 首个包含完整合同的核心版本发布后，opt-in 插件必须同步提高 wheel 和 descriptor
  下限，并重新评审 `<0.9` 上限。
- 正常安装中，wheel 依赖先拒绝旧核心。如果使用不安全方式强制安装，旧核心可以在新
  slotted descriptor 字段的构造期返回稳定 plugin-load error；不强求它在无法构造新对象
  时继续执行 descriptor 版本比较，但必须保持零仪器 I/O。
- 新核心中任何缺 profile、缺 bounded Protocol 方法、backend/resource 不支持或版本不兼容的
  opt-in 组合，都必须由 construction barrier 和静态 validator 在第一次仪器 I/O 前拒绝。

## 验收矩阵

### 核心离线合同

| 范围 | 必测内容 |
| --- | --- |
| profile | `None` legacy 路径、精确空/非空 trailing、整数上限、字段顺序、缺方法零 I/O 拒绝 |
| factory barrier | opt-in factory 可打开 transport 但在 post-factory validator 完成前的 query/write/binary 全部零发送；验证失败关闭 transport |
| framing | 空 trailing、`LF`、`CRLF`、缺失 trailing、额外 trailing、malformed header、截断 payload |
| budget | per-response、operation-total、query-count、resynchronization、deadline；跨 chunk/phase/channel 不重置 |
| no replay | 发送后失败只有一次 binary command；不从中间 chunk 继续 |
| recovery | 已证明同步的数据错误执行 restore + fresh verify；restore 失败不返回成功 |
| poison | 失步后 backend 收到零条 restore/verify 仪器命令；原 `TransportIOError` 保持 primary |
| multiple | 一次 acquisition、一个 context、一个 ledger，保留现有 partial result/callback 顺序；每个通道只允许一次 waveform callback，且 callback 与最终返回 map 必须一致 |
| errors | 旧 `check_errors` bool 分别映射为 `required/disabled`；路径选择在 legacy `scope.errors` gate 前完成；`core_v1` 唯一执行者；不双重 drain |
| audit | framing、声明/消费字节、trailing 长度、budget 前后摘要、session health、cleanup 原因 |

### 兼容性验收

1. 旧 descriptor 在新核心下发出与基线一致的 driver 调用，不多传 `baseline` 或新 transport
   关键字。
2. 旧 duck-typed fake 没有 `query_binary()` 时，非 opt-in 测试仍通过。
3. 新 descriptor 在旧核心下由 wheel 依赖门拒绝；强制安装时允许在新 slotted field
   构造期返回 plugin-load error，但不允许仪器 I/O。
4. 内建 DS1104/DS1000Z 的分块读取、RTM2032 的 float-list 读取和旧 screenshot 行为保持回归。
5. CLI text/JSON、run plan、`WaveformData`、capture 目录和旧 metadata reader 保持兼容。
6. capability discovery 不因 profile 存在而新增未声明 capability，也不因 driver 方法存在而自动
   opt-in。
7. backend/resource 不支持 bounded contract 时在 binary command 发送前拒绝，session 保持
   `healthy`。
8. `capture_waveforms_bounded()` 保留 `on_channel_start` 和 `on_waveform` 回调、调用顺序和已完成通道
   的 partial artifact；cleanup 失败不删除已生成证据。
9. `check_errors=true` 的 bounded descriptor 只声明 `scope.error_drain_v1` 也能进入 core executor，
   不会先被 legacy `scope.errors` capability gate 拒绝。

### 插件与实机验收

一个插件恢复标准波形 capability 前至少需要：

1. descriptor profile、bounded driver 合同、包检查和新旧核心组合测试通过；
2. 对声明的每个型号、固件和 resource/backend 组合单独证明 trailing；
3. 单通道 `DEF` 的 payload 长度、X/Y 换算和已知信号闭环在阈值内；
4. transfer state restore 由独立 fresh readback 证明；
5. 单通道、多通道、分块长记录、总预算和 no-replay 按顺序验收；
6. 涉及外部 source 时，每步前后独立确认输出关闭，不用 scope session 的健康状态
   代替 source 状态证据。

## 实施里程碑

| 里程碑 | 范围 | 退出条件 |
| --- | --- | --- |
| P0 | 已完成：冻结 core ceiling、独立 waveform recovery 模型、V2 additive 分流和错误检查映射 | 不再保留核心 API 未决项 |
| P1 | 已完成：profile、descriptor validation、opt-in factory construction barrier 和 transfer recovery 合同 | 模型、版本组合、factory 零 I/O 与失败 close 测试覆盖 |
| P2 | 已完成：标准 Service 接入 operation context、独立 bounded Protocol、typed error executor、同一 ledger 和 core-owned recovery | 单通道、多通道、callback、主失败、cleanup 和 poison 故障注入覆盖 |
| P3 | 已完成：可信 backend/resource gate、legacy 分流、CLI/run/artifact 兼容回归 | 聚焦测试、完整离线回归、Ruff、文档规则和 diff 检查通过后可随核心发布 |
| P4 | 在核心发布后由外部插件单独 opt-in 并执行受控实机验收 | 插件提高版本门，只恢复已完成验收的 capability |

P0–P3 不修改外部插件仓库；P4 不属于本核心分支的自动延伸。本文所称「已完成」仅指核心
离线实现，未包含插件版本门、硬件连接或实机验收。

## 已否决方案

- 向 `query_bin_block()` 增加 `expect_termination: bool`：无法表达精确非空 trailing、总预算、
  query count 或 resynchronization，并形成第二套 binary 合同。
- 向 `query_binary()` 公开 `transport_trailing` 关键字：会让 driver 在 operation 中临时放宽
  descriptor 和 budget 的静态边界。
- 全局关闭 PyVISA termination 等待：会改变未 opt-in driver 的边界并可能遗留字节。
- 插件直接访问 PyVISA/RsInstrument session：绕过 lease、access、audit、deadline 和 session
  health。
- 把 `fetch_trace` 无条件转成 `WaveformData`：不能覆盖现有 capture acquisition、多通道部分结果和
  采集包合同。
- 在 profile 路径失败后回退 legacy：无法证明前一条 binary response 的边界，也会隐藏
  conformance 缺口。
- 新增 `_v2` 或 `_bounded` capability：标准实验动作不变，平行 capability 会让 CLI、
  run plan 和插件发现承担双重语义。本 RFC 使用显式 profile 和可选的 bounded
  driver Protocol 选择更强的实现合同；Protocol 方法名不是新 capability。

## 已冻结的实现决定与剩余边界

1. 标准 waveform core ceiling 为 `8 MiB / 64 MiB / 256 / 64 KiB`，只约束 profile opt-in 路径。
2. `ScopeWaveformTransfer*` 是独立于 `ScopeTraceTransfer*` 的恢复模型；旧 trace import 和类型身份
   保持不变，capture profile 必须声明完整恢复闭包。
3. profile-specific bounded waveform Protocol 组属于 `wavebench.instrument.v2` additive extension；旧
   `ScopeDriver` 方法不增加参数。
4. `fetch_waveform()` 仍只返回 `WaveformData`。失败时 context、budget 和 cleanup 证据附加到结构化
   exception diagnostics，不复制命令或 payload。
5. bounded main phase 只允许 `query_binary()`；`query_bin_block()` 在发送前以
   `binary_legacy_entry_unsupported` 拒绝。
6. construction barrier 在 profile 非空的 factory 中使用稳定错误码
   `factory_construction_pending`；验证失败关闭 transport，且不发送仪器命令。
7. `ScopeConfig.check_errors=true` 固定要求 `scope.error_drain_v1`，`false` 固定禁用 typed drain；
   该分流发生在 legacy `scope.errors` capability gate 前。
8. P4 仍需由插件单独提高版本门、完成 conformance 和受控实机验收。MSO8000 的 empty trailing、
   分块预算、双通道和恢复证据不能由本核心离线实现替代。
