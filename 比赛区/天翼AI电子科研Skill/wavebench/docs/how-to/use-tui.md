# 使用 TUI

WaveBench TUI 是实验性的终端控制面板，适合在实验台前查看和进行受限的电源、万用表和信号源操作。它不是 run plan 编辑器、插件管理器、完整示波器查看器或报告系统。

## 安装

```bash
python -m pip install -e ".[tui]"
```

## 启动

```bash
python -m wavebench tui --config wavebench.toml
python -m wavebench tui --refresh-interval 5
python -m wavebench tui --log-file data/tui/wavebench-tui.log
```

刷新间隔必须大于零。非 fake 模式会读取并可能修改配置的仪器状态；它遵循与 CLI 相同的 access policy 和安全限制。

## 无硬件预览

```bash
python -m wavebench tui --fake
```

`--fake` 使用模拟适配器，不读取 `wavebench.toml`，也不连接实验台。交互 TUI 默认可能写本地调试日志；它不是零文件副作用的 CLI smoke test。

## 安全边界

电源和信号源的设定与输出仍是显式的独立操作。TUI 不会因为修改电压、频率或幅度而隐式开启输出。需要可复查的多仪器流程时，使用[执行一次实验](run-an-experiment.md)和 run plan。
