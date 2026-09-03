# 安全模型

WaveBench 的安全判断以「这次 operation 是否可执行」为单位，而不是以某个配置项或一次旧查询为单位。通过 `run check` 不等于接线正确，也不等于可以安全写入仪器。

## 四道门

1. **Operation 合同**定义操作的副作用和所需输入。
2. **access policy** 限制配置允许的副作用；`read_only` 只能执行观察类操作。
3. **descriptor capability** 表示已安装的 driver 声明支持该操作。
4. **运行时 preflight 与 readback** 检查当前状态、配置上限、连接和操作后的结果。

任何一层不足都应拒绝操作。静态 capability 不等于当前仪器状态，也不等于现场接线、安全限值或人工授权。

## 失败时的行为

写入、读取或状态确认出现不确定性时，服务不会把先前的成功当成持续授权，也不会无条件重试同一写入。恢复只在操作合同明确允许、会话仍可安全使用时执行，并且必须由独立 readback 证明结果。无法证明状态时，后续写入应停止。

## 人工责任

配置只能声明已确认的资源和限制，不能替代现场核对。执行可能改变输出、采集或供电状态的操作前，仍须确认接线、端接、输入额定值、设备身份和预期副作用。

## 相关页面

- [会话与恢复](sessions-and-recovery.md)
- [Capability 模型](capability-model.md)
- [配置 Reference](../reference/configuration.md)
- [执行一次实验](../how-to/run-an-experiment.md)
