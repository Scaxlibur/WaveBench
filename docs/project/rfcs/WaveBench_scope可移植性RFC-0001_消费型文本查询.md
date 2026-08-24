# RFC-0001：消费型文本查询与错误队列

> 状态：`Superseded R1`
> 原提案：新增 `query_text_once()`
> 核心裁决：使用统一 replay 合同和 `scope.error_drain_v1`
> 相关规范：[transport 重放与 session 健康 RFC](WaveBench_transport重放与session健康RFC.md)、[scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)
> 系列总览：[scope 可移植性 RFC 组合说明](WaveBench_scope可移植性RFC组合说明.md)

## 摘要

消费型文本 query 的问题成立，但原提案的平行 transport 方法不再采用。核心已经为
`InstrumentTransport.query()` 增加显式 `ReplayPolicy`，默认值为
`ReplayPolicy.NO_REPLAY`。错误队列则由受管的 `scope.error_drain_v1` capability 提供
类型化、有限次、可对账的 drain。

本 RFC 记录替代关系，不新增运行代码。

## 问题

`:SYSTem:ERRor?`、`*ESR?` 和其他读后清除 query 可能在首次发送后已经消费设备状态。如果
响应在返回途中失败，再次发送相同命令会观察下一条状态，调用方无法证明第一次响应内容或
消费次数。

不可重放只解决「不得再次发送命令」，不消除读后清除的语义副作用。将这类 query 放入
snapshot 或普通状态读取时，仍须在 operation 中声明其状态变化和 artifact 证据。

## 核心裁决

### Transport 入口

公共入口保持统一形态：

~~~python
def query(
    self,
    command: str,
    *,
    replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
) -> str: ...
~~~

强制规则：

1. `NO_REPLAY` 最多发送一次完整命令。
2. 发送、响应进度或同步状态无法证明时返回结构化 `TransportIOError`。
3. transport 不根据 SCPI 字符串猜测幂等性。
4. `read_retry_attempts` 只影响显式 `SAFE_TO_REPLAY`。
5. 不支持 continuation 的 backend 不得用完整重放代替继续读取。

因此不再增加 `query_text_once()`。平行方法会重复 replay、审计、session health 和 backend
一致性合同，也会让普通 query 与消费型 query 形成两套错误语义。

### 类型化错误队列

新 scope operation 需要错误检查时，只接受：

~~~text
scope.error_drain_v1 -> drain_errors(max_records=...)
~~~

`drain_errors()` 必须：

- 每次 query 只解析一条错误或一个文档化结束 token；
- 最多公开 `max_records` 条错误；
- 额外执行一次证明 query；
- 以 `query_count == len(records) + 1` 证明正常终止；
- 队列超过上限时保留脱敏的 overflow record，并以 `error_queue_incomplete` 失败；
- 与 guarded transport 的实际 query 增量对账；
- 不执行自动 clear、peek 或 binary I/O。

错误队列 query 失败必须保留 transport/session 异常，不能返回伪造的空队列。

## 旧 `scope.errors` 兼容边界

旧 `scope.errors(limit) -> list[str]` 保持原签名和成功值，只能记录
`legacy_unstructured` artifact：

- `terminated=null`；
- `query_count=null`；
- 不从列表内容推导厂商结束 token；
- 不作为 R1.3 `required` 或 `if_supported` 的能力证明；
- 不与核心 `scope.error_drain_v1` 在同一次 operation 中双重 drain。

`ScopeConfig.check_errors=true` 在 bounded waveform 路径中固定要求
`scope.error_drain_v1`；`false` 固定禁用错误队列读取。旧 `scope.errors` 不能替代该门。

## Snapshot 与状态读取

`NO_REPLAY` 不表示消费型寄存器适合进入普通 snapshot：

- 没有独立字段和 changed-field 合同时，`*ESR?` 不进入 snapshot；
- `*STB?` 若具有读后清除语义，首版可组合 snapshot 应保持 unavailable；
- 后续若确需暴露消费型状态，应新增明确的 stateful-read operation，不得伪装成无副作用字段。

因此 RFC-0005 不以本 RFC 为硬依赖。缺少消费型健康字段不会阻止其他 snapshot 分区返回。

## 插件采用条件

外部插件声明 `scope.error_drain_v1` 前必须提供：

1. 厂商文档化的结束 token；
2. 错误 code/message 的严格解析；
3. 消息包含逗号、引号和控制字符的负向测试；
4. `max_records+1` 查询次数和 overflow 对账；
5. `NO_REPLAY` 发送次数测试；
6. 查询失败、session unhealthy 和 deadline 的结构化失败；
7. wheel 与 descriptor 指向首个正式包含该合同的核心版本。

插件没有该 capability 时可以继续保留旧 `scope.errors`，或在允许的 operation 中显式禁用
错误检查；不能发送 `*CLS` 掩盖合同缺失。

## 验收与结案

核心侧结案条件：

- transport 默认 `NO_REPLAY` 和结构化失败已有单元测试；
- `scope.error_drain_v1` 的 Protocol、phase、查询上限和 artifact 已冻结；
- 旧 `scope.errors` 的成功返回和 artifact 保持兼容；
- bounded waveform 的 `check_errors` 分流在主 I/O 前完成；
- 文档明确原 `query_text_once()` 提案已被取代。

具体仪器错误队列的厂商解析和实机验证不属于本核心 RFC 的完成范围。
