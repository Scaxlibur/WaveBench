# RFC-0003：示波器截图 framing 与菜单合同

> 状态：`Superseded R1`
> 原提案：新增 `query_raw_bytes_once()` 和可空菜单布尔值
> 核心裁决：使用 `query_binary()`、`ScopeScreenshotProfile` 和 `scope.screenshot_v2`
> 相关规范：[scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)
> 系列总览：[scope 可移植性 RFC 组合说明](WaveBench_scope可移植性RFC组合说明.md)

## 摘要

截图需要同时证明 transport framing、媒体完整性、菜单/颜色请求和临时状态恢复。核心 R1.3
已经提供这组合同，因此不再增加「读取到 timeout 为止」或「返回任意 bytes」的平行 transport
入口。

本 RFC 记录替代关系。核心 API 已实现不表示任一现有内建 driver 或外部插件已经声明
`scope.screenshot_v2`。

## 核心裁决

### Framing

`query_binary()` 首版只接受：

- `DEFINITE_BLOCK`：响应由 IEEE 488.2 `#N` 头声明 payload 长度；
- `MESSAGE`：具体 backend/resource 能证明 EOI、VISA END 或等价 message boundary。

换行、短 `recv()`、idle、timeout、PNG IEND 和暂时无数据都不是通用 transport message
边界。无法证明边界时，binary command 必须在发送前拒绝，或在发送后按结构化同步失败处理。

`query_raw_bytes_once()` 不采用，原因包括：

- 缺少统一完成条件；
- 容易绕过 response/operation/query/resynchronization 预算；
- 会重复 replay 和 session health 合同；
- 容易把媒体结束标记误当成 transport 结束；
- backend 之间无法提供同形的成功证明。

如果未来出现既不是 definite block、也无法由 backend 报告 message END 的必要协议，应另立
包含长度、EOM、预算和失步处理的新 RFC，不能把 raw bytes 方法作为无边界后门。

### 请求 tuple

核心不再使用单个 `include_menu: bool` 表达所有设备。请求由明确 tuple 构成：

~~~text
format = png
menu_mode = device | include | exclude
color_mode = device | color | monochrome | inverted
~~~

`device` 表示保留设备当前行为，不等于 `include` 或 `exclude`。descriptor profile 必须枚举
实际支持的 request tuple；请求没有精确匹配时，在仪器 I/O 前拒绝。

菜单不可控制或无法证明时，只能声明 `device`，不能把 unknown 静默改写成
`exclude`。结果中的 effective request 必须与已验证 variant 一致。

## 媒体与 transport 分层

transport 只验证 framing、长度、精确 trailing、预算和同步。driver/核心媒体处理器负责：

- PNG signature；
- IHDR 尺寸；
- chunk 长度与校验；
- IEND；
- profile 声明的 content trailing；
- media type。

位于 definite block payload 或 MESSAGE 数据中的 PNG 后缀属于 content trailing，不是
transport trailing。两者都只能精确匹配，不能用 `rstrip()` 删除任意空白。

## 状态恢复

需要临时改变菜单或颜色时，顺序固定为：

~~~text
preflight snapshot
  -> main capture
  -> error-after
  -> restore
  -> fresh verify
~~~

核心持有 baseline、context、session epoch 和一次性 nonce。driver 不得自行替换 baseline，
也不得在 `finally` 中绕过阶段授权恢复。恢复或 fresh verify 失败时不能返回截图成功值。
session 已 `poisoned` 时禁止追加 restore、verify、IDN 或探测 I/O。

## 旧截图兼容边界

- 旧 `scope.screenshot` 与 `screenshot_png()` 保持不变；
- 同时声明旧 capability 和 `scope.screenshot_v2` 时，旧 capture 仍走 legacy screenshot；
- 只有 V2、没有旧 capability 时，旧 capture 的嵌入截图请求在 I/O 前拒绝；
- 独立 V2 命令不改变旧 `CaptureResult` 和部分 waveform 产物语义；
- 内建 RTM2032/DS1104 没有因为核心合同存在而自动获得 V2 capability。

## 插件采用条件

具体插件声明 screenshot V2 前必须逐 request variant 证明：

1. 实际 framing 与 resource/backend；
2. response、operation、query 和 resynchronization 上限；
3. transport/content trailing；
4. PNG 完整性和尺寸；
5. menu/color 的 requested/effective 语义；
6. changed fields、restore order 和 step 上限；
7. 成功、媒体失败、transport 失败和恢复失败；
8. fresh readback 与最终 session health。

若设备只能证明 definite block 截图，但不能控制菜单，可以只声明
`menu_mode="device"` 的受限 variant。无法证明 `exclude` 时不得复用旧
`include_menu=False` 的表面语义。

## 验收与结案

核心侧结案条件：

- `BinaryResponseFraming`、`query_binary()` 和四维预算已实现；
- `ScopeScreenshotRequest/Profile/Baseline/ScopeScreenshot` 已公开；
- capability、required Protocol、Service、CLI 和 artifact 已注册；
- legacy capture 分流和零 I/O 拒绝已有回归；
- 文档明确原 `query_raw_bytes_once()` 不再采用。

具体型号的 framing/menu 证据和实机验收仍属于插件范围。
