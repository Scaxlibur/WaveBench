# 频率响应与校准

本页适用于已经明确 DUT 输入、输出和量程的操作者。目标是先用保守的双通道模板确认测量链路，再按需要增加基线、校准、比较或补测；所有字段以当前 `run schema` 为准。

> [!WARNING]
> 频率响应会驱动信号源并采集两路波形。开始前确认 reference 通道接 DUT 输入、response 通道接 DUT 输出，并确认两路的输入阻抗、量程、接地和安全幅度。

## 1. 生成并离线检查模板

```bash
python -m wavebench run template source-scope-frequency-response \
  --frequencies 100,1000,10000 \
  --source-channel 1 \
  --reference-channel 1 \
  --response-channel 2 \
  --output plans/frequency-response.toml

python -m wavebench run check --config wavebench.toml --plan plans/frequency-response.toml
```

模板生成只写本地 TOML；`run check` 不连接仪器。检查通过后，仍需人工确认频点、幅度、通道角色和执行时间。

## 2. 只读预检后执行

```bash
python -m wavebench run verify --config wavebench.toml --plan plans/frequency-response.toml
python -m wavebench run plan --config wavebench.toml --plan plans/frequency-response.toml
```

`run verify` 查询设备和安全前提。`run plan` 才会改变信号源状态并采集数据；若预检不通过，先停止并修正配置或接线。

## 3. 查看产物和离线后处理

```bash
python -m wavebench run report data/runs/<run-dir>
python -m wavebench run compare data/runs/<baseline> data/runs/<current>
```

`run report` 和 `run compare` 只读取已有产物。频响 step 的 CSV、拟合、基线、校准、补测和证据字段属于运行产物；入口与稳定字段见[运行产物 Reference](../reference/artifacts.md)。

## 校准与补测

- 直通基线应是独立 run；不要把 DUT 结果直接当作基线。
- 使用 `run calibrate`、`run compare` 或 `run resume` 前，先通过 `python -m wavebench run --help` 和 `run schema` 核对安装版本的参数。
- 校准或补测不会替代原始采集证据；保留原 run 目录和对应的 plan。

## 相关页面

- [执行一次实验](run-an-experiment.md)
- [run plan Reference](../reference/run-schema.md)
- [run plan 排错](troubleshooting.md)
- [重构前的专题记录](../archive/run-plan-guide-pre-migration.md)：仅用于追溯历史细节，不作为当前 schema 或 capability 的来源。
