# WaveBench 文档

[English](README_EN.md) · 中文

WaveBench 是一个用 Python 编写的实验室自动测量台。它提供 CLI、实验性 TUI、显式 run plan、采集包和离线报告。当前仓库开发线为 `0.8.22`，最新稳定 tag 为 `v0.8.0`；查看旧版本时，请打开对应 tag 中的文档。

> [!WARNING]
> 这里的命令有些会连接并控制真实仪器。页面中的示例会标出离线检查、连接读取和硬件写入的区别。执行写入前，请自己确认接线和限制值。

## 从哪开始

### 我还没有接仪器

先回到根目录的 [README](../README.md)，完成安装和离线检查：

```bash
wavebench run template --list
wavebench run template source-scope-sine --output /tmp/wavebench-demo.toml --force
wavebench run check --plan /tmp/wavebench-demo.toml
```

想检查 TUI 界面，可安装 `.[tui]` 后运行 `wavebench tui --fake`。它使用模拟设备。

### 我要配置实验台

- [配置文件格式](project/WaveBench_配置文件格式.md)：TOML 查找顺序、字段和安全限制。
- [示波器接入说明](project/WaveBench_DS1104示波器接入说明.md)：DS1000Z/DS1104Z 的采集和输入语义。
- `project/` 下的 DP800、DG4202 和 DM3000 接入说明：设备特定的配置和边界。

### 我要执行实验

- [run plan 使用指南](project/WaveBench_run_plan_使用指南.md)：模板、`run check`、`run verify`、执行、恢复和报告。
- [数据输出格式](project/WaveBench_数据输出格式.md)：采集包、run、频响和校准产物。
- [示例计划目录](../plans/README.md)：每个计划的硬件副作用和适用范围。

### 我要查参数或接口

- [CLI 形态](project/WaveBench_CLI形态.md)：命令入口和交互/非交互边界。
- [配置文件格式](project/WaveBench_配置文件格式.md)
- [错误处理和日志策略](project/WaveBench_错误处理和日志策略.md)
- [HTTP MCP 只读接口](project/WaveBench_HTTP_MCP_只读接口.md)
- [扫频分析仪公共契约](project/WaveBench_扫频分析仪公共契约.md) · [English contract](project/WaveBench_sweep_analyzer_contract_EN.md)

### 我要安装或开发插件

- [可安装仪器插件用户指南](project/WaveBench_可安装仪器插件.md)：本地目录/wheel 的检查、安装、升级、卸载和恢复。
- [插件开发指南](project/WaveBench_插件开发指南.md)：V2 descriptor、factory、capability 和测试要求。
- [新增仪器驱动指南](project/WaveBench_新增仪器驱动指南.md)：从 driver 到 CLI、run plan 和文档的接入路径。
- [声明式 SCPI 插件](project/WaveBench_声明式SCPI插件.md) · [插件注册表](project/WaveBench_插件注册表.md) · [插件市场索引](project/WaveBench_插件市场索引.md)

### 我要理解设计取舍

- [项目边界](project/WaveBench_项目边界.md)
- [设备抽象层](project/WaveBench_设备抽象层.md)
- [多仪器协同流程设计](project/WaveBench_多仪器协同流程设计.md)
- [sweep 状态保存与恢复](project/WaveBench_sweep状态恢复设计.md)
- [TUI 终端控制面板](project/WaveBench_TUI终端控制面板.md)

这些页面仍保留在原目录中，目录目前处于过渡状态。本页只负责入口，不把阶段日志当作当前使用说明。

## 命令的副作用

| 类别 | 例子 | 是否连接仪器 |
| --- | --- | --- |
| 离线 | `run schema`、`run template`、`run check`、`run report`、`capture inspect`、`tui --fake` | 不连接仪器；TUI 可能写本地日志 |
| 连接读取 | `doctor`、`idn`、`status`、`run verify` | 是，仅读取或做预检 |
| 修改状态 | `scope fetch/capture/autoscale`、source/power setter、output、`run plan` | 是，可能写入、触发或改变输出 |

`run check` 不代表 plan 可以安全执行。它只检查 TOML 和字段；真正执行前，还要核对接线、scope coupling、输出状态、保护限值和 restore 条款。

## 历史资料与厂商手册

`project/` 里还留有 v0.1–v0.4 的路线图、收口清单和早期设计草案。它们用于追溯决策，不是当前能力的承诺。厂商手册和 SCPI 摘录在 [instruments/](instruments/)；它们不进入主导航，使用时请同时核对当前 driver 和 CLI 行为。

## 语言和事实源

中文页面覆盖最完整，英文入口提供对应的安装、离线体验和安全摘要。版本以 `pyproject.toml` 和 Git tag 为准；CLI 语法以 `wavebench --help`，run plan 语法以 `wavebench run schema` 和 `run template --list` 为准。文档中的示例资源必须使用保留地址或占位符，不要写入实验室真实 IP、序列号或串口路径。
