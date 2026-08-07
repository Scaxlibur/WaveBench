# WaveBench 新增仪器驱动指南

本页面向需要将新仪器接入 WaveBench 执行路径的开发者。

先确定扩展类型：

| 目标 | 应该做什么 |
|---|---|
| 只想记录一个仪器的型号、能力、IDN 查询方式 | 编写 V1 metadata 或声明式 SCPI TOML，参阅[声明式 SCPI 插件](../reference/plugins/WaveBench_声明式SCPI插件.md)。 |
| 要以独立 wheel 提供真实 driver | 实现 Instrument API V2，参阅[插件开发指南](WaveBench_插件开发指南.md)和 [API 约定](../reference/plugins/WaveBench_可执行仪器插件API.md)。 |
| 要新增 CLI 命令连接仪器并读写状态 | 写真实 driver + service + CLI。 |
| 要让仪器参与 `doctor` / `run verify` | 写配置解析、只读 IDN / safety 检查。 |
| 要让仪器参与 `run plan` | 写 step schema、执行逻辑、artifact / summary、测试和文档。 |
| 要生成保守 plan 模板 | 在 `run_templates` 中加模板，并保证模板不连接仪器。 |

这两条路径需要分开处理：插件 metadata 可以描述仪器，但不会自动生成真实执行能力。

## 最小接入路径

一个真实仪器接入通常按这个顺序推进：

1. 确认手册和最小只读命令，例如 `*IDN?`、状态查询、错误查询。
2. 写 driver，只封装仪器命令和返回值解析。
3. 编写 Service，表达 WaveBench 的业务动作，例如「读取一次 DMM」「设置电源电压和限流」。
4. 写配置字段，避免把 resource、driver、backend 散落到 CLI 里。
5. 写 CLI 参数和处理逻辑。
6. 接入 `doctor`，先只读检查可达性和 IDN。
7. 如需参与实验流程，再接入 `run plan` / `run verify` / `run template`。
8. 补测试、公开示例和文档索引。

## 代码入口

| 层 | 常见入口 |
|---|---|
| Driver | `src/wavebench/drivers/*.py` |
| Service | `src/wavebench/services/*_service.py` |
| Config | `src/wavebench/config.py` |
| CLI parser | `src/wavebench/cli_parser.py` |
| CLI handler | `src/wavebench/cli.py` |
| Discovery / doctor | `src/wavebench/discovery.py`, `src/wavebench/doctor.py` |
| Run plan schema | `src/wavebench/services/run_plan.py` |
| Run execution | `src/wavebench/services/run_service.py` |
| Run artifacts / analysis | `src/wavebench/services/run_artifacts.py`, `src/wavebench/services/run_analysis.py` |
| Run templates | `src/wavebench/services/run_templates.py` |
| Plugin metadata | `src/wavebench/plugins/builtin.py` |
| Report | `src/wavebench/report/html.py`, `src/wavebench/report/index.py` |
| Tests | `tests/test_<instrument>.py`, `tests/test_<service>.py`, `tests/test_cli.py`, `tests/test_doctor.py`, `tests/test_run_plan.py` |

## Driver 层

Driver 层只负责「仪器能直接执行的命令」：

- 打开后通过 transport 发 SCPI 或串口命令。
- 提供窄接口，例如 `idn()`、`status()`、`read_voltage()`、`set_voltage_current_limit()`。
- 解析仪器返回值，抛出 WaveBench 可解释的错误。
- 不直接读 CLI 参数。
- 不写 run artifact。
- 不偷偷 reset、autoscale 或打开输出。

推荐做法：

- 先实现 `idn()` 和一个只读状态查询。
- 写 driver 单测，用 fake transport 验证命令字符串和解析结果。
- 对危险写命令保持一命令一方法，便于 service 层做显式组合。

## Service 层

Service 层表达 WaveBench 的动作边界。

例如：

- DMM Service 表达「读取一次测量」。
- Power Service 表达「设置电压 / 限流」或「显式切换输出」。
- Source Service 表达「设置频率 / 波形 / 幅度」或「显式切换输出」。
- Scope Service 表达「采集波形」「截图」和「显式 autoscale」。

规则：

- 不把一个仪器的动作偷偷塞进另一个仪器 service。
- 不隐式打开或关闭输出。
- 不隐式 reset。
- 危险动作必须有清楚的 CLI 命令或 run-plan step。
- 自动恢复、safety guard、artifact 写入应在 run/service 边界清楚表达。

## 配置字段

新增仪器一般需要在 `config.py` 里补字段。

要考虑：

- `driver`：型号/驱动选择。
- `backend`：LAN/VISA、serial 或其他 transport。
- `resource`：例如 `TCPIP::192.0.2.10::INSTR` 或 `/dev/ttyUSB0`。
- `timeout_ms`：连接和查询超时。
- safety limits：如最大电压、最大电流、最大 source Vpp。

公开文档不要写真实实验室 IP。示例使用保留网段：

```toml
resource = "TCPIP::192.0.2.10::INSTR"
```

## CLI 接入

CLI 接入分两部分：

1. `cli_parser.py` 定义命令、参数、默认值和帮助文本。
2. `cli.py` 调用 config、service 和输出格式。

建议：

- 先做只读命令，例如 `idn` / `status` / `read`。
- 写命令要明确动词，例如 `power output on`、`source set-freq`。
- 不用一个命令隐式做多个危险动作。
- 输出保持窄而可 grep，必要时提供结构化字段。

## Doctor / Verify

`doctor` 面向配置资源可达性和 IDN 匹配。

新增仪器时应补：

- 读取配置中的 resource / driver / expected model。
- 只读连接。
- 发送 `*IDN?` 或等价只读识别命令。
- 判断 IDN 是否匹配 driver / model。
- 给出可操作建议，不修改配置。

`run verify` 是执行前只读预检。它可以检查：

- 计划涉及的资源是否可达。
- `*IDN?` 是否匹配。
- safety guard 是否满足，例如示波器输入不是 50 ohm。

`run check` 不连接仪器，只解析 plan 并打印摘要。

## Run Plan 接入

如果新仪器要参与 `run plan`，至少要补这些点：

1. `ALLOWED_STEP_KINDS`。
2. 必填 / 可选字段。
3. `format_run_plan_schema()` 输出。
4. TOML 解析和未知字段校验。
5. `run_service` 中的执行分支。
6. artifact / `run.json` / `summary.csv` 输出。
7. 失败时 run 状态和错误信息。
8. run-plan session 生命周期。
9. tests。

常见 step 命名风格：

```text
scope.capture
source.set_freq
source.output
power.set
power.output
dmm.read
```

新 step 应该一件事只做一件事。例如 `power.set` 只设置电压和限流，不顺手打开输出。

## Run Plan Session 生命周期

`run plan` 会为本次实验需要的仪器统一打开 session，并在 safety guard、snapshot/restore 和所有 step 之间复用。成功或失败后统一关闭。

边界：

- 普通 CLI one-shot 命令仍然一次操作打开一次、结束关闭。
- 当前不做长 session 断线自动重建。
- 如果 run 中途断线，应失败并留下 `run.json` 证据。
- 不应为了「继续运行」而静默重连并隐藏问题。

## Run Template 接入

模板只生成 TOML，不连接仪器。

新增模板时：

- 加到 `src/wavebench/services/run_templates.py`。
- 更新 `RUN_TEMPLATES`。
- 让模板输出标准 run plan TOML。
- 默认不覆盖文件，除非用户显式 `--force`。
- 模板必须保守，危险动作要显式写出来。
- 补 `tests/test_run_templates.py`。
- 补 README / run plan 指南示例。

## 安全边界

新增仪器时优先写下这些决定：

- 是否会写仪器状态。
- 是否会打开/关闭输出。
- 是否需要 safety guard。
- 是否需要 restore。
- 写命令失败时如何报告。
- restore 失败时是否让 run 失败。
- 哪些状态暂不恢复，为什么。

硬规则：

- 不默认 reset。
- 不隐式 output on/off。
- 不把危险状态藏在普通读命令里。
- 不自动扫描串口并盲发命令。
- 不把真实实验室 IP 写进公开文档。

## 测试清单

最小测试建议：

```bash
python -m pytest tests/test_<instrument>.py
python -m pytest tests/test_<service>.py
python -m pytest tests/test_cli.py
python -m pytest tests/test_doctor.py
python -m pytest tests/test_run_plan.py
```

按接入范围补充：

- driver fake transport 单测。
- service 单测。
- CLI 参数和输出测试。
- config override 测试。
- doctor / verify 测试。
- run plan schema 和执行测试。
- run template 测试。
- report / artifact 测试。
- 必要时做实机 smoke，并把真实环境信息清洗成占位符后再写公开文档。

## 文档清单

新增真实仪器后通常要更新：

- `README.md`：用户可见能力和命令示例。
- `docs/README.md`：文档索引。
- `docs/project/reference/WaveBench_配置文件格式.md`：配置字段。
- `docs/project/guides/WaveBench_run_plan_使用指南.md`：run plan step、template 和 safety 说明。
- `docs/project/contributing/WaveBench_新增仪器驱动指南.md`：接入过程出现新的边界时，更新本页。
- 相关命令确认表或设计记录。

如果只是插件 metadata 或声明式 SCPI TOML，应明确说明它尚未进入真实执行路径。
