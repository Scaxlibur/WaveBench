# WaveBench

[English documentation](doc/README_EN.md)

> [!WARNING]
> 本项目仍处于早期开发阶段，会读写真实实验设备。错误操作可能导致器件、仪器损坏甚至人身伤害，使用时请务必小心。

WaveBench 是一个面向电子设计竞赛调试场景的轻量 Python 自动测量台。

它提供小而明确的 CLI 命令，用于控制局域网内的实验室仪器，也开始提供实验性的终端 TUI 控制面板。当前重点是可靠采集示波器波形、做信号源到示波器的闭环检查、控制可编程电源、读取万用表，以及用显式 run plan 编排多仪器实验。WaveBench 不做隐藏复位，也不偷偷打开或关闭输出。

WaveBench 主包长期预装 RTM2000/RTM2032、DS1104Z/DS1000Z、DG4000/DG4202、DP800 和 DM3000/DM3058 系列驱动；首次使用无需安装外置插件，只需复制示例配置并填写实际仪器 resource。这五个仪器族没有从主包移除的计划；外置插件是显式选择、独立发布的可选升级或扩展，不会取代示例配置使用的内建短名。

> [!IMPORTANT]
> 当前开发版本是 `v0.8.22`。`v0.8.22` 增加低阶预设谐波的 profile/configuration 模型、驱动协议和 capability-gated service 入口：公开写入仅限 EVEN、ODD、ALL；USER 位图与 H2–H16 幅度/相位仅供完整回读和恢复。只有具备逐字段回读、完整恢复和歧义写入锁存的外置插件才能声明这些能力，内建 DG4202 fallback 保持不声明；external modulation source、advanced digital modulation、用户自定义谐波与 DAC16 仍未公开。使用其他 Release 的读者应以对应 tag 内的文档和命令为准。

## 🌟 特别鸣谢

<p align="center">
  <a href="https://linux.do">
    <img src="doc/images/linuxdo.png" alt="LINUX DO" width="420" />
  </a>
</p>
<p align="center"><b>学AI，上L站！祝小破站越来越好～</b></p>

## 当前能力

### 示波器：R&S RTM2032 与 RIGOL DS1104Z/DS1000Z

- LAN VISA 连接
- `scope idn`、`scope errors`；声明相应 capability 的驱动还支持只读 `scope status`、`scope acquisition-status`、`scope history-timestamps` 与 `scope measurement-statistics`
- 显式 `scope auto` / `scope autoscale`
- `scope fetch` 与 `scope capture`；默认先只读确认输入为高阻，50 Ω 需显式 `--allow-50ohm`
- 声明 `scope.capture_average` 的驱动可执行受控平均采集；公共结果要求逐项恢复并返回恢复前后配置证据
- 声明 `scope.digital_status` 的驱动可读取既有 MSO 数字通道状态；该能力不读取数字波形，也不隐式配置阈值、显示或传输格式
- 声明 `scope.digital_waveform` 的驱动可在调用方明确确认采集已停止后读取既有数字轨迹；返回值按 Dn→bit n 合并为 `uint16`，驱动不得改点数、传输格式、显示、阈值或采集状态
- 通过重复 `--channel` 在一次 acquisition 中读取多通道；每个通道完成后立即原子落盘
- DS1104Z 支持 CH1–CH4、NORM 屏幕波形、RAW 存储波形分块读取和 PNG 截图
- DS1104Z 使用 `:WAVeform:PREamble?` 将 BYTE 数据换算为时间/电压；RAW 每块最多读取 250000 点
- 采集包包含 NPY / CSV / JSON metadata / `commands.log`
- 波形指标：Vpp、RMS、均值、频率估计、占空比、适用时的上升/下降时间
- 波形质量告警：周期数过少、每周期采样点过少、幅度过低、频率不匹配

### 信号源：RIGOL DG4202

- `source idn`、`source status`
- `source profile`：只读查询 basic 状态之外的负载、极性、噪声、同步、burst、调制、marker 与 pulse hold 上下文
- `source sweep-profile`：只读查询仪器当前内置 sweep 的频率、时序、触发与 marker 全量 profile；不启动、停止或触发 sweep
- 公共 Python API 提供完整 sweep 配置事务与独立显式触发契约；具体驱动必须自行实施输出、负载、频率、回读、恢复与歧义写锁存边界。MANUAL 配置与触发必须通过同一个由调用方持有并关闭的持久 source session，临时会话会在连接仪器前拒绝这两种操作
- `source counter-profile`：只读查询独立 counter 输入配置、统计状态及已有测量；不会自动启用 counter 或清除统计
- `source set-freq`
- `source set-func`：`sin`、`squ`、`ramp`/`triangle`、`puls`、`nois`、`dc`
- `source set-vpp`
- `source set-duty`
- `source arb-probe`：只查询任意波 SCPI 支持情况
- `source arb-load --dry-run`：离线校验任意波 payload
- `source arb-load --frequency ... --output-on`：已确认可用的 DG4202 `DATA:DAC VOLATILE` 任意波上传
- `source output`
- `sweep discrete`：信号源到示波器的离散扫频，默认同样检查示波器高阻输入
- 离散扫频可选 `--restore-source-state` 恢复 basic 信号源状态（输出、函数、频率、Vpp、方波占空比）

### 电源：RIGOL DP800 系列

- `power idn`、`power status`
- `power protection status`
- `power protection set --ovp-threshold --ovp on|off --ocp-threshold --ocp on|off`
- `power set --voltage --current-limit`
- `power output on|off`
- 可配置读回等待：
  - `power.settle_ms_after_set`
  - `power.settle_ms_after_output`


### 万用表：RIGOL DM3000 / DM3058 系列

- `dmm idn`
- `dmm read dcv|acv|dci|aci|res|fres|freq|period|continuity|diode|cap`
- `dmm function status|set`、`dmm profile`：读取/设置当前测量功能，或只读当前量程码与 DCV 阻抗；不自动切换功能。
- `dmm range set dcv|acv 0..4`、`dmm impedance set 10M|10G`：受控配置、回读和失败恢复；`10G` 仅允许 DCV 档位码 `0..2`。
- `dmm trigger status`、`dmm calculation status`：只读当前触发与运算状态，不写设置、不清空统计、不触发测量。
- `dmm system-interface status`：只读并脱敏输出蜂鸣器、语言、数字格式、亮度、选件状态、DHCP、GPIB 和 RS-232 状态；不查询设备/网络标识，也不写配置。
- `dmm calculation statistics average|min|max --calculation-active-confirmed`：仅读取已启用且当前模式匹配的统计；调用者确认后驱动仍会复核模式。
- 支持 DM3058 LAN/VISA 与 RS232 读取；RS232 可配置行终止符和流控，DM3058 实机基线为 9600 8N1、写 CRLF、读 LF、无流控。设备 SCPI 保留在 DMM driver，不进入 CLI / service。
- 可配置 DMM 正式读取前等待：`dmm.settle_ms_before_read`
- 可用 `python scripts/dmm_dcv_staircase_smoke.py --config <toml>` 对 `DP800 -> DMM` 做保守 DCV 阶梯 smoke，并自动恢复电源输出。
- 可用 `python scripts/dmm_acv_source_smoke.py --config <toml>` 对 `DG4202 -> DMM` 做保守 ACV/RMS smoke，并自动恢复信号源状态。


### 终端 TUI：实验性控制面板

- TUI 是 `wavebench[tui]` optional extra；源码环境可用 `python -m pip install -e ".[tui]"` 安装界面依赖。
- `tui`：启动 Textual 终端界面，默认读取当前目录的 `wavebench.toml`。
- `tui --config <toml>`：从指定 TOML 读取仪器配置，适合不在配置文件目录启动时使用。
- `tui --fake`：使用模拟电源、模拟万用表和模拟信号源，不连接真实仪器，适合检查界面。
- `tui --refresh-interval 5`：设置自动刷新间隔；默认 5 秒。
- `tui --log-file <path>`：指定 TUI 调试日志文件；默认写入 `data/tui/wavebench-tui.log`。
- TUI 持久日志行数限制可在 `[tui]` 中配置，默认超过 10000 行后保留最新 1000 行。
- 电源面板支持三通道状态查看、设置电压/限流、开关输出、查看和设置 OVP/OCP。
- 万用表面板支持常用挡位按钮切换和手动读取。
- 信号源面板支持查看状态、设置波形/频率/幅度和切换输出。
- 当前范围冻结为电源、万用表和信号源三个面板；核心能力以 CLI、run plan 和 Service 为准。TUI 不承担 run-plan 编排、插件管理、完整示波器波形查看或报告系统。

### 多仪器 run plan

- `run check --plan <plan.toml>`：只解析并汇总 plan，不连接仪器
- `run verify --plan <plan.toml>`：只读查询 plan 涉及仪器的高阻保护状态与 `*IDN?`，用于执行前预检可达性
- `run template --list` / `run template <name> --output <plan.toml>`：列出或生成保守 run plan 模板；可用 `--frequency`、`--vpp`、`--source-channel` 等少量参数定制；不连接仪器，不覆盖已有文件，除非显式 `--force`
- `run plan --plan <plan.toml>`：执行显式 source、power、scope、dmm、sleep 步骤；一次 run 内统一打开并复用所需仪器 session，成功或失败后统一关闭，不静默断线重连
- `run report <run_dir>`：根据 `run.json` / `summary.csv` 生成静态离线 HTML 报告，包含信号分析指标、DMM 读数卡片、实验证据摘要、产物链接、证据时间线和截图
- `capture inspect <capture_dir>`：打印离线采集包摘要
- 默认示波器高阻保护：`scope.capture` / `scope.fetch` / `sweep discrete` / run-plan `scope.capture` 在采集前查询通道耦合。RTM2032 的 `DCL`/`ACL` 视为高阻，`DC`/`AC` 默认按可能的 50 Ω 拒绝；DS1000Z 输入固定为 1 MΩ，`AC`/`DC`/`GND` 只表示耦合方式，均按该机型语义检查。WaveBench 不会自动修改耦合或输入设置
- 可选 `[restore] source_state = true`：在 `finally` 路径快照并恢复 basic 信号源通道状态（输出、函数、频率、Vpp、方波占空比）。该选项不恢复 offset、phase、frequency mode、sweep、负载、极性、噪声、同步、burst、调制、marker、pulse hold 或易失任意波内存；run artifact 以 `source_state_scope = "basic"` 明示范围
- run 输出位于 `data/runs/<timestamp>_<label>/`，包含 `run.json`、`summary.csv`、步骤记录、质量状态和普通采集包引用
- `scope.capture` 可启用 `quality_gate = true`；配合 `auto_recover = true` 时，质量告警会触发最多 `[quality].auto_recover_attempts` 次 autoscale + 重采
- 多次告警采集若测量结果在 `[quality]` 容差内保持稳定，可标记为 `ok_by_consistency`
- `scope.capture` 可包含 `[steps.expect]` 指标约束；expect 失败会把 run 标记为 `failed`，但保留采集产物

### 网络发现：只读辅助工具

- `doctor --config <toml>`：只读检查当前配置里的 scope/source/power/dmm 资源，查询 `*IDN?` 并给出可达性、型号匹配和排错建议
- `doctor --discover-subnet <cidr>`：当配置资源不可达或型号不匹配时，顺手扫描网段并按 IDN 匹配可能的替代 resource；只建议，不修改配置
- `net discover --subnet <cidr>`：只读扫描局域网内疑似 SCPI/VISA 仪器，用于 DHCP 漂移后找回当前 IP
- 默认探测 `5025`、`5555` 和 `111` 端口；对 SCPI socket 候选只发送只读 `*IDN?`
- 输出可复制的 VISA resource 候选；不会修改 `wavebench.toml`，也不会打开/关闭任何仪器输出
- 发现命令是救急工具；稳定实验环境仍建议在路由器/DHCP 服务里按 MAC 地址固定仪器 IP

### 可安装仪器插件与只读 metadata

- `plugin list`：列出当前可用的仪器插件 metadata
- `plugin info <driver_id>`：查看单个插件的型号、能力、IDN 匹配和配置字段
- `plugin doctor`：检查插件 metadata 的 API 版本、能力命名、类型和加载错误
- `plugin package check <folder|wheel>`：检查受信任的本地源码目录或 wheel；源码目录会先离线构建 wheel，但不会修改当前 venv
- `plugin install <folder|wheel> [--dry-run]`：离线安装本地插件到当前 venv
- `plugin installed` / `plugin info <driver_id> --installed`：核对受管账本、distribution、entry point 与安装文件状态
- `plugin upgrade|downgrade <folder|wheel> [--dry-run]`：使用明确的本地目标包替换健康的受管插件
- `plugin remove <driver_id> [--dry-run]`：只移除健康且 ownership 一致的受管插件
- `plugin recover`：检查未完成事务，并仅在可证明旧态或目标态时恢复
- `plugin list/info/doctor --load`：显式加载并检查 `wavebench.instruments` V2 可执行 driver descriptor
- `plugin market search [query]`：搜索本地插件市场 JSON 索引
- `plugin market info <plugin_id>`：查看本地插件市场条目
- `plugin scpi check <path>`：检查本地声明式 SCPI 插件 TOML
- `plugin scpi doctor <path>`：诊断本地声明式 SCPI 插件，可显式加 `--probe --resource <VISA>` 做只读 IDN 匹配
- `plugin scpi info <path>`：查看本地声明式 SCPI 插件 metadata
- `plugin scpi probe <path> --resource <VISA>`：对单个资源执行插件声明的只读 IDN 查询
- 默认只显示 WaveBench 内置插件，不导入第三方包
- 只有显式传入 `--include-entry-points` 时，才加载 Python `wavebench.drivers` entry points
- 配置选中第三方 canonical driver ID 并执行仪器动作时，才按需加载对应 `wavebench.instruments` entry point
- Python 可执行插件是可信代码扩展，不是安全沙箱；WaveBench 只安装用户明确指定的本地受信任包，不自动下载或联网安装依赖
- 插件市场当前只读本地 JSON index，不安装插件，不导入市场条目里的包
- 声明式 SCPI 插件默认只读取并校验本地 TOML；只有显式执行 `plugin scpi probe` 才会对单个资源发送 TOML 中声明的只读 IDN 查询
- 安装与卸载见 [可安装仪器插件用户指南](doc/project/WaveBench_可安装仪器插件.md)，作者接口见 [插件开发指南](doc/project/WaveBench_插件开发指南.md)
- 扫频分析仪插件应复用 [扫频分析仪公共契约](doc/project/WaveBench_扫频分析仪公共契约.md)；当前仅提供核心 API，不代表任何具体型号已接入

### HTTP MCP 只读接口

- `mcp serve`：启动本机 HTTP MCP 只读服务
- 默认监听 `127.0.0.1:8765`；显式传入 `0.0.0.0` 会被拒绝
- `/health`：健康检查，不需要 token
- `/mcp`：MCP JSON-RPC 入口，支持 `initialize`、`tools/list`、`tools/call`，需要 Bearer token
- `/tools`：兼容接口，返回当前只读工具列表，需要 Bearer token
- `/call`：兼容接口，调用只读工具，需要 Bearer token
- 当前只读工具：
  - `run.schema`：返回 run plan schema
  - `run.check`：只解析并检查 `plans/*.toml` 下的 run plan，不连接仪器
  - `capture.inspect`：读取 `data/raw/` 下的离线采集包摘要
- `/mcp` 与 `/call` 的 JSON 请求体有 1 MiB 上限；路径参数按工具限制在项目内固定目录

## 安全默认值

WaveBench 避免隐藏的高影响动作：

- 默认不发送 `*RST`
- `scope capture` 不会自动 autoscale，除非用户显式请求
- `scope fetch` / `scope capture` 默认拒绝可能的 50 Ω 输入；只查询状态，不自动切换 coupling
- `power set` 不会打开或关闭输出
- `power output` 不会修改电压或电流限制
- `power protection` 与普通电压/限流和输出控制分离；写入保护阈值前会检查当前设定值和安全上限
- `sweep discrete` 不会恢复 basic 信号源状态，除非显式传入 `--restore-source-state`；该选项不承诺恢复完整通道 profile 或易失任意波内存
- `sweep discrete` 默认拒绝示波器 50 Ω 输入；确认安全后才可传 `--allow-50ohm`
- 命令不应静默修改示波器输入阻抗
- run-plan 安全保护可以查询仪器状态并拒绝执行，但不能自动修正硬件设置
- HTTP MCP 默认只监听 `127.0.0.1`，拒绝 `0.0.0.0`，并对 `/mcp`、`/tools` 与 `/call` 强制 Bearer token
- HTTP MCP 当前只暴露只读工具，不提供 raw SCPI、输出开关、run 执行或任何会改变仪器状态的工具
- `plugin list/info/doctor` 默认不导入第三方插件；V1 metadata 用 `--include-entry-points`，V2 可执行 descriptor 用 `--load`
- 插件生命周期命令拒绝系统 Python，只写当前 venv；安装固定离线、`--no-deps`，不会修改 `wavebench.toml`
- 源码包检查和 dry-run 会执行该源码声明的 build backend；只有 wheel 静态检查不执行包内代码

用示波器测量电源时，请保持示波器输入在安全的高阻模式。除非已经确认电压和仪器限制，否则不要切换到 50 Ω 端接。

## 部署与首次配置

WaveBench 当前以源码仓库部署为主，要求 Python 3.11 或更高版本。推荐为每套实验环境建立独立虚拟环境，使 WaveBench、仪器插件和厂商 SDK 的版本彼此隔离。主包已经包含五个常用仪器族的内建驱动；如果内建驱动满足需求，可以跳过后面的“安装外置仪器插件”。

### Linux / WSL 推荐部署

克隆仓库并在仓库根目录创建虚拟环境：

```bash
git clone https://github.com/Scaxlibur/wavebench.git
cd wavebench
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp wavebench.example.toml wavebench.toml
```

需要运行测试和代码检查时安装开发依赖；需要终端 TUI 时安装 `tui` extra：

```bash
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pip install -e ".[tui]"
```

编辑 `wavebench.toml`，填写实际使用的 VISA/串口 resource，并删除或禁用不属于当前实验台的仪器段。示例配置使用内建短名：

| 仪器族 | 内建 `driver` 短名 |
|---|---|
| R&S RTM2000 / RTM2032 | `rtm2032` |
| RIGOL DS1000Z / DS1104Z | `ds1104` 或 `ds1000z` |
| RIGOL DG4000 / DG4202 | `dg4202` |
| RIGOL DP800 | `dp800` |
| RIGOL DM3000 / DM3058 | `dm3000` 或 `dm3058` |

先列出保守 run-plan 模板，生成一份不会覆盖已有文件的本地 plan，再执行不连接仪器的解析检查。之后执行只读身份和安全预检：

```bash
.venv/bin/wavebench run template --list
.venv/bin/wavebench run template source-scope-sine --output local-source-scope.toml
.venv/bin/wavebench run check --plan local-source-scope.toml
.venv/bin/wavebench doctor --config wavebench.toml
.venv/bin/wavebench scope idn --config wavebench.toml
```

`run check` 只读取本地 plan。`doctor` 和 `scope idn` 会连接配置中的仪器并执行只读查询，但不会打开输出或修改配置。首次执行任何写命令前，应在仪器面板上再次确认接线、输入阻抗、输出状态和保护限值。

如果只想临时从源码树运行，不安装 editable package，可在仓库根目录设置 `PYTHONPATH=src`；这种方式不适合安装受管仪器插件，也不建议作为长期部署：

```bash
PYTHONPATH=src python -m wavebench plugin list
```

### Windows + WSL 部署

推荐把 Python 环境、测试和 LAN 仪器访问统一放在 WSL 中。首次在 WSL 终端进入仓库后，建立脚本默认使用的 `.venv-wsl`：

```bash
python3 -m venv .venv-wsl
.venv-wsl/bin/python -m pip install -e .
cp wavebench.example.toml wavebench.toml
```

随后可在 Windows PowerShell 中通过 `scripts/wsl-run.ps1` 执行 WaveBench 或测试：

```powershell
.\scripts\wsl-run.ps1 wavebench doctor --config wavebench.toml
.\scripts\wsl-run.ps1 wavebench scope idn --config wavebench.toml
.\scripts\wsl-run.ps1 pytest -q
```

指定 WSL 发行版或跳过虚拟环境激活：

```powershell
.\scripts\wsl-run.ps1 -Distro Ubuntu wavebench doctor --config wavebench.toml
.\scripts\wsl-run.ps1 --no-venv python3 --version
```

原生 Windows 可以导入和运行不依赖 POSIX 文件锁的 CLI 路径，但受管插件安装、升级、卸载和恢复需要 `fcntl` 文件锁，因此不支持原生 Windows。需要外置仪器插件时请使用 WSL 或 Linux 虚拟环境。

### 安装外置仪器插件（可选）

外置插件不是 WaveBench 的部署前置条件。它用于独立升级某个仪器族、选择特定 transport，或增加主包没有的仪器。插件是以当前用户权限运行的可信 Python 代码，只应安装已经审查并确认来源的本地源码目录或 wheel。

插件必须安装到运行 WaveBench 的同一个虚拟环境。受管安装器拒绝系统 Python，不联网解析依赖，并固定使用 `pip --no-deps --no-index`；因此 WaveBench 和插件声明的额外依赖必须事先存在于该环境。源码目录在检查和 dry-run 时也会执行其 build backend；若只希望做静态包检查，请使用来源已核验的 wheel。

如果插件仓库与主仓库放在同一父目录，可按以下顺序检查并安装 DS1000Z 插件：

```bash
git clone https://github.com/Scaxlibur/wavebench-instrument-plugins.git ../wavebench-instrument-plugins

.venv/bin/wavebench plugin package check \
  ../wavebench-instrument-plugins/packages/wavebench-rigol-ds1000z
.venv/bin/wavebench plugin install \
  ../wavebench-instrument-plugins/packages/wavebench-rigol-ds1000z --dry-run
.venv/bin/wavebench plugin install \
  ../wavebench-instrument-plugins/packages/wavebench-rigol-ds1000z
```

安装 wheel 时，把最后一个参数替换为 wheel 路径即可。安装完成后检查受管账本、文件摘要、descriptor 和 capability：

```bash
.venv/bin/wavebench plugin installed
.venv/bin/wavebench plugin info rigol.ds1000z --installed
.venv/bin/wavebench plugin info rigol.ds1000z --load
.venv/bin/wavebench plugin doctor --load
```

外置实现必须通过 canonical driver ID 显式选择；仅安装插件不会修改 `wavebench.toml`，也不会改变内建短名的解析结果：

| 外置 distribution | 配置中的 canonical `driver` | 内建短名仍指向 |
|---|---|---|
| `wavebench-rigol-ds1000z` | `rigol.ds1000z` | `ds1104` / `ds1000z` |
| `wavebench-rigol-dg4000` | `rigol.dg4202` | `dg4202` |
| `wavebench-rigol-dm3000` | `rigol.dm3000` | `dm3000` / `dm3058` |
| `wavebench-rigol-dp800` | `rigol.dp800` | `dp800` |
| `wavebench-rohde-schwarz-rtm2000` | `rohde-schwarz.rtm2032` | `rtm2032` |
| `wavebench-shengpu-sp3000a` | `shengpu.sp30120` | 无内建实现 |

例如，安装 DS1000Z 插件后，需要显式修改配置才能选择外置实现：

```toml
[scope]
driver = "rigol.ds1000z"
model_hint = "DS1104Z Plus"
default_channel = 1
check_errors = true
```

DG4000、DM3000、DP800 和 RTM2000 的 canonical ID 是受限覆盖槽位：卸载对应外置包后会回退到主包内建实现。`rigol.ds1000z` 和 `shengpu.sp30120` 是独立 canonical ID，卸载后必须重新安装插件，或手工把配置改回可用的内建 driver。不要假设外置插件与任意 WaveBench 版本兼容；源码部署时优先使用同一开发线，并以插件 `pyproject.toml` 和 descriptor 声明的版本范围为准。

升级、卸载或中断恢复同样先做 dry-run，并继续使用同一个虚拟环境：

```bash
.venv/bin/wavebench plugin upgrade <folder-or-wheel> --dry-run
.venv/bin/wavebench plugin remove rigol.ds1000z --dry-run
.venv/bin/wavebench plugin recover
```

完整的覆盖槽位、后端选择、升级/降级、事务恢复和故障排查规则见[可安装仪器插件用户指南](doc/project/WaveBench_可安装仪器插件.md)。开发新插件见[插件开发指南](doc/project/WaveBench_插件开发指南.md)。

## 示例命令

采集示波器波形：

```powershell
python -m wavebench scope capture --config wavebench.toml --channel 1 --label smoke --points def --window-frequency 1000 --target-cycles 10 --expect-frequency 1000 --frequency-tolerance 0.05 --target-vpp 1.0 --no-csv
python -m wavebench scope capture --config wavebench.toml --channel 1 --label smoke_with_screen --points def --no-csv --screenshot
```

DS1104Z 配置示例：

```toml
[connection]
backend = "lan"
resource = "TCPIP::192.0.2.20::INSTR"

[scope]
driver = "ds1104"
model_hint = "DS1104Z"
default_channel = 1
reset_before_run = false
check_errors = true
```

在 DS1104Z 上，`--points def` 读取当前显示的 NORM 波形；`--points max` 或
`--points dmax` 会停止采集并读取 RAW 存储波形，长记录按 250000 点分块传输。


启动实验性 TUI：

```powershell
python -m pip install -e ".[tui]"
python -m wavebench tui
python -m wavebench tui --config wavebench.toml
python -m wavebench tui --log-file data/tui/wavebench-tui.log
python -m wavebench tui --fake
```

启动 HTTP MCP 只读服务：

```powershell
python -m wavebench mcp serve --config wavebench.toml --token-env WAVEBENCH_MCP_TOKEN
```

调用方访问 `http://127.0.0.1:8765/health` 可做健康检查；访问 `/mcp`、`/tools` 和 `/call` 时需要发送 Bearer token。

只读扫描局域网仪器：

```powershell
python -m wavebench doctor --config wavebench.toml
python -m wavebench doctor --config wavebench.toml --discover-subnet 192.0.2.0/24 --discover-timeout-ms 500
python -m wavebench net discover --subnet 192.0.2.0/24
python -m wavebench net discover --subnet 192.0.2.0/24 --idn-only --timeout-ms 500
```

查看插件注册表：

```powershell
python -m wavebench plugin list
python -m wavebench plugin info rigol.dg4202
python -m wavebench plugin doctor
python -m wavebench plugin list --include-entry-points
python -m wavebench plugin list --load
python -m wavebench plugin info rigol.ds1000z --load
python -m wavebench plugin doctor --load
python -m wavebench plugin package check ../wavebench-instrument-plugins/packages/wavebench-rigol-ds1000z
python -m wavebench plugin install ../wavebench-instrument-plugins/packages/wavebench-rigol-ds1000z --dry-run
python -m wavebench plugin installed
python -m wavebench plugin info rigol.ds1000z --installed
python -m wavebench plugin market search rigol
python -m wavebench plugin market info wavebench-rigol-dg4202
python -m wavebench plugin scpi check doc/project/scpi-plugin.example.toml
python -m wavebench plugin scpi doctor doc/project/scpi-dp800.example.toml --probe --resource TCPIP::192.0.2.12::INSTR
python -m wavebench plugin scpi info doc/project/scpi-plugin.example.toml
python -m wavebench plugin scpi probe doc/project/scpi-dp800.example.toml --resource TCPIP::192.0.2.12::INSTR
```

设置 DG4202 信号源频率：

```powershell
python -m wavebench source set-freq --config wavebench.toml --channel 1 1000
```

执行离散扫频：

```powershell
python -m wavebench sweep discrete --config wavebench.toml --source-channel 1 --scope-channel 1 --frequencies 1000,2000,5000,10000 --target-cycles 10 --frequency-tolerance 0.05 --label dg4202_discrete_sweep --no-csv
```

执行带信号源状态恢复的扫频：

```powershell
python -m wavebench sweep discrete --config wavebench.toml --source-channel 1 --scope-channel 1 --frequencies 1000,5000 --source-func SQU --source-vpp 3.3 --restore-source-state --no-csv
```

读取 DP800 电源状态：

```powershell
python -m wavebench power status --config wavebench.toml
python -m wavebench power protection status --config wavebench.toml
```

设置 DP800 电压/限流但不改变输出状态：

```powershell
python -m wavebench power set --config wavebench.toml --channel 1 --voltage 5.0 --current-limit 0.1
```

显式打开或关闭 DP800 输出；该命令不会修改电压/电流限值：

```powershell
python -m wavebench power output --config wavebench.toml --channel 1 off
python -m wavebench power output --config wavebench.toml --channel 1 on
```

设置 DG4202 波形和方波占空比但不隐式改变输出状态：

```powershell
python -m wavebench source set-func --config wavebench.toml --channel 1 triangle
python -m wavebench source set-duty --config wavebench.toml --channel 1 25
```

只读探测 DG4202 任意波 SCPI 候选，不上传、不改变输出状态：

```powershell
python -m wavebench source arb-probe --config wavebench.toml --channel 1 --probe-timeout-ms 700
```

离线准备任意波 payload，校验 CSV/NPY 波形并可导出归一化 + 14-bit DAC payload：

```powershell
python -m wavebench source arb-load --channel 1 --file waveform.npy --name EXAMPLE_ARB --amplitude 1.0 --offset 0.0 --export-payload data/arb/EXAMPLE_ARB.json --dry-run
```

上传已确认的 DG4202 任意波并显式打开输出：

```powershell
python -m wavebench source arb-load --config wavebench.toml --channel 1 --file waveform.npy --name EXAMPLE_TRI --amplitude 1.0 --frequency 1000 --offset 0.0 --output-on
```

检查 run plan：

```powershell
python -m wavebench run schema
python -m wavebench run template --list
python -m wavebench run template source-scope-sine --output plans/source_scope_sine_1k.toml
python -m wavebench run template source-scope-sine --frequency 10000 --vpp 3.3 --source-channel 2 --scope-channel 1 --output plans/source_scope_sine_10k.toml
python -m wavebench run template source-scope-sweep --frequencies 100,1000,10000 --vpp 1.0 --source-channel 1 --scope-channel 1 --output plans/source_scope_sweep.toml
python -m wavebench run check --plan plans/example_scope_expect_quality.toml
python -m wavebench run check --plan plans/closure_sine_1k.toml
python -m wavebench run check --plan plans/closure_triangle_1k.toml
```

执行 run plan 并生成报告：

```powershell
python -m wavebench run plan --config wavebench.toml --plan plans/example_scope_expect_quality.toml
python -m wavebench run report data/runs/<run_dir>
```

DMM ACV smoke 示例：

```powershell
python -m wavebench run verify --config wavebench.toml --plan plans/example_dmm_acv_source_smoke.toml
python -m wavebench run plan --config wavebench.toml --plan plans/example_dmm_acv_source_smoke.toml
```

DMM `dmm.read` 可用 `[steps.expect]` 对读数做门禁，例如 `value = { min = 0.34, max = 0.37 }`。

公开闭环示例：

```powershell
python -m wavebench run check --config wavebench.toml --plan plans/closure_sine_1k.toml
python -m wavebench run check --config wavebench.toml --plan plans/closure_triangle_1k.toml
```


Run plan 可选择在成功或失败路径恢复信号源状态：

```toml
[restore]
source_state = true
source_channel = 1
```

启用后，WaveBench 会在执行步骤前快照 output/function/frequency/amplitude/方波 duty，并在结束时恢复。

Run plan 中的 `scope.auto` 是显式步骤，对应当前示波器驱动的 autoscale 命令并等待 `*OPC?`；`scope.capture` 不会隐式插入 autoscale：

```toml
[[steps]]
kind = "scope.auto"

[[steps]]
kind = "scope.capture"
channel = 1
label = "after_auto"
```

Run plan 也可以显式组合任意波上传、采集和断言。示例中的输出打开是 `output_on = true` 明确请求，不是默认行为：

```toml
[[steps]]
kind = "source.arb_load"
channel = 1
file = "data/arb/triangle_1024.npy"
frequency_hz = 1000
amplitude_vpp = 1.0
offset_v = 0.0
output_on = true

[[steps]]
kind = "scope.capture"
channel = 1
label = "arb_triangle_1k"
window_frequency_hz = 1000
target_cycles = 10
target_vpp = 1.0
screenshot = true

[steps.expect]
voltage_vpp_v = { min = 0.8, max = 1.2 }
frequency_estimate_hz = { min = 950, max = 1050 }
```

## 开发与测试

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

GitHub Actions 会在 push 和 pull request 时自动运行 Python 3.11 / 3.12 单元测试。
