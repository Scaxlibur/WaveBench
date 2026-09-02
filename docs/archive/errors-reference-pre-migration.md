# 归档：重构前的 WaveBench 错误处理和日志策略

> [!NOTE]
> 本页保留文档重构前的错误、日志和 session 说明。当前 error envelope、退出码和敏感信息边界以实现与测试为准；请阅读[错误 Reference](../reference/errors.md)和[run plan 排错](../how-to/troubleshooting.md)。

## 核心原则

仪器控制的错误处理需要同时满足三个要求：

1. 操作失败时尽早终止，不返回成功状态；
2. 错误信息包含命令和仪器响应等必要上下文；
3. 已生成的采集证据和失败位置可以继续检查。

## 错误分类

当前实现将错误分为五类。

### `ConfigError`

本地配置错误，不应尝试连接仪器。

常见情况：

- 找不到 `wavebench.toml`，也没有通过 CLI 提供 `--resource`；
- `backend` 不是 `lan`；
- `scope.driver` 不是 `rtm2032`、`ds1104` 或 `ds1000z`；
- RTM2032 的 `default_channel` 不是 1 或 2，或 DS1104/DS1000Z 的通道不在 1–4；
- `format` 不是 `real`；
- `points` 不是 `dmax`。

示例提示：

```text
ConfigError: missing connection.resource.
Set it in wavebench.toml or pass --resource TCPIP::<ip>::INSTR.
```

### `ConnectionError`

连接阶段失败。

常见情况：

- 仪器 IP 不通；
- VISA 后端未安装或不可用；
- VISA resource 字符串错误；
- 示波器未开机；
- 防火墙阻断。

示例提示：

```text
ConnectionError: failed to open TCPIP::192.0.2.10::INSTR.

Check:
1. Is the oscilloscope powered on?
2. Can you ping the instrument IP?
3. Is R&S VISA / NI VISA installed?
4. Is the VISA resource string correct?
```

连接错误不生成采集包。

### `InstrumentError`

仪器接受到了命令，但错误队列报告问题。

例如：

```text
SYST:ERR? -> -113,"Undefined header"
```

示例提示：

```text
InstrumentError after command: CHAN1:DATA:POIN DMAX
SYST:ERR?: -113,"Undefined header"
```

这类错误通常说明 SCPI 命令不被当前设备支持，或者命令写法与手册不一致。

### `OperationTimeout`

等待仪器操作完成时超时。

常见情况：

- `AUToscale` 后 `*OPC?` 等不到；
- `SINGle` 后没有触发；
- 波形数据读取太慢；
- 信号没有满足触发条件。

示例提示：

```text
OperationTimeout: waiting for capture to complete timed out after 30000 ms.

Command: SINGle + *OPC?

Possible causes:
- Trigger condition was not met.
- Trigger mode is normal and no valid edge occurred.
- Acquisition time is too long.
- opc_timeout_ms is too short.
```

### `DataError`

连接和命令可能成功，但返回数据不符合预期。

常见情况：

- waveform header 解析失败；
- header 点数和实际波形点数不一致；
- 波形数据为空；
- `x_stop <= x_start`；
- `points < 2`。

示例提示：

```text
DataError: waveform header says 5000 points, but received 0 samples.
```

## 退出码

当前定义以下退出码：

```text
0  成功
1  一般错误
2  配置错误
3  连接错误
4  仪器错误
5  超时
6  数据错误
```

批处理程序可以根据退出码判断是否继续。

## 终端输出原则

终端输出给人看，应该短而明确，并提供下一步检查方向。

不推荐：

```text
Error: VI_ERROR_TMO
```

推荐：

```text
Timeout while waiting for capture to complete.
Command: SINGle + *OPC?
Timeout: 30000 ms

Try:
- Use `wavebench scope fetch` if the waveform is already stopped.
- Check trigger mode on the oscilloscope.
- Increase opc_timeout_ms in wavebench.toml.
```

## 日志分类

当前实现有两类日志。

### 程序运行日志

输出到终端，用于提示当前进度。

示例：

```text
[INFO] Opening TCPIP::192.0.2.10::INSTR
[INFO] IDN: Rohde&Schwarz,RTM2032,...
[INFO] Running autoscale...
[INFO] Autoscale done.
```

程序日志使用 Python `logging`，不额外引入日志框架。

推荐参数：

```bash
--verbose
--quiet
```

行为：

- 默认：显示关键步骤；
- `--verbose`：显示更多细节；
- `--quiet`：只显示结果和错误。

### `commands.log`

`commands.log` 是采集包的一部分，用于复盘 SCPI 命令和响应。

示例：

```text
2026-04-29T01:15:30.123 WRITE *CLS
2026-04-29T01:15:30.130 QUERY *IDN?
2026-04-29T01:15:30.145 RESP Rohde&Schwarz,RTM2032,...
2026-04-29T01:15:30.160 WRITE FORM REAL
2026-04-29T01:15:30.170 WRITE FORM:BORD LSBF
2026-04-29T01:15:30.180 WRITE CHAN1:DATA:POIN DMAX
2026-04-29T01:15:30.200 WRITE SING
2026-04-29T01:15:30.210 QUERY *OPC?
2026-04-29T01:15:31.532 RESP 1
2026-04-29T01:15:31.540 QUERY CHAN1:DATA:HEAD?
2026-04-29T01:15:31.550 RESP -4.998E-07,5.000E-07,5000,1
2026-04-29T01:15:31.570 QUERY_BINARY CHAN1:DATA?
2026-04-29T01:15:31.880 RESP_BINARY <20000 bytes>
```

二进制波形不要完整写入 log，只记录字节数或样本数。

长波形读取还记录 `telemetry` 条目，包括 preamble、各数据块、整体传输和数值换算的
单调耗时，以及数据块范围、字节数和吞吐率。RIGOL 二进制块失败时还会记录失败块范围
和耗时，然后终止本次采集；WaveBench 不会在响应边界未知的原 session 中再次发送同一
查询。若后续仍需操作仪器，调用方应显式关闭并新建 session，再重新执行完整采集；当前
不会自动完成 session 重建或从失败块续传。RTM2032 浮点列表读取同样不在原 session 内
重放，但当前没有 RIGOL 逐块读取那样的块级 telemetry。

`commands.log` 要回答的问题是：

> 脚本刚才到底对仪器说了什么？

同一个物理 resource 当前按串行独占使用。WaveBench CLI 进程会在打开 transport 前取得
跨进程 resource lock；锁忙时返回 `resource_busy`，不会打开后续 transport。VXI-11/SCPI
响应仍可能被未使用 WaveBench 锁的外部程序交叉消费，出现 IDN、错误队列或二进制响应串线。
完成或失败一个命令并关闭 session 后，再执行下一条命令。原生 Windows 与 WSL 的锁互操作
不在当前保证范围内。

## 采集包生成规则

### 成功

成功采集生成正常采集包：

```text
data/raw/20260429_011530_ch1/
├─ ch1.csv
├─ ch1.npy
├─ metadata.json
└─ commands.log
```

### 失败

如果已经成功连接仪器，之后失败，则保留失败采集包：

```text
data/raw/20260429_011530_ch1_failed/
├─ metadata.partial.json
├─ commands.log
└─ error.txt
```

失败包不要自动删除。失败现场本身有排查价值。

### 不生成采集包的情况

```text
配置错误：不生成采集包
连接错误：不生成采集包
连接成功后失败：生成 *_failed 采集包
成功：生成正常采集包
```

## `auto` 的日志策略

`wavebench scope auto` 不生成采集数据，当前只输出终端日志。需要持久保存时，应由调用方重定向终端输出或使用外部日志收集方式；`scope auto` 不提供独立的 `--log` 文件参数。

## SCPI 错误检查策略

### `auto`

```text
*CLS
AUToscale
*OPC?
SYST:ERR?
```

### `fetch`

```text
*CLS
FORM REAL
FORM:BORD LSBF
CHAN1:DATA:POIN DMAX
CHAN1:DATA:HEAD?
CHAN1:DATA?
SYST:ERR?
```

### `capture`

```text
*CLS
FORM REAL
FORM:BORD LSBF
CHAN1:DATA:POIN DMAX
SINGle
*OPC?
CHAN1:DATA:HEAD?
CHAN1:DATA?
SYST:ERR?
```

当前不要求每条命令后都查询 `SYST:ERR?`，但整个操作结束后必须检查错误队列。

如果某条命令处于命令确认阶段，可临时打开更严格的逐命令错误检查。

## 重试策略

所有通过 `InstrumentTransport` 发起的 query 都携带显式 `ReplayPolicy`；核心 driver
当前统一使用 `no_replay`。默认规则如下：

| 策略 | 命令行为 | 适用范围 |
| --- | --- | --- |
| `safe_to_replay` | 只有调用点显式声明且 transport 证明可重放时，才按配置重新发送 | 后续专项审计确认幂等的状态查询 |
| `no_replay` | 命令最多发送一次；失败后不重新发送 | 默认策略、`SYST:ERR?`、触发/采集相关查询、波形和截图读取、`*OPC?` |
| `read_continuation_only` | 只在同一响应边界继续读取；backend 不支持时发送前失败 | 当前核心暂无已证明的调用点 |

`read_retry_attempts` 只影响显式 `safe_to_replay` 的完整命令重放，不会改变
`no_replay` 或不支持的 continuation。连接失败、`*OPC?` 超时和波形读取默认不自动重放；
自动化流程应在需要时关闭旧 session、建立新 session，再从完整操作起点执行。

transport 失败使用结构化 `TransportIOError`，至少记录 operation、phase、replay policy、
command transmission、response progress、synchronization 和 attempts；不记录完整命令、响应、
资源串或凭据。健康门禁拒绝的调用使用 `SessionHealthError`，固定表示零仪器 I/O，并包含
当前连接代次。`uncertain`/`poisoned` session 上的普通操作在发送前拒绝，
`on_failure = "continue"` 不会清除该锁存。transport 或资源管理器关闭失败使用
`SessionCloseError`，只记录失败组件和异常类型，并写入运行产物的
`provenance.session_lifecycle.close_errors`。

## 异常类设计

当前核心定义：

```python
class WaveBenchError(Exception): ...
class ConfigError(WaveBenchError): ...
class ConnectionError(WaveBenchError): ...
class InstrumentError(WaveBenchError): ...
class TransportIOError(InstrumentError): ...
class SessionHealthError(InstrumentError): ...
class SessionCloseError(ConnectionError): ...
class OperationTimeout(WaveBenchError): ...
class DataError(WaveBenchError): ...
```

结构化错误只输出稳定字段，不把命令、完整响应、真实资源串或凭据复制到 envelope：

```python
raise InstrumentError("Instrument returned an SCPI error")
```

命令和响应只在经过脱敏的 `commands.log` 或专用诊断上下文中按项目策略记录；错误 envelope
只保留可供程序判断的分类、发送证据和连接代次。

核心服务在执行声明了 `required_verified_fields` 的普通操作前，会针对当前连接代次检查
`verified_fields`；缺少字段时只允许核心验证器执行有界只读验证，通信状态不是 `healthy` 时
在 transport I/O 前返回 `SessionHealthError`。这些字段不会因为 Service 重建或重连而继承。

## 当前结论

当前采用以下记录方式：

```text
终端日志：给人看
commands.log：给复盘用
error.txt：失败采集包里的错误说明
metadata.partial.json：失败时保留已有上下文
```

错误分类：

```text
ConfigError
ConnectionError
InstrumentError
TransportIOError
SessionHealthError
SessionCloseError
OperationTimeout
DataError
```

失败包规则：

```text
连接成功后失败才生成 *_failed 采集包。
```
