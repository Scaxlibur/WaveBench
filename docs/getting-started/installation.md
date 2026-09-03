# 安装 WaveBench

本页用于在本地工作副本中安装 WaveBench，并确认 CLI 已可运行。完成后会得到一个独立虚拟环境和可验证的 `--help` 输出。

## 前置条件

- Python 3.11 或更高版本。
- 已检出的 WaveBench 源码。

## 安装基础包

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m wavebench --help
```

PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m wavebench --help
```

最后一条命令列出 `run`、`scope`、`source`、`power`、`dmm`、`doctor`、`plugin` 等一级命令时，基础安装完成。

## 可选组件

| Extra | 用途 |
| --- | --- |
| `.[tui]` | 实验性终端控制面板。 |
| `.[analysis]` | 频响分析与校准所需的分析依赖。 |
| `.[pdf]` | 离线报告的 PDF 输出。 |
| `.[report3d]` | 报告中的交互式三维图。 |
| `.[dev]` | 本地测试和静态检查。 |

按实际任务安装，例如：

```bash
.venv/bin/python -m pip install -e ".[tui]"
```

可选组件不连接仪器。安装后先继续[无硬件快速开始](quickstart.md)，不要直接执行硬件写入命令。
