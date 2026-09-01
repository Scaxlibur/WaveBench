# WaveBench 文档

[English](README_EN.md) · 中文

WaveBench 是一个用 Python 编写的实验室自动测量台，提供 CLI、实验性 TUI、显式 run plan、采集包和离线报告。当前开发线为 `0.8.26`，最新稳定 tag 为 `v0.8.0`。版本变化见 [更新日志](../CHANGELOG.md)；旧版本原始文档可切换到对应 Git tag 查看。

> [!WARNING]
> 部分命令会连接并控制真实仪器。示例会区分离线检查、连接读取和硬件写入；执行写入前，应确认接线和限制值。

## 从这里开始

### 尚未连接仪器

先阅读根目录的 [README](../README.md)，完成安装和离线检查：

```bash
wavebench run template --list
wavebench run template source-scope-sine --output /tmp/wavebench-demo.toml --force
wavebench run check --plan /tmp/wavebench-demo.toml
```

安装 `.[tui]` 后可运行 `wavebench tui --fake` 检查 TUI 界面；该模式使用模拟设备。

### 配置实验台

- [配置文件格式](project/reference/WaveBench_配置文件格式.md)：TOML 查找顺序、字段和安全限制。
- 仪器型号命令和编程手册由 [仪器插件仓库](https://github.com/Scaxlibur/wavebench-instrument-plugins) 维护；本仓库只记录 WaveBench 的接入边界。

### 配置示波器联合视图

`wavebench scope focus` 接受重复的 `--channel`、可选的 `--time-range`、重复的
`--vertical-scale CHANNEL=V_PER_DIV` 和 `--hide-others`。Core 只定义可移植事务：插件 profile
声明模拟通道、数值范围、容差和 I/O 预算；Core 读取完整联合 baseline，成功后保留目标视图，失败时
恢复并重新查询。未声明 `scope.focus_configure_v2` 的插件会在仪器 I/O 前拒绝操作。

该命令会修改仪器状态，但不会启动采集、调用 autoscale、修改耦合或切换输入终端。执行前应核对
接线、输入状态和插件 capability。

### 使用 RF 信号源

`rf_source` 不复用普通 `source` 的 Vpp、offset 或数字 channel 模型。先从 [RF 信号源使用指南](project/guides/WaveBench_RF信号源使用指南.md) 确认当前 production capability 和端接声明；DSG830 已开放固定 profile 的调制输出，以及唯一受验证的后面板 `pulse_in_out` output 路径。后者不代表 Pulse input、`TRIGGER IN` 或同步能力。需要实现新型号或查看证据门时，再阅读 [领域设计](project/design/WaveBench_RF信号源设计.md) 与 [开发里程碑](project/design/WaveBench_RF信号源开发里程碑.md)。

### 执行实验

- [run plan 使用指南](project/guides/WaveBench_run_plan_使用指南.md)：模板、`run check`、`run verify`、执行、恢复和报告。
- [数据输出格式](project/reference/WaveBench_数据输出格式.md)：采集包、run、频响和校准产物。
- [示例计划目录](../plans/README.md)：每个计划的硬件副作用和适用范围。

### 查询参数或接口

- [CLI 形态](project/guides/WaveBench_CLI形态.md)：命令入口和交互/非交互边界。
- [配置文件格式](project/reference/WaveBench_配置文件格式.md)
- [错误处理和日志策略](project/reference/WaveBench_错误处理和日志策略.md)
- [HTTP MCP 只读接口](project/guides/WaveBench_HTTP_MCP_只读接口.md)
- 插件接口以当前源码中的 Protocol、models 和对应插件文档为准。

### 安装或开发插件

- [可安装仪器插件用户指南](project/guides/WaveBench_可安装仪器插件.md)：本地目录/wheel 的检查、安装、升级、卸载和恢复。
- [插件开发指南](project/contributing/WaveBench_插件开发指南.md)：V2 插件的接入流程和发布检查。
- [可执行仪器插件 API 约定](project/reference/plugins/WaveBench_可执行仪器插件API.md)：descriptor、factory、context、capability 和兼容边界。
- [新增仪器驱动指南](project/contributing/WaveBench_新增仪器驱动指南.md)：从 driver 到 CLI、run plan 和文档的接入路径。
- [声明式 SCPI 插件](project/reference/plugins/WaveBench_声明式SCPI插件.md) · [插件注册表](project/reference/plugins/WaveBench_插件注册表.md) · [插件市场索引](project/reference/plugins/WaveBench_插件市场索引.md)

### 理解设计取舍

- [项目边界](project/design/WaveBench_项目边界.md)
- [设备抽象层](project/design/WaveBench_设备抽象层.md)
- [多仪器流程设计](project/design/WaveBench_多仪器协同流程设计.md)
- [sweep 状态保存与恢复](project/design/WaveBench_sweep状态恢复设计.md)
- [TUI 终端控制面板](project/guides/WaveBench_TUI终端控制面板.md)

目录分类见 [project/README](project/README.md)。本页只负责入口，不把阶段记录当作当前使用说明。

## 命令的副作用

| 类别 | 例子 | 是否连接仪器 |
| --- | --- | --- |
| 离线 | `run schema`、`run template`、`run check`、`run report`、`capture inspect`、`tui --fake` | 不连接仪器；TUI 可能写本地日志 |
| 连接读取 | `doctor`、`idn`、`status`、`run verify` | 是，仅读取或做预检 |
| 修改状态 | `scope focus/fetch/capture/autoscale`、source/power setter、output、`run plan` | 是，可能写入、触发或改变输出 |

`run check` 不代表 plan 可以安全执行。它只检查 TOML 和字段；真正执行前，还要核对接线、scope coupling、输出状态、保护限值和 restore 条款。

## 历史资料与厂商手册

版本历史统一放在根目录的 [CHANGELOG.md](../CHANGELOG.md)。厂商手册和 SCPI 摘录由 [仪器插件仓库](https://github.com/Scaxlibur/wavebench-instrument-plugins) 维护；本仓库只保留 WaveBench 自身的接入边界、接口、配置和安全语义。

## 语言和事实源

中文页面覆盖最完整，英文入口提供对应的安装、离线体验和安全摘要。版本以 `pyproject.toml` 和 Git tag 为准；CLI 语法以 `wavebench --help`，run plan 语法以 `wavebench run schema` 和 `run template --list` 为准。公开文档中的示例资源必须使用保留地址或占位符，不得写入实验室真实 IP、序列号或串口路径。
