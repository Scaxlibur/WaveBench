# RFC-0002：示波器通道输入状态 V2

> 状态：`Implemented R1（未发布）`
> 核心基线：现有 `scope.channel_coupling` 与高阻安全门
> 目标：分开表达 coupling 与 termination
> 系列总览：[scope 可移植性 RFC 组合说明](WaveBench_scope可移植性RFC组合说明.md)

## 摘要

现有 `channel_coupling(channel) -> str` 同时承载显示耦合和输入终端证明。部分仪器把
termination 编入 `ACL/DCL/AC/DC` token，另一些仪器使用独立 query。为了保留两项真实状态，
本 RFC 已追加输入状态 V2，不修改旧 coupling token 或旧 capture 安全门。

## 核心实现状态

核心开发线已经提供 `ScopeChannelInputStateV2`、独立 Protocol、
`scope.channel_input_state_v2`、`ScopeService.channel_input_state_v2()` 和
`wavebench scope channel-input-state`。该 capability 不需要 descriptor profile，但会触发
factory construction barrier；缺方法、无效版本或 factory 内 I/O 都在第一次仪器命令前失败。

R1 同时提供 V2 termination 的纯判断规则：`high_z` 通过，已明确允许时 `50_ohm` 通过，
`unknown` 始终拒绝。标准 fetch/capture 仍使用 legacy coupling gate，不会因为 descriptor
同时声明 V2 而改变调用顺序。

本状态只表示核心离线实现完成。外部插件必须等待正式核心发行版本，再提高版本门或声明该
capability。

## 当前问题

只读取 AC/DC/GND 无法判断输入是高阻还是 50 Ω。把两项设备状态压成旧 token 可以继续满足
现有安全门，但会丢失原始 coupling 语义，也无法单独展示 termination。

反过来，如果直接把旧 `AC` 或 `DC` 当作高阻，也可能在 50 Ω 输入上继续执行采集。安全
判断必须使用 descriptor 规定的终端策略和实际读取结果，不能靠字符串相似度推断。

## 公共模型

~~~python
ScopeInputCoupling = Literal["ac", "dc", "gnd", "unknown"]
ScopeInputTermination = Literal["high_z", "50_ohm", "unknown"]


@dataclass(frozen=True, slots=True)
class ScopeChannelInputStateV2:
    channel: int
    coupling: ScopeInputCoupling
    termination: ScopeInputTermination
    impedance_ohm: float | None = None
    unavailable_fields: tuple[Literal["impedance_ohm"], ...] = ()
~~~

Protocol 与 capability：

~~~python
class ScopeChannelInputStateDriverV2(Protocol):
    def get_channel_input_state_v2(
        self,
        channel: int,
    ) -> ScopeChannelInputStateV2: ...
~~~

~~~text
scope.channel_input_state_v2 -> get_channel_input_state_v2
~~~

operation 同样使用 `scope.channel_input_state_v2`。它是
`stateful_read / exclusive`，不提供输入设置或终端切换。

## 模型不变量

- `channel` 是正的非 bool 整数；
- `impedance_ohm` 非空时必须为有限正数；
- `impedance_ohm` 只保存设备实际报告或厂商合同明确换算的值；
- `impedance_ohm is None` 时 `unavailable_fields == ("impedance_ohm",)`，非空时该 tuple
  必须为空；
- `termination` 是安全分类，不从 coupling 推导；
- `coupling="gnd"` 不隐含任何 termination；
- 设备返回未识别但语法完整的 token 时可返回 `"unknown"`；
- 响应缺失、格式损坏或 query 失败时 operation 失败，不能返回 `"unknown"`。

R1 冻结上述小写枚举和 availability 语义。后续修订不得退回大小写不一致的多组字符串。

所有 public dataclass 必须在 `__post_init__` 中执行本 RFC 的类型、范围、枚举和
availability 不变量。构造失败属于参数或 driver contract failure；Service 不修正无效对象。

## 高阻安全规则

### Legacy 路径

没有声明新 capability 的 descriptor 继续使用现有 `scope.channel_coupling`：

- `fixed-high-impedance` 继续按 descriptor 固定策略处理；
- `switchable-termination` 的既有组合 token 继续保持当前兼容行为；
- 旧 `require_high_impedance()`、CLI 参数和 capture 返回值不改变。

R1 不让旧 switchable descriptor 强制迁移，否则新核心会改变旧插件在发送前的行为。

### V2 路径

明确选择输入状态 V2 的新 operation 使用以下门：

| termination | 默认结果 | 显式允许 50 Ω 后 |
| --- | --- | --- |
| `high_z` | 通过 | 通过 |
| `50_ohm` | 拒绝 | 通过 |
| `unknown` | 拒绝 | 仍拒绝 |

`allow_50ohm` 只能授权已证明的 `50_ohm`，不能把 unknown 解释成 50 Ω。以后若让标准
capture 采用 V2，必须由 descriptor 显式 opt-in，并单独冻结与 legacy coupling gate 的分流。

## Capability 与 factory

- capability 声明但缺少 `get_channel_input_state_v2()` 时，factory 在仪器 I/O 前拒绝；
- 方法存在但 capability 未声明时，不产生隐式能力；
- 该 capability 不要求 descriptor profile；
- construction barrier 必须覆盖 opt-in factory 内的 query/write；
- 当前开发线的静态版本下限为 `0.8.24`；它不是外部插件的发行授权；
- 参数错误、无效 channel 和不支持的安全策略在仪器 I/O 前失败。

## 序列化

JSON 应分别保存：

~~~json
{
  "channel": 1,
  "coupling": "dc",
  "termination": "high_z",
  "impedance_ohm": null,
  "unavailable_fields": ["impedance_ohm"]
}
~~~

`impedance_ohm=null` 表示没有精确数值，不影响已由独立设备 token 证明的
`termination="high_z"`。CLI 不再把该对象格式化回 `DCL`，旧 coupling 命令仍可继续返回
旧 token。

## 兼容性与迁移

1. 保留 `scope.channel_coupling -> channel_coupling`。
2. 不修改旧 `ScopeDriver`。
3. 不增加输入 setter。
4. 不自动迁移 standard fetch/capture。
5. 新插件可以同时声明旧 coupling 和 V2 input state；两项 capability 在运行时独立，V2
   operation 不为比较结果额外调用 legacy `channel_coupling()`。
6. 插件 conformance 必须用同一组设备状态 fixture 验证 legacy token 与 V2
   coupling/termination 映射一致。若未来需要运行时交叉比较，应另增静态映射 profile 和固定
   query 顺序，不能隐式增加 I/O。

## 验收矩阵

- 模型：channel、有限阻抗、枚举和 bool-as-int 负向测试；
- 安全门：high-Z、50 Ω 默认拒绝、显式放行、unknown 始终拒绝；
- coupling：AC/DC/GND 与 termination 独立组合；
- factory：缺 capability、缺方法、额外方法和 construction barrier 零 I/O；
- failure：query/解析错误不转换成 unknown；
- compatibility：旧 coupling、旧 CLI、旧 fake、DS/RTM 和 standard capture 回归；
- serialization：字段稳定，null 不被替换成 0 或默认阻抗。

## 实施边界

本 RFC 不授权修改真实仪器输入终端，也不把输入状态 V2 自动加入所有 capture。R1 只提供
纯读取和安全判定；随后需要真实 termination 的新 operation 必须显式依赖本 capability，不能
改写 legacy capture 的前置检查。
