# 执行一次实验

本页适用于已经有明确实验目标和 plan 的操作者。目标是在不跳过安全检查的前提下，完成 `run check`、`doctor`、`run verify`、`run plan` 和结果复查。

> [!WARNING]
> `run plan` 可能写入仪器、切换输出或触发采集。不要把 `run check` 的通过结果视为接线或硬件安全证明。

## 必要条件

- plan 的任务、预期输出和 restore 范围已经人工审阅。
- `wavebench.toml` 中的资源、安全限制和 access policy 与当前实验台一致。
- 已确认接线、输入阻抗、地线、输出状态和仪器自身限制。

## 步骤

### 1. 查看当前 schema

```bash
python -m wavebench run schema
```

这条命令离线输出当前支持的 step 和字段。需要修改 plan 时，以它和[run plan Reference](../reference/run-schema.md)为准。

### 2. 进行离线检查

```bash
python -m wavebench run check --config wavebench.toml --plan plans/<plan>.toml
```

命令成功退出并输出摘要后，表示 TOML、字段、离线安全上限、引用和 capability 声明已经通过检查。它不连接仪器。

### 3. 确认资源可读

```bash
python -m wavebench doctor --config wavebench.toml
python -m wavebench run verify --config wavebench.toml --plan plans/<plan>.toml
```

这两条命令会访问真实仪器，但只进行身份查询和预检。预检失败时不要直接执行 `run plan`；先核对资源、型号、access policy、接线和输入状态。

### 4. 执行已确认的 plan

```bash
python -m wavebench run plan --config wavebench.toml --plan plans/<plan>.toml
```

执行前再次确认 plan 是否包含 source、power、scope 或 RF 写入。WaveBench 不会因 `power set` 或设置 source 参数而隐式开启输出，但 plan 中显式的 output step 仍会按请求执行。

### 5. 复查运行产物

```bash
python -m wavebench run report data/runs/<run-dir>
```

先检查 `run.json` 的 `status`，再查看 `summary.csv`、step 记录和报告。`run report` 是离线命令；文件和字段含义见[运行产物 Reference](../reference/artifacts.md)。

## Verification

- `run check` 完成且没有 schema 或安全上限错误。
- `doctor` 和 `run verify` 返回预期资源的只读记录。
- `run plan` 返回 run 目录，`run.json.status` 与实验结果一致。
- 输出、采集和恢复状态符合 plan 的显式请求；不确定时停止后续写入并重新读取状态。

## 常见失败

- `kind`、字段名或必填字段错误：阅读[run plan 排错](troubleshooting.md)，再运行 `run schema`。
- 资源不可达或型号不匹配：检查 `wavebench.toml`，再运行 `doctor`。
- 预检或安全限制失败：不要降低限制来绕过错误；先确认真实接线和安全边界。
- step 执行失败：保留 run 目录，检查 `run.json` 和对应 `steps/` 记录，再查[错误 Reference](../reference/errors.md)。
