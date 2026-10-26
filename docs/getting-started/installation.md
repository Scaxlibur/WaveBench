# 安装 WaveBench

本页对应 WaveBench `0.8.28`。发行物及发布状态以 [GitHub Releases](https://github.com/Scaxlibur/WaveBench/releases) 为准；旧版本应使用对应 tag 下的文档。

## 安装发行包

需要 Python 3.11 或更高版本，以及 PATH 中可用的 [uv](https://docs.astral.sh/uv/getting-started/installation/)。从对应 Release 下载 wheel，在源码仓库之外创建专用运行环境：

```bash
uv venv .venv-runtime --python 3.12
uv pip install --python .venv-runtime /path/to/wavebench-0.8.28-py3-none-any.whl
```

将 wheel 路径替换为实际下载位置。Bash 使用：

```bash
.venv-runtime/bin/python -m wavebench --version
.venv-runtime/bin/python -m wavebench --help
```

PowerShell 使用：

```powershell
.venv-runtime\Scripts\python.exe -m wavebench --version
.venv-runtime\Scripts\python.exe -m wavebench --help
```

版本应显示 `wavebench 0.8.28`。后续示例中的 `python` 指这个环境的解释器。运行环境不执行源码项目的 `uv sync`；动态插件的检查、升级和恢复见[管理仪器插件](../how-to/manage-plugins.md)。

## 从固定版本源码安装

需要仓库中的示例计划或修改代码时，使用固定 tag 的新工作副本：

```bash
git clone --branch v0.8.28 https://github.com/Scaxlibur/WaveBench.git
cd WaveBench
uv sync --locked
uv run --no-sync wavebench --version
```

`uv sync --locked` 按 `uv.lock` 准备 `.venv`，并以 editable 方式安装 Core。`uv run --no-sync` 使用已准备的环境，不在运行命令前调整依赖。tag 在正式发布准备完成后可用。

## 使用开发线

跟进未发布修改时，将克隆命令中的 `--branch v0.8.28` 换成 `--branch Dev`。开发线的行为以当前源码、版本查询和文档为准，不作为已发布版本的功能保证。修改包版本后重新同步环境，更新 editable 安装的元数据。

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

发行包安装时，在 wheel 路径后加所需 extras：

```bash
uv pip install --python .venv-runtime "/path/to/wavebench-0.8.28-py3-none-any.whl[analysis,tui]"
```

源码开发环境按实际任务同步，例如：

```bash
uv sync --locked --extra dev --extra analysis --extra tui
```

每次同步应列出所有需要的 extras；默认精确同步会移除未选择的可选组件和锁文件未声明的包。已有仪器插件的环境应采用专用运行方式。

安装后继续[无硬件快速开始](quickstart.md)，确认离线命令可用后再配置实验台。
