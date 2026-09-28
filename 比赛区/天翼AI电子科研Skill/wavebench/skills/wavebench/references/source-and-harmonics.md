# 信号发生器与谐波操作

> 加载时机：涉及信号发生器、源通道、输出、扫频、pulse、burst、调制或谐波时加载。
> 本文件不依赖其他 reference。

## 只读确认

先读取当前命令和能力：

```bash
.venv/bin/wavebench source idn --config wavebench.toml
.venv/bin/wavebench source status --channel 1 --config wavebench.toml
.venv/bin/wavebench plugin list --load
.venv/bin/wavebench plugin installed
.venv/bin/wavebench plugin doctor --load
```

不要假定某个设备名称、README 或插件列表提供了目标操作。确认实际驱动 ID、来源、版本和 capability contract。

## 基本 setter 与输出

通过实时写入门后，使用显式参数：

```bash
.venv/bin/wavebench source set-freq --channel 1 1000 --config wavebench.toml
.venv/bin/wavebench source set-vpp --channel 1 1.0 --config wavebench.toml
.venv/bin/wavebench source output --channel 1 on --config wavebench.toml
```

- setter 不隐式切换输出状态；
- `source output` 只改变输出状态，不替换频率、幅度或函数；
- 每一个输出转换都要单独授权和验证；
- 操作结束后重新读取输出、函数、频率、Vpp 和方波占空比。

不要把 setter 成功解释为后续输出转换的授权。

## 高级配置

在 sweep、pulse、burst、coupling、modulation 或 harmonic 配置前：

1. 确认输出处于关闭状态；
2. 记录完整源状态和保护上下文；
3. 检查当前驱动帮助和能力；
4. 设定不超过配置限制的参数；
5. 配置完成后重新读取设置；
6. 只有在用户明确授权后才打开输出或触发。

若驱动契约明确证明了另一种安全转换顺序，记录该依据；否则维持输出关闭。

## 会话边界

输出状态或手动触发授权可能绑定到持久驱动会话。需要连续执行的配置、触发和验证必须保持同一有效会话，不要在命令之间无依据地重新建立连接。

会话中断、超时或响应含糊时，停止后续写入，查询实际输出状态并保存证据。

## 谐波

受控谐波操作必须同时具备实际驱动声明的：

- `source.harmonic_profile`；
- `source.harmonic_configure`。

公共受控写入仅限驱动已经验证的低阶预设。不得暴露或猜测 USER mask、逐分量幅度/相位 setter，也不得用裸 SCPI 绕过 capability gate。

不要假设存在 harmonic CLI 命令。先查看：

```bash
.venv/bin/wavebench source --help
```

CLI 未提供该功能时，使用公开的 Python service/driver contract，并记录版本和能力证据。

谐波验收至少包括：

- 外部示波器采集；
- 活跃阶次与非活跃阶次区分；
- 频率、幅度和容差；
- 源状态及示波器设置恢复；
- 完整恢复是否确实由验收路径承诺。

## 恢复声明

基础源恢复通常只涵盖：

- 输出；
- 函数；
- 频率；
- Vpp；
- 方波占空比。

除非快照和验收路径明确支持，否则不宣称恢复偏置、相位、频率模式、扫频、负载、极性、噪声、同步、burst、调制、marker、pulse hold 或易失性 ARB 内存。

## 失败处理

写入超时、输出状态不明、谐波配置失败或恢复失败时：

- 不盲目重试；
- 停止后续输出和触发；
- 将受影响输出保持关闭；
- 重新读取真实仪器；
- 保留已有波形和日志；
- 报告未恢复字段和人工恢复要求。
