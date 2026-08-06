# WaveBench 项目边界

## 项目定位

WaveBench 是面向电子设计竞赛调试和实验室测量的 Python 自动测量台。它把仪器命令、实验步骤、采集证据和离线报告放在同一套 CLI 与 run plan 中。

WaveBench 优先解决以下问题：

- 减少电脑、仪器和实验记录之间的重复操作；
- 让常用测量步骤可以由脚本执行并留下证据；
- 在执行真实硬件操作前提供离线检查和只读预检；
- 明确记录会改变仪器状态的动作，避免隐式修改。

## 当前范围

| 组件 | 当前能力 | 主要边界 |
|---|---|---|
| 示波器 | RTM2000 / RTM2032、DS1000Z / DS1104Z 的身份查询、状态读取、显式 autoscale、波形读取和采集包 | 型号覆盖以已验证的 driver 和命令为准，不代表完整手册覆盖 |
| 信号源 | DG4000 / DG4202 的状态、基本波形、频率、幅度、输出和任意波上传 | 不提供通用波形编辑器或跨厂商抽象 |
| 电源 | DP800 的状态、保护、设定值和显式输出控制 | `power set` 与 `power output` 是独立动作 |
| 万用表 | DM3000 / DM3058 的常用读数和部分连接方式 | 型号、接口和测量函数以当前 driver 为准 |
| run plan | source、power、scope、dmm、sleep 和频响步骤；包含检查、预检、恢复和质量判断 | 不保证多仪器同步采样 |
| 报告与产物 | CSV、NPY、JSON metadata、命令记录、静态 HTML 报告和 report index | 报告读取已有产物，不代替实时采集 |
| TUI | 电源、万用表和信号源的实验性终端面板 | 不负责 run plan 编辑、完整波形查看或插件管理 |
| 插件 | V2 Python 插件、V1 metadata 和声明式 SCPI 检查 | Python 插件是可信代码，不是安全沙箱 |

## 推荐工作顺序

1. 使用 `run template` 生成 plan。
2. 使用 `run check` 检查 TOML、字段和安全耦合。
3. 使用 `doctor` 或 `run verify` 做连接和身份预检。
4. 明确确认接线、输入阻抗、输出状态和限制值。
5. 使用 `run plan` 执行实验，并检查生成的 `run.json`、`summary.csv` 和采集包。

没有仪器时，可以只执行前两步；`run check` 不连接仪器，也不会打开输出。

## 硬件安全边界

- 不自动发送 `*RST`。
- 不因设置电压或幅度而自动打开输出。
- 不自动改变示波器输入阻抗。
- `scope auto` 是显式操作，可能改变示波器的水平、垂直或触发设置。
- 仪器写入、输出切换和采集触发不做盲目重试。
- source restore 只覆盖文档声明的 basic 字段，不等于完整通道快照。
- 安全 guard 负责查询和拒绝不安全条件，不代替人工确认接线和负载。

## 明确不包含的能力

- 覆盖所有厂商、型号和 SCPI 命令的通用仪器库；
- 以 GUI 取代 CLI 和 run plan；
- 在后台自动修正仪器状态或自动决定输出动作；
- 远程下载、安装或升级插件；
- 将声明式 SCPI 文件当作任意 SCPI 执行入口；
- 为第三方 Python 插件提供进程级安全隔离；
- 把真实 IP、序列号、凭据、串口路径或实验产物写入公开文档。

## 事实源

当前命令以 `wavebench --help`、`wavebench run schema` 和 `wavebench run template --list` 为准。版本变化见根目录 [CHANGELOG](../../../CHANGELOG.md)；逐版原始设计记录可在对应 Git tag 中查阅。厂商编程手册和型号命令表由 [仪器插件仓库](https://github.com/Scaxlibur/wavebench-instrument-plugins) 维护。
