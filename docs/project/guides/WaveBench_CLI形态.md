# WaveBench CLI 使用边界

本页说明 WaveBench CLI 的命令分类、硬件副作用和常用调用顺序。具体参数以当前代码输出的 `--help`、`run schema` 和 `run template --list` 为准。

## 命令入口

根命令为：

```bash
wavebench
```

当前一级命令按设备或功能划分：

```text
scope    source    power    dmm    sweep    run
capture  mcp       tui      net    doctor   plugin
capability
```

命令帮助：

```bash
wavebench --help
wavebench scope --help
wavebench run --help
```

## 按硬件副作用分类

| 类别 | 示例 | 行为 |
|---|---|---|
| 离线 | `run schema`、`run template`、`run check`、`run intent`、`run report`、`run compare`、`run resume`、`capability explain`、`capture inspect`、`tui --fake` | 不连接仪器；报告、比较、检查、能力解释和意图生成只读取本地文件 |
| 连接读取 | `doctor`、`net`、`scope idn`、`scope status`、`run verify` | 查询资源、身份或状态，不应修改实验设置 |
| 显式写入或触发 | `scope auto`、`scope fetch/capture`、source / power setter、`run plan` | 可能改变设置、触发采集或切换输出 |

执行硬件写入前，应先确认接线、输入阻抗、输出状态和安全限制。CLI 不会自动发送 `*RST`，也不会因为设置电压或幅度而自动打开输出。

## 推荐调用顺序

没有仪器时，先完成离线检查：

```bash
wavebench run template --list
wavebench run template source-scope-sine --output /tmp/wavebench-demo.toml --force
wavebench run check --plan /tmp/wavebench-demo.toml
```

连接真实仪器时，按以下顺序执行：

```bash
wavebench doctor --config wavebench.toml
wavebench run verify --config wavebench.toml --plan plans/example_scope_expect_quality.toml
wavebench run plan --config wavebench.toml --plan plans/example_scope_expect_quality.toml
wavebench run report data/runs/<run-dir>
```

`run check` 只解析 TOML 和字段，不连接仪器；`run verify` 做执行前的只读预检；`run plan` 才会执行真实实验。

`scope status` 在驱动没有完整 `scope.snapshot` 时返回 `status=partial`，并列出缺少的能力；需要
完整快照时追加 `--strict`。能力解释不会连接仪器：

```bash
wavebench capability explain scope.status --driver rtm2032
wavebench capability explain source.output --config wavebench.toml --json
```

`--json` 可以放在命令行任意位置。非交互命令输出 `wavebench.cli.result.v1`；错误输出
`wavebench.error.v1`，诊断信息写入 stderr。TUI 和 HTTP MCP 不使用 one-shot JSON 包装。

频响结果的离线处理使用以下命令：

```bash
wavebench run compare data/runs/<reference-run> data/runs/<candidate-run> --format json
wavebench run resume data/runs/<candidate-run> --plan plans/<plan>.toml
```

`run compare` 按 `case_id` 比较增益、相位和测量状态；`run resume` 生成可复用点与待补测点清单。两条命令都不会打开仪器 session。

## 示波器命令

### 查询和状态

```bash
wavebench scope idn --resource TCPIP::192.0.2.10::INSTR
wavebench scope errors --resource TCPIP::192.0.2.10::INSTR
wavebench scope status --config wavebench.toml
```

`scope idn` 查询 `*IDN?`；`scope errors` 读取 `SYST:ERR?`，直到仪器返回无错误；`scope status` 读取只读状态，缺少完整快照能力时返回 partial summary。

### `auto`、`fetch` 和 `capture`

| 命令 | 作用 | 主要副作用 |
|---|---|---|
| `scope auto` | 执行显式 `AUToscale` 并等待 `*OPC?` | 改变示波器的水平、垂直或触发设置 |
| `scope fetch` | 读取当前已有波形 | 不主动触发新的采集；具体传输设置以配置为准 |
| `scope capture` | 触发一次采集、等待完成并保存采集包 | 触发采集，生成 CSV、NPY、JSON metadata 和命令记录 |

常见调用：

```bash
wavebench scope auto --config wavebench.toml --channel 1
wavebench scope fetch --config wavebench.toml --channel 1
wavebench scope capture --config wavebench.toml --channel 1
```

`fetch` 和 `capture` 不会隐式执行 `auto`。需要调整量程时，先单独执行 `scope auto`，再执行采集命令。

## 配置和资源优先级

WaveBench 使用 TOML 配置。资源和选项的优先级为：

```text
命令行参数 > wavebench.toml > 默认值
```

配置示例：

```toml
[connection]
backend = "lan"
resource = "TCPIP::192.0.2.10::INSTR"
timeout_ms = 10000
opc_timeout_ms = 30000

[scope]
model_hint = "RTM2032"
reset_before_run = false
check_errors = true
```

命令行可临时覆盖资源：

```bash
wavebench scope capture --config wavebench.toml \
  --resource TCPIP::192.0.2.10::INSTR --channel 2
```

公开示例使用 RFC 5737 保留地址。真实 IP、序列号、串口路径和实验产物不应写入仓库。

## 输出和错误

- 正常结果写入标准输出；错误信息写入标准错误，并返回非零退出码。
- 采集失败时，已生成的部分 artifact 仍会保留，并通过 `metadata.partial.json`、`error.txt` 或 `commands.log` 记录上下文。
- `run plan` 失败时保留 `run.json`、`summary.csv` 和 step record，便于定位失败步骤。
- 需要脚本消费结果时，优先读取 JSON；`summary.csv` 适合快速查看和表格导入。

## TUI 和 HTTP MCP

TUI 是可选的终端控制面板，不替代 CLI 的 run plan 编排：

```bash
wavebench tui --fake
wavebench tui --config wavebench.toml
```

`--fake` 不连接真实仪器；非 fake 模式可能读取或修改电源、万用表和信号源状态。

HTTP MCP 默认只监听 loopback，并要求受保护端点使用 Bearer token。当前工具只提供 schema、检查和离线采集包查看，不执行 run，也不提供 raw SCPI。

## 当前边界

- 仪器命令按设备和 Service 分层，CLI 不直接拼接业务流程中的 SCPI。
- 需要改变硬件状态的动作必须由显式命令或 run plan step 表达。
- source restore 只覆盖文档声明的 basic 字段，不等于完整通道快照。
- 外部 Python 插件按当前用户权限运行，不是安全沙箱；只安装已确认来源的本地目录或 wheel。
