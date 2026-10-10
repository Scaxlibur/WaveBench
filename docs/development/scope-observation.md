# 开发线示波器观察

> 状态：`Proposed / Future`，开发线已实现、尚未正式发布。
> 本页面向实现与测试人员，记录开发线的执行合同；不表示已安装的正式版本提供这些入口。

## 实现入口与职责

CLI 的 `scope observe` 和 MCP 的 `scope.observe` / `scope.advise` 由
`services/agent_observe.py`、`services/agent_advise.py`、`ScopeService` 与 `mcp_http.py`
共同实现。CLI 参数以本开发线的 `cli_parser.py` 和离线 `--help` 为准。
Core 表达观察、访问约束、会话和报告语义；型号通道、端接与传输差异由 descriptor 和 driver
提供，不在观察层维护型号范围表。

MCP 的 `doctor.config` 同属未发布入口，调用 `doctor_records()` 并返回结构化检查记录。
它不读取波形或执行建议；实际查询合同以 doctor 与 driver 实现为准。

## 观察与建议

默认 `scope observe` 只获取身份、可用状态与输入耦合，不读取波形。观察路径收紧有效访问
权限，原配置为 `disabled` 时在打开连接前拒绝。只有具备纯查询合同的状态才进入结果；缺少字段或
capability 时保留不可用原因，不把部分状态当作完整快照。

MCP `scope.advise` 依据可用状态和调用方的 `expected_frequencies_hz` 提供显示或时基建议，
不会执行建议。无可用档位、测量值或期望频率时返回逐通道 `advice_unavailable`，表示无法
评估当前设置。查询失败不能生成「无需调整」的判断。调用方提供的频率只标为 configured，
不能当作实测证据；带低周期告警的测量频率不能直接用于时基建议。

生成的 focus 建议遵循现有 CLI 格式：`--vertical-scale CHANNEL=V_PER_DIV`；
隐藏其它通道使用 `--hide-others`。建议的执行仍须经过命令自身的 access、capability 和
运行时安全检查。

## 显式波形报告与预检

`scope observe --fetch-waveform` 会操作仪器，可能停止采集、启用通道显示、改变波形传输
source/mode/format/points，以及按配置消费错误队列。它不恢复原来的运行状态。MCP 不提供
这一读取路径。

`--target-cycles` 与 `--target-vertical-divisions` 必须是有限正数；expectation 的字段名、
类型、有限性与取值范围由 `data/expectations.py` 校验。这些输入在创建仪器服务前拒绝。
型号相关的通道支持验证可能需要连接后的纯查询预检；全部请求通道必须在第一次采集写入
前完成验证。它不是「任意型号输入错误都在打开会话前拒绝」的保证。

离线 access、capability 或波形配置预检失败时不构造 driver 或 transport。观察路径的
factory 构造阶段对所有 descriptor 启用 Core I/O 锁，禁止通过 context transport 查询或
写入；构造与声明校验完成后才释放。需要在构造阶段进行设备初始化的旧插件不能使用这一
入口，应将设备操作移入明确的执行方法。该约束只保护 Core transport，不提供 Python 沙箱。

报告在受控持续会话和独占资源租约内执行。最终高阻确认与波形读取共用会话和租约，避免
遵守同一资源锁的其它进程在两者之间改变输入设置；资源锁不能阻止前面板或外部软件操作。
不确定 I/O 或会话健康故障使剩余采集停止，并记录中止原因；不自动重连继续写入。

持续会话不证明波形来自同一次 acquisition。报告仍将 `same_acquisition` 标为 false，
跳过相位、相关性、延迟和交点，保留同步无关摘要。正式同步时序分析应使用
`scope capture --synchronized` 产物和既有 `analysis.pair` 同步证明合同。
`data.relationships` 的默认值同样不认定同步；其显式断言仅供已确认来源的内部分析。

## 期望检查与输出

`--expect <file.toml>` 需要显式波形报告；示例格式为：

```toml
[channels.1]
frequency_hz = 1000
frequency_tolerance_ratio = 0.05
vpp_v = 3.3
duty_percent = 50
```

汇总保留全部待验收通道。无法取得或评估波形时通道为 `unavailable`；全部不可用时总体为
`unavailable`，有可用验收但不完整时为 `partial`，已确认的 `fail` 优先。
无可执行期望指标时为 `skipped`。报告成功返回不等于所有期望通过，调用方须检查验收汇总
以及 warnings 和逐通道结果。

## 维护与验收

聚焦验证覆盖生命周期关闭与借用、跨进程租约竞争、非法后续通道零采集写入、会话故障后的
剩余通道中止、禁用配置及离线失败零构造、legacy factory I/O 拦截、真实 guard 的观察零写，
以及缺证据建议与同步默认值。测试只使用 fake
transport 和合成信号，不连接设备；实机 evidence 属于单独授权的插件验证。

发布时依据实际 tag 和实现将正式可用行为转入 CLI Reference 和 MCP How-to，并保留唯一
事实来源。本页不提前承诺发布版本、插件支持范围或硬件验收结论。

相关合同见[安全模型](../concepts/safety-model.md)、[会话与恢复](../concepts/sessions-and-recovery.md)、
[插件模型](../concepts/plugin-model.md)和[测试说明](testing.md)。
