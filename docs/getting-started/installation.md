# 安装 WaveBench

本页用于安装 `Dev` 开发线，并确认 CLI 已可运行。该开发线的 uv 安装与插件管理后端尚未发布；使用已发布版本时，应查阅[对应 tag 的安装说明](https://github.com/Scaxlibur/WaveBench/blob/v0.8.27/docs/getting-started/installation.md)。完成后会得到一个独立虚拟环境和可验证的 `--help` 输出。

## 前置条件

- Python 3.11 或更高版本。
- PATH 中可用的 [uv](https://docs.astral.sh/uv/getting-started/installation/)。
- 一个新的 `Dev` 工作副本；已有仪器插件的环境按[管理仪器插件](../how-to/manage-plugins.md)处理。

## 安装基础包

以下命令适用于 Bash 和 PowerShell：

```bash
git clone --branch Dev https://github.com/Scaxlibur/WaveBench.git
cd WaveBench
uv sync --locked
uv run --no-sync wavebench --help
```

最后一条命令列出 `run`、`scope`、`source`、`power`、`dmm`、`doctor`、`plugin` 等一级命令时，基础安装完成。

`uv sync --locked` 按 `uv.lock` 准备 `.venv`，并以 editable 方式安装 Core。`uv run --no-sync` 使用已准备的环境，不在运行命令前调整依赖。

uv 不读取 `pip.conf` 或 `PIP_INDEX_URL`。使用镜像或私有软件源时，先按 [uv 软件源配置](https://docs.astral.sh/uv/concepts/indexes/)设置索引，再运行安装命令。

## 可选组件

| Extra | 用途 |
| --- | --- |
| `tui` | 实验性终端控制面板。 |
| `analysis` | 频响分析与校准所需的分析依赖。 |
| `pdf` | 离线报告的 PDF 输出。 |
| `report3d` | 报告中的交互式三维图。 |
| `dev` | 本地测试和静态检查。 |
| `docs` | 文档站点预览与构建。 |

按实际任务安装，例如：

```bash
uv sync --locked --extra dev --extra analysis --extra tui
```

每次同步应列出所有需要的 extras；默认精确同步会移除未选择的可选组件和锁文件未声明的包。运行环境与开发环境的区别见[管理仪器插件](../how-to/manage-plugins.md)。

可选组件不连接仪器。安装后先继续[无硬件快速开始](quickstart.md)，不要直接执行硬件写入命令。
