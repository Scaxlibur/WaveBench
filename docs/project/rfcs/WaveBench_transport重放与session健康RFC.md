# WaveBench transport 重放与 session 健康 RFC

> 状态：`Accepted`
> 修订：`R1`
> 核心基线：WaveBench `0.8.22`
> 核心实现：尚未开始
> 目标版本：下一个开发版本

## 摘要

WaveBench 当前没有统一的查询重放合同，也没有绑定物理连接代次的共享 session 健康状态。`PyVisaTransport` 会按 `read_retry_attempts` 重发文本 query 和 `*OPC?`，其他 backend 没有相同的显式行为；二进制块和浮点列表查询则已经采用失败后不重放的路径。

这类差异会让读后清除寄存器、与 acquisition 绑定的 `*OPC?`、部分响应和写入结果未知产生不可证明的后果。临时重建 Service 还会继续复用原 driver 与 transport，因此健康状态不能存放在单个 Service 中。

本 RFC 冻结以下合同：

1. 所有 query 都携带明确的重放策略，默认不重放完整命令；
2. transport 以结构化结果报告命令是否发送、响应进度和通信同步状态；
3. 核心为每次物理连接创建唯一的 `InstrumentSessionState`；
4. `GuardedAuditedTransport` 在每次仪器 I/O 前执行共享健康门禁；
5. `uncertain` 只允许核心授权的有界恢复与相关字段验证；
6. `poisoned` 禁止旧连接上的全部仪器 I/O，只允许关闭旧连接和建立新 session；
7. capture/fetch 的副作用、字段范围和恢复覆盖必须重新审计；
8. 行为变化通过核心版本、插件 wheel、descriptor 版本范围和 `api_version` 共同管理。

本文件是已接受的实现合同，但不表示上述接口已经实现或可供插件依赖。只有状态改为 `Implemented`、稳定用法同步到 `reference/` 且对应版本发布后，插件才能提高最低核心版本并使用新合同。

## 目标

- 消除 backend 之间未声明的 query 重放差异。
- 证明不可重放操作最多发送一次命令。
- 让部分响应、写入结果未知和通信失步具有稳定的错误分类。
- 让同一 driver/transport 的全部 Service 引用共享一个健康状态。
- 在 `on_failure=continue` 下阻止已失去可信度的 session 继续访问仪器。
- 为恢复与状态验证提供不能由普通调用伪造的授权边界。
- 保持现有插件调用语法可迁移，并明确可观察行为变化。

## 非目标

- 不新增 SDS3000 专用 transport 或厂商协议分支。
- 不开放 raw SCPI、任意恢复命令或调用方提供的 transport 句柄。
- 不在本 RFC 中冻结类型化 scope 状态、通用配置 patch 或 `ScopeSnapshotV2`。
- 不把重连解释为仪器配置已经恢复。
- 不以实机 happy path 代替故障注入和状态机单元测试。
- 不在合同冻结前提高任何插件的最低 WaveBench 版本。

## 当前实现事实

| 对象 | 当前行为 | 风险或缺口 |
| --- | --- | --- |
| `InstrumentTransport` | 只有 `write()`、`query()`、二进制/浮点查询和 `query_opc()` | 没有 replay policy、发送结果或 session health |
| `PyVisaTransport.query()` | `session.query()` 失败后可重发完整 query | 读后清除、动作型 query 和状态消费可能重复执行 |
| `PyVisaTransport.query_opc()` | 与普通文本 query 共用重试路径 | acquisition-bound `*OPC?` 可能重复发送 |
| PyVISA 二进制/浮点查询 | 失败后不在原 session 重放 | 已有方向正确，但错误仍缺少统一结构 |
| `RsInstrumentTransport` | 文本 query 没有 WaveBench 自己的显式重试 | 与 PyVISA 的可观察行为不同 |
| `SerialTransport` | 写命令后读取一次终止符响应 | 不支持继续读取合同，也没有共享健康状态 |
| `GuardedAuditedTransport` | 执行 access 门禁和 I/O 计数 | 不拥有 session health、连接代次和恢复授权 |
| Service/run session | 新 Service 可以复用同一 driver 与 transport | Service 私有锁存会被对象重建绕过 |
| `OperationSpec` | 描述 effect、`changed_fields`、恢复覆盖和风险 | 没有 recovery/verification 目的分类、验证字段或显式 timeout 来源；capture/fetch 字段不足 |

现有《错误处理和日志策略》把 `*OPC?` 描述为不自动重试，但当前 PyVISA 实现会进入文本 query 重试路径。代码是当前行为事实源；本 RFC 将该差异视为需要修复的 P0 问题。实现完成后再同步稳定参考文档。

## 术语

- **发送尝试**：transport 把命令交给 backend 的一次动作。
- **完整重放**：再次发送同一命令，并重新开始读取响应。
- **继续读取**：命令只发送一次，在同一响应边界内继续取得剩余数据。
- **通信同步**：transport 能证明下一个读取位置仍位于协议定义的响应边界。
- **结果未知**：命令可能已经被仪器处理，但调用方无法证明最终结果。
- **连接代次**：一次成功建立的具体底层连接。关闭或重连都会产生新的代次。
- **受影响字段闭包**：某项操作直接或间接可能改变、失效或依赖的最小字段集合。
- **恢复**：尝试把受影响字段恢复到操作前已记录的值。
- **验证**：通过独立读取证明通信、身份和相关字段满足冻结的不变量。

## 强制安全不变量

1. `no_replay` 和 `read_continuation_only` 最多发送一次命令。
2. transport 不解析 SCPI 文本来猜测重放安全性。
3. backend 不支持继续读取时必须失败，不得退化为完整重放。
4. 命令发送结果、响应进度或同步状态无法证明时采用更保守的状态转移。
5. session health 与物理连接代次绑定；重建 Service 或重新借用 lease 不得清除锁存。
6. `uncertain` 下普通 Service 操作在 transport I/O 前拒绝。
7. `poisoned` 下旧连接不执行恢复、验证、IDN 或其他探测命令。
8. 关闭旧连接属于生命周期控制，不视为旧 session 上的仪器协议 I/O。
9. 恢复成功只有在独立验证覆盖完整相关字段后才能回到 `healthy`。
10. 日志和错误结构不记录敏感 payload、真实资源串或凭据。

## 查询重放合同

### 策略

```text
safe_to_replay
no_replay
read_continuation_only
```

| 策略 | 命令发送次数 | 失败后行为 | 适用范围 |
| --- | ---: | --- | --- |
| `safe_to_replay` | 可多于一次 | 只有调用点显式声明后，才按配置重新发送完整命令 | 已审计的幂等状态 query |
| `no_replay` | 最多一次 | 返回结构化失败，不再次发送命令 | 默认策略、读后清除、动作型 query、acquisition-bound `*OPC?` |
| `read_continuation_only` | 最多一次 | backend 只继续读取当前响应；不支持时直接失败 | 能证明响应边界的分块或长响应 |

首版建议把所有 query 的默认策略冻结为 `no_replay`。仓库内调用点必须显式分类；未迁移的外部插件调用仍可运行，但采用安全的默认行为。

### 公共方法形态

R0 推荐保留现有方法名，并增加可选关键字：

```python
def query(
    self,
    command: str,
    *,
    replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
) -> str: ...

def query_opc(
    self,
    *,
    replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
) -> str: ...
```

二进制块和浮点列表查询使用同一策略类型，默认仍为 `no_replay`。是否保留统一关键字形态，以及 `read_continuation_only` 的 backend 能力表示，属于 R0 必须冻结的 API 决策。

现有不带关键字的调用保持语法有效，但默认行为改变属于可观察兼容性变化。第三方 duck-typed fake 如果需要使用新策略，必须增加对应关键字；核心不能用捕获 `TypeError` 后退回旧调用的方式绕过合同。

### `read_retry_attempts`

`read_retry_attempts` 继续被配置解析接受，但首版只影响显式 `safe_to_replay` 的完整命令重放。它不适用于 `no_replay`，也不能把不支持的 continuation 伪装成重新发送命令。

仓库内已审计为安全的 query 可以显式使用 `safe_to_replay`，从而保留原有有限重试。未分类调用不得因为配置值大于零而隐式重放。

### 调用点迁移清单

M1 以 `src/wavebench/` 中通过 `InstrumentTransport` 发起的 exchange 为迁移分母。直接调用 PyVISA 的 discovery 与 doctor 不复用 transport 重放机制，但仍必须保持单次查询。

| 调用类别 | 首版迁移策略 | 原因 |
| --- | --- | --- |
| IDN、状态、配置和测量文本查询 | 显式 `no_replay` | 先消除 backend 差异；只有后续专项审计才能改为 `safe_to_replay` |
| `SYST:ERR?` 等读后清除查询 | 显式 `no_replay` | 重放会消费下一条记录 |
| acquisition-bound `*OPC?` | 显式 `no_replay` | 重放不能证明仍绑定原 acquisition |
| 波形、截图二进制块和浮点列表 | 显式 `no_replay` | 部分响应后的边界不可假定 |
| 旧外部插件的无关键字调用 | 默认 `no_replay` | 保留源码调用语法，同时使行为 fail closed |
| `read_continuation_only` | 首版无调用点 | 当前 backend 没有已证明的继续读取能力 |

M7 增加静态合同测试，检查核心 driver 中所有 transport query 都显式传入 `replay`。新增未分类调用时测试必须失败。

## 结构化传输错误

transport 失败需要提供稳定字段，而不是只拼接错误字符串。R0 建议新增 `TransportIOError`，并保持它属于现有 `WaveBenchError`/`InstrumentError` 体系。

最低字段如下：

| 字段 | 取值示例 | 含义 |
| --- | --- | --- |
| `operation` | `query`、`write`、`query_binary` | 失败的 transport 操作 |
| `phase` | `before_send`、`sending`、`reading`、`parsing` | 失败阶段 |
| `replay_policy` | 三种策略之一 | 本次调用采用的合同 |
| `command_transmission` | `not_sent`、`sent`、`unknown` | 是否可能到达仪器 |
| `response_progress` | `none`、`partial`、`complete`、`unknown` | 响应取得程度 |
| `synchronization` | `proven`、`unproven`、`lost` | 当前协议边界可信度 |
| `attempts` | 非负整数 | 实际命令发送尝试数；发送前拒绝时为 `0` |
| `cause` | 原 backend 异常 | 保留因果链，不序列化 traceback |

错误对象不保存完整命令或响应 payload。审计日志可以记录经过现有脱敏策略处理的命令摘要、策略、发送次数、响应进度、连接代次和健康状态转移。

## session 状态所有权

### 目标对象关系

```text
instrument factory
  └─ InstrumentSessionState（核心创建，绑定一个连接代次）
      ├─ concrete transport
      ├─ GuardedAuditedTransport（最终 I/O 门禁）
      ├─ plugin driver
      └─ Service / RunService aliases
```

`InstrumentSessionState` 是唯一权威状态。factory 在成功打开具体 transport 后创建它；`GuardedAuditedTransport` 持有并执行门禁；`OpenedInstrument` 向 Service 暴露只读状态引用。插件 driver、`ScopeService` 和 `RunService` 都不能自行把状态改回 `healthy`。

每次底层连接成功建立时创建新的状态对象和不复用的 `epoch_id`。旧对象关闭后进入 `closed`；重连必须创建新对象，不能原地清除 `poisoned`。

### 通信健康状态

```text
healthy -> uncertain -> poisoned -> closed
```

允许从 `healthy` 或 `uncertain` 直接进入更保守的状态。

| 事件 | 下一状态 |
| --- | --- |
| 预检在发送前拒绝 | 保持 `healthy` |
| 写入确认未发送 | 保持 `healthy`，操作失败 |
| 结果未知且通信同步可证明 | `uncertain` |
| 通信失步或无法证明同步 | `poisoned` |
| 恢复命令失败或结果未知 | `poisoned` |
| 恢复完成，但验证不完整且通信仍同步 | 保持 `uncertain` |
| 验证期间通信同步无法证明 | `poisoned` |
| 恢复与完整验证通过 | `healthy` |
| close | `closed` |

### 配置验证不是全局布尔值

通信健康与配置可信度是两条独立轴。配置验证必须相对于操作所需的受影响字段闭包计算，不能因为一条无关 query 成功就宣称整个仪器配置已验证。

新连接可以是通信 `healthy`，但没有自动继承旧连接的已验证字段。首版用与当前连接代次绑定的 `verified_fields` 集合表示配置可信范围，新连接初始为空集合，不使用全局 `verified` 布尔值。普通只读身份查询可以作为基线验证的一部分；写入或 acquisition 操作必须满足其 `OperationSpec` 声明的前置字段要求。

## health 与操作目的矩阵

R0 建议为 `OperationSpec` 增加独立的 `session_purpose`，取值为 `normal`、`recovery`、`verification` 或 `lifecycle`。该字段不替代现有 effect 和 access policy。

| session health | `normal` | `recovery` | `verification` | `lifecycle` |
| --- | --- | --- | --- | --- |
| `healthy` | 按 access/effect 规则执行 | 仅核心授权；通常不需要 | 仅核心授权 | 允许 close |
| `uncertain` | 拒绝，零仪器 I/O | 仅核心授权 | 仅核心授权且限相关字段 | 允许 close 或建立新 session |
| `poisoned` | 拒绝，零仪器 I/O | 拒绝 | 拒绝 | 只允许关闭旧 session 和建立新 session |
| `closed` | 拒绝 | 拒绝 | 拒绝 | 只允许建立新 session |

`on_failure=continue` 只影响 run 调度，不产生 recovery/verification 授权，也不改变 session health。

## 恢复与验证授权

恢复授权由核心事务协调者创建，必须同时绑定：

- 当前 `InstrumentSessionState` 和 `epoch_id`；
- 发起恢复的 operation ID；
- `recovery` 或 `verification` 目的；
- 允许的 I/O 类别；
- 受影响字段闭包；
- 有界步骤和超时。

授权只在持有 session 事务锁的动态范围内有效。普通 Service、run step、插件和调用方不能构造授权 token，也不能用布尔参数绕过门禁。`GuardedAuditedTransport` 在每次真实 I/O 前复核健康状态、授权、连接代次和 access policy。

插件可以实现已冻结的恢复和验证动作，但不能提供 raw SCPI、调用方命令列表或任意字段范围。恢复成功后，核心仍需执行独立验证；验证闭包不完整时保持 `uncertain`。

## `OperationSpec` 接入

除 `session_purpose` 外，R0 还需要冻结以下语义：

- `changed_fields` 表示操作期间可能触碰的字段，不只表示最终保留的变化；
- `verification_fields` 表示恢复后必须独立验证的相关字段闭包；
- `restore_coverage` 说明哪些字段有正式恢复合同；
- `timeout_source` 或等价字段说明普通操作、恢复和验证分别受哪个已配置 timeout 约束；
- effect、risk 和 access policy 继续承担现有职责。

以下操作必须在 P1 类型化状态工作前完成专项审计：

```text
scope.capture
scope.capture_waveforms
scope.capture_multiple
scope.fetch_waveform
```

审计至少覆盖 run/stop、trigger/acquisition、时基、垂直比例、trace、waveform source/format/range、传输状态、capture identity 和插件实际临时修改的字段。

## 兼容性与版本门

| 组合 | 预期行为 |
| --- | --- |
| 旧核心 + 旧插件 | 保持旧版本行为 |
| 新核心 + 旧插件 | 旧调用语法继续有效；未显式声明策略的 query 默认 `no_replay` |
| 旧核心 + 新插件 | wheel 和 descriptor 最低版本在 driver factory 前拒绝 |
| 新核心 + 新插件 | 使用完整 replay/session 合同 |

R0 需要单独裁决 `wavebench.instrument.v2` 是否保持兼容。只有现有插件方法和调用语法仍然有效时才保留 V2；若最终 API 要求插件实现不兼容的新合同，则必须升级核心 API 常量和 descriptor。

插件采用新能力前必须同时检查：

1. P0 已进入正式 WaveBench 版本；
2. wheel `Requires-Dist` 下限指向首个包含 P0 的版本；
3. descriptor `wavebench_min_version` 同步提高，并重新评审上限；
4. descriptor `api_version` 与核心冻结结果一致；
5. registry 在 driver factory 和 transport I/O 前拒绝不兼容组合。

## 测试矩阵

| 层级 | 必测内容 | 默认连接仪器 |
| --- | --- | --- |
| replay policy | 三种策略、发送次数、unsupported continuation、默认 `no_replay` | 否 |
| structured error | 发送前失败、结果未知、部分响应、同步可证/不可证、cause | 否 |
| backend contract | PyVISA、RsInstrument、Serial、Guarded 和 fake 行为一致 | 否 |
| session state | 全部状态转移、共享 alias、close/reconnect、旧 token 失效 | 否 |
| authorization | `uncertain` 普通 I/O 零发送、授权范围、字段闭包和超时 | 否 |
| run behavior | `on_failure=continue` 不清锁存，后续同 session I/O 被拒绝 | 否 |
| OperationSpec | capture/fetch 真实副作用、恢复与验证覆盖 | 否 |
| plugin versions | wheel、descriptor min/max、`api_version`、entry point | 否 |
| opt-in hardware | 已发布合同在批准 backend 和仪器上的补充验证 | 是 |

测试必须断言具体命令发送次数，不能只检查最终异常类型。实机验收不替代发送次数、授权和状态机测试。

## 开发里程碑

| 里程碑 | 范围 | 离线验收 |
| --- | --- | --- |
| M1 | 冻结本 RFC，建立 transport exchange 调用点清单 | RFC 状态为 `Accepted`，每类调用都有迁移规则 |
| M2 | 实现 `ReplayPolicy`、`TransportIOError` 和纯 `InstrumentSessionState` 模型 | 默认策略、JSON 错误和全部状态转移的单元测试 |
| M3 | 接入 PyVISA、RsInstrument、Serial 和测试 fake | 发送次数、不支持 continuation 与 backend 一致性测试 |
| M4 | 在 `GuardedAuditedTransport` 接入健康门禁和核心授权 | `uncertain` 普通 I/O 零发送，`poisoned` 只允许 close |
| M5 | 在 factory、`OpenedInstrument` 和 Service alias 共享连接代次 | Service 重建不清除锁存，close/reconnect 产生新代次 |
| M6 | 接入 run 调度和审计证据 | `on_failure=continue` 不允许已中毒 session 继续 I/O |
| M7 | 审计 capture/fetch `OperationSpec`，迁移核心调用点并同步稳定文档 | 零个未分类核心调用，全量离线测试通过 |

M7 完成后仍不自动进行实机验证。实机验证必须在核心版本发布后单独授权，且不替代上述离线发送次数和状态机测试。

## 已否决方案

- **把 health 放在 Service 中**：Service 会被重建，同一 transport 会出现多个状态副本。
- **把 health 交给插件 driver**：核心安全合同不能依赖每个插件自行实现。
- **只在 RunService 中阻止后续步骤**：one-shot Service、TUI 和其他调用路径仍可绕过。
- **根据命令字符串猜测幂等性**：transport 不理解厂商状态和操作上下文。
- **捕获异常后自动重连并重试**：新连接不证明旧操作结果，也不证明仪器配置已恢复。
- **在 `poisoned` session 上发送 IDN 探测**：无法证明响应边界安全，探测本身仍是仪器 I/O。
- **让调用方提供恢复命令**：会形成绕过 capability、access 和审计的 raw SCPI 通道。

## R1 冻结决策

- `query()`、`query_opc()`、二进制块和浮点列表查询都增加只能以关键字传入的 `replay` 参数，不增加平行方法。
- 全部查询的默认策略为 `no_replay`；`read_retry_attempts` 只影响显式 `safe_to_replay`。
- 首版 backend 均不声明 continuation 能力；收到 `read_continuation_only` 时在发送前返回 `TransportIOError`，`attempts=0`。后续 backend 只能通过明确能力和专项测试加入。
- `TransportIOError` 继承 `InstrumentError`，保存本 RFC 定义的结构字段，通过现有 `wavebench.error.v1` envelope 的 `details` 对外表示。
- factory 在具体 transport 打开后创建唯一 `InstrumentSessionState`；`OpenedInstrument`、guarded transport 和所有 Service alias 共享该对象。close 只能进入 `closed`，reconnect 创建新对象和新 `epoch_id`。
- session 事务锁由 `InstrumentSessionState` 持有；恢复与验证授权只能由核心 transaction coordinator 在持锁的动态范围内安装。
- `OperationSpec` 增加 `session_purpose`、`verification_fields` 和 `timeout_source`；默认分别为 `normal`、空集合和 `connection.timeout_ms`。
- 新连接的通信初始为 `healthy`，`verified_fields` 初始为空。身份和配置基线由具体操作的前置字段声明决定，不把一次 IDN 成功扩大为全局验证。
- `wavebench.instrument.v2` 保持不变：新参数属于核心提供的 transport 合同，现有 driver 方法和插件 factory 形态不变。
- 核心 driver 调用点在 M7 前全部显式分类。外部旧插件保持调用语法有效并获得 `no_replay` 默认；新插件的 wheel、descriptor 和四组合版本测试只在核心发布后更新。
