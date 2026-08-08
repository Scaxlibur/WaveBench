---
name: wavebench
description: >-
  Safely diagnose, configure, test, and extend the WaveBench Python measurement
  bench. Use for WaveBench CLI, run plans, capture packages, reports, TUI,
  instrument discovery, oscilloscope capture, signal-generator control,
  programmable-power-supply or digital-multimeter measurements, and WaveBench
  instrument plugins. Do not use for general electronics theory or unrelated
  VISA/SCPI projects.
license: MIT
compatibility: >-
  Codex or a compatible Agent Skills host; Python 3.11+; Linux or WSL
  recommended. Live instrument operations require the project virtual
  environment, configured LAN/VISA access, confirmed wiring, and explicit
  authority for writes.
metadata:
  author: "WaveBench maintainers"
  version: "2.0.0"
  project: "wavebench"
  specification: "https://agentskills.io/specification"
---

# WaveBench skill workflow

## Core objective

在不意外改变真实硬件的前提下，完成 WaveBench 的诊断、配置、测量、测试和扩展。优先使用能证明结果的最小操作，先做离线或只读检查，为每次实时写入保留可复核证据。

## Start every task

1. 用 `git rev-parse --show-toplevel` 定位仓库根目录，并从根目录工作。
2. 先读取 `README.md`、`pyproject.toml` 和与任务直接相关的 `docs/project/` 文档。
   当前 CLI 事实源依次为实现、`--help`、`run schema`、`run template --list` 和
   `wavebench.example.toml`；技能正文与旧记忆不能覆盖这些事实源。
3. 执行 `git status --short --branch`，保留无关用户改动；禁止 reset、强制覆盖或隐式清理。
4. 将任务归类为离线说明/评审、离线代码或配置、实时只读诊断、受控写入或采集。
5. 在安装依赖、编辑配置或连接硬件前，说明计划、影响范围、预期结果和恢复边界。

## Risk classes

| 类别 | 典型操作 | 默认处理 |
| --- | --- | --- |
| 离线 | `run schema`、`run template`、`run check`、报告、包检查 | 可直接执行，不连接仪器 |
| 实时只读 | `doctor`、`idn`、`status`、`profile`、`run verify` | 先说明连接范围；记录有状态查询的副作用 |
| 实时写入 | setter、output、trigger、fetch、capture、autoscale、TUI、`run plan` | 必须通过写入门禁并在结束后回读 |

`doctor` 会联系配置中的仪器；`run check` 不做仪器 I/O。错误队列、`scope fetch`、通道启用和传输格式设置都可能改变设备状态，不能笼统归为无副作用读取。

## Non-negotiable safety gates

进行任何 setter、输出切换、采集、扫频或验收脚本前：

1. 确认明确的实时写入授权、当前接线和目标资源。
2. 查询并记录 IDN、相关初始状态、输出状态、保护设置和耦合/负载上下文。
3. 检查配置的电压、电流、Vpp、频率、超时和重试上限；禁止为通过测试而静默放宽限制。
4. 按驱动能力执行高阻输入检查；禁止自动加入 `--allow-50ohm` 或等效覆盖项。
5. 优先使用已通过 `run check` 的计划或既有验收脚本，不把松散命令串当作安全流程。
6. 明确写前快照、计划恢复范围和不会恢复的设置。

写入期间和结束后：

- 禁止盲目重试写入、触发、采集和输出转换；二进制采集不使用普通读取重试。
- 写入结果不明确或恢复失败时，保持受影响输出为 OFF，停止后续写入并保存证据。
- 恢复后重新查询真实仪器，不以进程退出码代替状态确认。
- 交接中写出产物目录、最终设备状态、未恢复设置和所有跳过或失败的检查。

完整安全语义见 [safety-and-recovery.md](references/safety-and-recovery.md)。

## Load only the relevant reference

触发技能后只读取与任务匹配的文件，不预加载整个 `references/`：

| 任务 | 追加读取 |
| --- | --- |
| 恢复、状态漂移、停止策略、安全门 | [safety-and-recovery.md](references/safety-and-recovery.md) |
| `run check`、`run verify`、`run plan`、报告、恢复 | [run-plans.md](references/run-plans.md) |
| 示波器、通道设置、波形采集 | [scope-and-capture.md](references/scope-and-capture.md) |
| 信号发生器、波形、谐波、源状态 | [source-and-harmonics.md](references/source-and-harmonics.md) |
| 可编程电源、数字万用表、保护和测量 | [power-and-dmm.md](references/power-and-dmm.md) |
| 插件发现、检查、安装、升级、回滚 | [plugins.md](references/plugins.md) |
| CLI/TUI/报告开发、代码修改、验证和交接 | [development-validation.md](references/development-validation.md) |
| 技能维护或触发回归 | [eval-prompts.md](references/eval-prompts.md) |

Reference 只从本入口直接链接，保持一层目录；详细命令和型号边界不得复制回入口。

## Environment and data boundaries

- 要求 Python 3.11+；优先使用 `.venv/bin/python` 和 `.venv/bin/wavebench`。
- `.venv` 不存在或过期时，先说明安装影响；禁止未经授权修改系统 Python。
- `wavebench.toml` 是本地实验室状态；不要把真实资源地址、序列号或设备标识写入跟踪文件。
- `data/` 是生成证据；提交采集包、截图、快照或日志前先检查敏感标识。
- 优先使用 WaveBench CLI、Service 和驱动；裸 SCPI 仅用于明确授权、命令已记录且有安全/恢复方案的驱动探针。

## Discover actual capabilities

不要仅凭型号或 README 推断能力。先确认已启用的驱动、来源、版本和 capability：

```bash
.venv/bin/wavebench plugin list --load
.venv/bin/wavebench plugin installed
.venv/bin/wavebench plugin info <driver-id> --installed
.venv/bin/wavebench plugin doctor --load
.venv/bin/wavebench capability explain <operation> --config wavebench.toml
```

插件 descriptor 的加载会导入可信第三方 Python 代码。能力门拒绝是预期的 fail-closed 结果，不得用裸 SCPI 绕过。

## Standard workflows

离线或只读预检优先使用：

```bash
.venv/bin/python -m pip check
.venv/bin/wavebench run check --plan plans/<plan>.toml --config wavebench.toml
.venv/bin/wavebench run verify --plan plans/<plan>.toml --config wavebench.toml
```

真实计划必须遵循 `run check → run verify → run plan → run report`，并同时检查步骤状态、质量门、期望指标、产物和最终设备状态。TUI 界面开发使用 `tui --fake`。

## External research

只有用户明确要求厂商资料、标准或最新外部信息时才联网检索。优先官方文档，记录来源和日期，不发送本地配置、序列号、网络地址或实验数据。`tavily_hikari` 等搜索 MCP 为可选能力；不可用时说明限制并使用仓库事实源或已能访问的官方页面，不伪造工具调用。

## Code, docs, and handoff

代码改动遵循外科手术式修改：先读实现、契约和聚焦测试，再改最小范围并补测试。公开中文 Markdown 使用项目文档规范，保留代码字面量、路径、URL 和配置键的原样格式。

验证强度按风险匹配：

```bash
.venv/bin/python -m pytest -q tests/<focused-test>.py
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
git diff --check
```

交接先给结论，再列检查结果、产物路径、最终状态、未恢复设置、剩余能力缺口，以及是否改动跟踪文件、本地配置、虚拟环境或真实仪器。不得用笼统成功描述掩盖跳过、失败、部分产物或恢复错误。
