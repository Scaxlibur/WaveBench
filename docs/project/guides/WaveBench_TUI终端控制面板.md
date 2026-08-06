# WaveBench TUI 终端控制面板

WaveBench TUI 是可选的实验性终端控制面板，用于在实验台前查看和操作常用仪器。它不是图形 GUI，也不负责 LabVIEW 式的流程编排。

TUI 通过 `wavebench[tui]` optional extra 提供。源码环境先安装界面依赖：

```text
python -m pip install -e ".[tui]"
```

产品范围冻结为现有的电源、万用表和信号源三个面板。新仪器能力默认先进入 CLI、Service 和 run plan；TUI 不扩展为 run-plan 编辑器、插件管理器、完整示波器波形查看器或报告系统。

## 当前范围

```text
python -m wavebench tui
python -m wavebench tui --config wavebench.toml
python -m wavebench tui --log-file data/tui/wavebench-tui.log
python -m wavebench tui --fake
```

`wavebench tui` 默认读取当前目录的 `wavebench.toml`。从其他目录启动时，使用 `--config <toml>` 指定配置文件。

当前 TUI 覆盖：

- DP800 / DP832A 电源面板
  - 显示 CH1/CH2/CH3 的输出状态、CV/CC 模式、设定电压、限流、实测电压、电流和功率
  - 显示 OVP/OCP 状态、阈值和触发状态
  - 支持设置电压与电流限值，并用 `设定 / Set` 应用
  - 支持 CH1/CH2/CH3 独立 `开关 / Toggle`
  - 支持设置 OVP/OCP 阈值和开关状态
- DM3000 / DM3058 万用表读数面板
  - 显示连接状态、当前功能、读数、单位和 raw reading
  - 支持用按钮切换常用挡位：DCV、ACV、DCI、ACI、二线电阻、四线电阻、频率、周期、通断、二极管、电容
  - 支持 `读取 / Read` 手动刷新当前挡位读数
- DG4202 信号源面板
  - 显示输出状态、波形、频率、幅度和偏置
  - 支持手动刷新
  - 支持设置波形、频率和 Vpp
  - 支持切换输出
- `--fake` 模式
  - 使用模拟电源、模拟万用表和模拟信号源，不连接真实仪器
- `--log-file <path>`
  - 指定 TUI 调试日志文件
  - 默认路径为 `data/tui/wavebench-tui.log`
  - 日志行数限制由 `[tui].log_max_lines` 和 `[tui].log_keep_lines_after_trim` 配置

## 常用启动方式

默认刷新间隔为 5 秒：

```text
python -m wavebench tui --refresh-interval 5
```

如果已经在项目根目录或 `wavebench.toml` 所在目录：

```text
python -m wavebench tui
```

如果从其他目录启动：

```text
python -m wavebench tui --config /path/to/wavebench.toml
```

只检查界面，不连接真实仪器：

```text
python -m wavebench tui --fake
```

把调试日志写到指定位置：

```text
python -m wavebench tui --log-file data/tui/wavebench-tui.log
```

## 安全约束

电源控制保持显式分离：

```text
power set      只修改电压/电流限值，不改变 output
power output   只开关输出，不改变电压/电流限值
```

TUI 沿用同一规则。开关输出前仍会经过 service 层已有的安全上限检查。
