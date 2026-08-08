# Run plan 工作流

> 加载时机：创建、审查、验证、执行或报告 WaveBench run plan 时加载。
> 本文件不依赖其他 reference。

## 标准顺序

对计划使用以下顺序：

```bash
.venv/bin/wavebench run check --plan plans/<plan>.toml --config wavebench.toml
.venv/bin/wavebench run verify --plan plans/<plan>.toml --config wavebench.toml
.venv/bin/wavebench run plan --plan plans/<plan>.toml --config wavebench.toml
.venv/bin/wavebench run report data/runs/<run-dir>
.venv/bin/wavebench capture inspect data/raw/<capture-dir> --fft
```

先 `check`，再 `verify`，最后才考虑实时执行。计划输出或帮助文本发生变化时，先读取当前 CLI 帮助和 schema，不凭记忆补参数。

## Check 与 verify

- `run check` 检查计划结构、字段、引用和静态约束，不执行仪器 I/O。
- `run verify` 执行计划所需的只读前置确认；把身份、能力、资源和安全策略问题暴露在实时写入前。
- 两者任一失败时，不执行 `run plan`。
- 退出码为成功不等于仪器状态、质量门和恢复状态都满足要求。

## 计划审查

执行前逐项审查：

- 计划目标、输入、输出和预期产物；
- 每个源、电源、触发和采集步骤；
- 输出启用的明确位置和关闭位置；
- 电压、电流、Vpp、频率、超时、重试和采样限制；
- 输入阻抗、耦合、通道和接线假设；
- 期望指标、容差、质量门和失败策略；
- 恢复条款及不恢复字段；
- 运行目录和敏感信息处理方式。

计划只读确认与实时执行之间应有明确授权边界。发现缺失的输出关闭步骤、超出限制的参数或含糊的恢复语义时，停止并修订计划。

## 意图与证据

需要固定计划、配置和输入时，先生成执行意图，再在实时执行前核验摘要：

```bash
.venv/bin/wavebench run intent --config wavebench.toml \
  --plan plans/<plan>.toml --output data/intents/<intent>.json
.venv/bin/wavebench run plan --config wavebench.toml \
  --plan plans/<plan>.toml --intent data/intents/<intent>.json
```

意图摘要应覆盖计划、配置、资源和输入载荷的校验信息。`run plan --intent` 必须在取得资源租约或打开仪器前重新核验摘要。

执行前记录本次操作意图：

- 请求目标和验收标准；
- 用户授权范围；
- 受影响的仪器与通道；
- 允许的最大输出和持续时间；
- 预期恢复状态；
- 计划文件版本或校验信息。

执行中保留：

- `check`、`verify` 和执行命令；
- 计划解析结果；
- 步骤状态和质量门结果；
- 仪器响应、异常和时间戳；
- 波形、截图、报告和状态快照。

不得用一份成功的短采集证明长波形、多通道或长期稳定性。

## 失败处理

遇到单步失败、通信超时、结果含糊或恢复异常时：

1. 停止后续不可逆步骤。
2. 不重复发送可能已经生效的写入。
3. 保持受影响输出关闭。
4. 保留已经生成的部分产物。
5. 重新读取实际状态。
6. 将失败步骤、恢复结果和剩余风险写入报告。

## 成功判定

只有以下条件同时满足才报告成功：

- 所有必需步骤状态为成功；
- 质量门和期望指标通过；
- 产物完整且可读取；
- 最终仪器状态符合计划；
- 恢复范围与计划承诺一致；
- 报告包含命令、配置、结果和未恢复字段。

## 产物报告

使用：

```bash
.venv/bin/wavebench run report data/runs/<run-dir>
```

报告至少包含：

- 计划和配置的脱敏标识；
- 实际执行步骤；
- 测量值、阈值和单位；
- 质量门结果；
- 产物路径；
- 最终源、示波器、电源和 DMM 状态；
- 跳过的测试、部分产物、失败重试和恢复异常。

不要把 `wavebench.toml` 中的真实资源、序列号或私有路径复制到公共文档。
