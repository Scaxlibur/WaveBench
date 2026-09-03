# WaveBench sweep 状态保存与恢复设计（迁移前归档）

> 历史提案。当前通用恢复模型见[会话与恢复](../concepts/sessions-and-recovery.md)；本页不构成当前恢复覆盖范围的承诺。

## 背景

`wavebench sweep discrete` 已经能完成最小完整流程：

```text
设置信号源频率 -> 示波器单次采集 -> 写采集包 -> 汇总 summary.csv
```

现在 source 侧也已经有了正式命令：

```text
source set-freq
source set-func
source set-vpp
source output
source status
```

可以进一步在 sweep 前保存信号源状态，并在 sweep 后恢复。但恢复范围不能写成「恢复全部状态」；仪器状态比普通配置更敏感，错误恢复会影响实验台。

所以本设计先规定边界：**只恢复 WaveBench 已经能明确表达、已经有正式命令支持、并且实机验证过的字段。**

## 设计原则

### 1. 默认少动仪器

`sweep discrete` 默认只设置每个频点需要的 frequency。

只有用户显式传参时才设置：

```text
--source-func
--source-vpp
```

即使加入 restore，也不能变成「默认修改一组未明确声明的状态」。

### 2. 只恢复自己会写的字段

当前可以安全恢复的字段：

```text
output
function
frequency
amplitude_vpp
```

原因：这些字段都有正式命令路径：

```text
source output
source set-func
source set-freq
source set-vpp
```

暂不恢复：

```text
sweep mode
sweep start/stop frequency
sweep time
sweep spacing
offset
phase
load/impedance
modulation/burst
```

这些字段有的还没有正式命令，有的和 DG4202 内部模式强相关。硬恢复会把问题藏起来。

### 3. restore 是 best-effort，但必须记录结果

sweep 主流程失败时，也应该尝试 restore。

但 restore 自己也可能失败。不能吞掉，至少要在 stderr / summary metadata 中写清楚：

```text
sweep_error: ...
restore_error: ...
restore_attempted: true
restore_succeeded: false
```

第一版可以先只打印，后面再写入 summary JSON。

### 4. 不用 restore 掩盖失败

如果 sweep 中某个频点失败，命令应该失败。restore 成功不代表 sweep 成功。

推荐行为：

```text
try:
    run sweep
finally:
    attempt restore if requested
```

退出码仍以 sweep 主流程为准；如果主流程成功但 restore 失败，则也应返回非零。

## SourceState 数据结构

建议增加一个小而明确的数据结构，而不是直接传 `SourceStatus`。

```python
@dataclass(frozen=True)
class RestorableSourceState:
    channel: int
    output: str
    function: str
    frequency_hz: float | None
    amplitude: float | None
    amplitude_unit: str | None
```

这里故意不包括：

```text
frequency_mode
sweep_enabled
apply_raw
offset_v
phase_deg
```

不是这些字段不重要，而是当前还不能安全写回。

## 保存流程

当调用方显式请求保存或恢复时，例如使用以下参数：

```text
--restore-source-state
```

sweep 开始前执行：

```python
original = source.status(channel)
restorable = RestorableSourceState.from_status(original)
```

如果状态里缺少必要字段，例如 `frequency_hz is None` 或 `amplitude is None`，第一版应拒绝 restore：

```text
cannot restore source state: missing frequency/amplitude in source status
```

不要猜。

## 恢复顺序

推荐恢复顺序：

```text
1. set-func
2. set-vpp
3. set-freq
4. output on/off
```

原因：

- function 可能影响 amplitude/frequency 的合法范围；
- amplitude unit 先固定为 VPP；
- frequency 最后设置到目标值；
- output 最后恢复，避免恢复过程中输出短暂异常波形。

如果原始 output 是 OFF，更应该最后关回 OFF。

## CLI 设计建议

不要默认开启 restore。建议增加显式参数：

```text
--restore-source-state
```

示例：

```bash
python -m wavebench sweep discrete --config wavebench.toml ^
  --source-channel 2 ^
  --scope-channel 1 ^
  --source-func sin ^
  --source-vpp 5 ^
  --frequencies 1000,2000,5000,10000 ^
  --restore-source-state
```

默认不 restore 的原因：

- 有些实验就是希望 sweep 结束后停在最后一个频点；
- 自动恢复会多写几条仪器命令，增加失败面；
- 当前还没有覆盖完整 DG4202 sweep 模式状态。

## 第一版实现范围

第一版只做：

```text
[ ] RestorableSourceState 数据结构
[ ] SourceService.snapshot_restorable_state(channel)
[ ] SourceService.restore_restorable_state(state)
[ ] sweep discrete --restore-source-state
[ ] 单元测试：restore 顺序
[ ] 单元测试：缺字段时拒绝 restore
[ ] 实机 smoke：从 SIN/5k/5Vpp/ON -> sweep -> 恢复到原状态
```

第一版不做：

```text
[ ] 恢复 DG4202 sweep mode
[ ] 恢复 offset/phase
[ ] 恢复任意 waveform apply_raw
[ ] 自动打开 output
[ ] 失败包之外的复杂报告
```

## 实机验证计划

最小验证：

1. 读取原状态：

```bash
python -m wavebench source status --config wavebench.toml --channel 2
```

2. 设置一个可观察的原状态，例如：

```text
SIN / 5000 Hz / 5 Vpp / output=ON
```

3. 执行 sweep：

```bash
python -m wavebench sweep discrete --config wavebench.toml ^
  --source-channel 2 ^
  --scope-channel 1 ^
  --source-func squ ^
  --source-vpp 3.3 ^
  --frequencies 1000,2000 ^
  --target-cycles 10 ^
  --frequency-tolerance 0.05 ^
  --restore-source-state ^
  --no-csv
```

4. 再读状态，应恢复为：

```text
SIN / 5000 Hz / 5 Vpp / output=ON
```

5. 检查 summary：

```text
1000 Hz ok=True
2000 Hz ok=True
restore_succeeded=True
```

## 风险和暂缓项

### sweep mode 恢复

DG4202 的 `FREQ:MODE=SWE` 不是一个孤立字段。它和 sweep 开关、起止频率、时间、间隔方式有关。

当前已经验证过：如果直接在 sweep 模式下写固定频率，示波器会读到错误结果。因此第一版 restore 不恢复 sweep mode。

后面如果真的需要恢复 sweep 模式，应该单独做：

```text
DG4202 sweep profile snapshot/restore
```

不要混进普通离散 sweep。

### output 恢复

如果原始 output 是 OFF，restore 最后应关回 OFF。

但 sweep 本身不应该自动打开 output。用户必须显式执行：

```bash
wavebench source output --channel 2 on
```

这样比「工具在未声明时打开输出」更安全。

## 结论

`sweep discrete` 的状态恢复应该是一个显式功能，不是默认行为。

第一版只恢复 WaveBench 已经能可靠写回的最小集合：

```text
output / function / frequency / amplitude_vpp
```

这不完美，但可解释、可测试、可回滚。等这些稳定后，再考虑 DG4202 sweep profile 这类更复杂的状态。
