# WaveBench scope 可移植性 RFC-0009：SINGLE 模式读回后的终态 STOP 完成证明

> 状态：`Implemented R1（未发布）`
> 核心基线：WaveBench `0.8.24` 开发线
> 依赖：[scope 通用扩展接口 RFC](WaveBench_scope通用扩展接口RFC.md)
> 范围：完成式 `scope.acquisition_control` 的附加证明分支

## 目的

`scope.acquisition_single` 的成功结果表示本次 SINGLE 已完成。普通的 `STOP`、固定等待、
`*OPC?`、现有波形或 preamble count 都不能单独证明这一点。

某些设备的单条 SINGLE 命令会同时选择 SINGLE 模式并 arm。在 LAN 轮询中，触发可能在首条
状态查询前已经完成，因而首个状态就是 `STOP`。RFC-0009 为这种设备定义一个比普通
`state_transition` 更窄的可选证明；默认行为仍然拒绝首条 `STOP`。

该合同只证明完成式 SINGLE 控制操作，不证明波形记录的新鲜性，也不证明平均累积完成。

## 核心合同

`ScopeCompletionProof` 追加：

```python
"single_mode_readback_then_stopped"
```

`ScopeAcquisitionControlProfile` 在末尾追加：

```python
single_mode_readback_allows_terminal_stop: bool = False
```

`ScopeAcquisitionCompletion` 在现有可选字段末尾追加：

```python
post_arm_trigger_mode: ScopeTriggerMode | None = None
```

profile 字段必须是实际的 `bool`。默认值为 `False`，因此现有 descriptor、driver 和完成
证明保持原样。`post_arm_trigger_mode` 只能记录 SINGLE 写入后实际查询到的模式，不能从
run-state 推测。

核心只在以下条件同时成立时接受新证明：

1. `profile.single_mode_readback_allows_terminal_stop is True`；
2. `completion.post_arm_trigger_mode == "single"`；
3. `completion.state.phase == "stopped"`，且 `completion.state.trigger_mode == "single"`；
4. `completion.observed_states == (completion.state,)`；
5. `baseline_count`、`completed_count`、`baseline_identity` 和 `completed_identity` 都是 `None`；
6. 原有的 `original_state`、proof baseline stage、atomic-arm baseline 和终态校验全部通过。

该分支不要求 `state_transition`，也不解释或放宽另外三种 proof。缺任一条件时，核心拒绝
completion，并沿用原有失败处理。

## Driver 顺序与失败处理

dataclass 不能证明实际 I/O 顺序。声明该 profile 开关的插件必须用 conformance test 固定以下
顺序：

```text
不可重放的 SINGLE 写入
        ↓
SINGLE 模式读回 == single
        ↓
第一条 acquisition-state 读取 == stopped
```

模式读回和第一条状态读取必须发生在 SINGLE 写入之后，且在任何轮询或等待之前。首条状态为
`waiting` 时继续沿用 `state_transition`；模式不匹配、状态为其他 token、超时、after-error、
transport 错误或模型错误都不能构造本证明。

现有的 exclusive lease、deadline、failure cleanup、fresh verification 和 poisoned-session 零追加
I/O 语义不变。成功的 SINGLE 保持设备的 SINGLE/STOP 后置状态；失败路径仍只恢复 operation
开始前的 trigger/acquisition 配置。

## 兼容性与边界

- 不新增 capability、Service 入口、CLI、run-plan step 或独立成功返回类型；仅向既有
  `ScopeAcquisitionCompletion` 追加可选的 `post_arm_trigger_mode` 字段。
  仍使用 `scope.acquisition_control` 和 `ScopeService.acquire_single()`。
- 旧 profile 因默认 `False` 继续拒绝首条 `STOP`；旧 completion 不需要填写
  `post_arm_trigger_mode`。
- 该证明不开放 `scope.capture_waveform`、`scope.capture_waveforms`、运行态 MAX/DMAX、
  record/replay 或 `scope.capture_average_v2`。
- average capture R1 仍只接受 `device_average_complete`。未来的
  `documented_single_completion` 需要独立的 R2 接受门；RFC-0009 不能替代平均完成位。

## 插件采用门

具体插件只能在以下条件全部满足后，把
`single_mode_readback_allows_terminal_stop` 设为 `True` 并声明 `scope.acquisition_control`：

1. 厂商资料明确说明该 SINGLE 命令的 mode/arm/stop 语义，并限定适用型号和固件；
2. driver conformance fixture 覆盖精确 query 顺序、`waiting → stopped` 旧路径以及模式/状态
   不匹配、超时和 transport 失败；
3. 低压实机验收覆盖成功 trace、failure restore、fresh verification 与最终安全状态；
4. wheel 与 descriptor 的最低核心版本指向首个实际发布且包含本合同的核心版本。

核心 R1 的离线实现不构成任何型号的实机结论。本仓库本次不修改外部插件 descriptor 或 driver，
也不进行仪器 I/O。

## 离线验收

核心测试覆盖 profile 默认拒绝、精确合法 completion、错误的模式读回、多个 observed states、
count/identity 混入、非 bool profile 值，以及 Service 接受合法新 proof 的路径。既有
identity/count/state-transition 证明和 legacy descriptor 继续由完整回归覆盖。
