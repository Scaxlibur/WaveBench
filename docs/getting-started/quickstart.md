# 无硬件快速开始

本页用于在未连接任何仪器时确认 WaveBench 已安装，并验证一个现有 plan 可以通过离线检查。完成后可以看到模板列表、示例 TOML 和 `run check` 的明确成功结果。

## 前置条件

- Python 3.11 或更高版本，以及 [uv](https://docs.astral.sh/uv/getting-started/installation/)。
- 一个新的 WaveBench 工作副本。
- 不需要 `wavebench.toml`，也不需要连接仪器。

## 1. 创建虚拟环境并安装

在新的仓库工作副本中运行，Bash 与 PowerShell 使用相同命令：

```bash
uv sync --locked
```

依赖版本来自 `uv.lock`，项目以 editable 方式安装到 `.venv`。
已有仪器插件的环境不要执行默认同步，因为未声明的包会被移除；实际仪器使用采用
[专用运行环境](../how-to/manage-plugins.md)。

## 2. 检查命令入口

```bash
uv run --no-sync wavebench --help
```

输出会列出 `run`、`scope`、`source`、`power`、`dmm`、`doctor`、`plugin` 和 `capability` 等命令域。`--no-sync` 使用已经准备好的环境，不在执行前更改依赖。

## 3. 查看并打印一个模板

```bash
uv run --no-sync wavebench run template --list
uv run --no-sync wavebench run template source-scope-sine --print
```

第一条命令应包含 `source-scope-sine`。第二条命令会把 TOML 打印到标准输出，其中包含实验段和 `[[steps]]`；它不会写文件、加载配置或连接仪器。

## 4. 离线检查现有示例

```bash
uv run --no-sync wavebench run check --plan plans/closure_sine_1k.toml
```

成功时，输出末尾为 `safety_limits=ok / 安全上限=通过`。`run check` 会解析 plan、检查 schema、离线安全上限和声明的 capability；它不会打开 transport 或访问仪器。

## 下一步

- 想理解模板如何进入真实实验流程，阅读[从模板到报告](../tutorials/from-template-to-report.md)。
- 已准备连接实验台时，先阅读[配置实验台](configure-bench.md)和[执行一次实验](../how-to/run-an-experiment.md)。
- 需要查询字段时，运行 `python -m wavebench run schema`，并阅读[run plan Reference](../reference/run-schema.md)。

`run verify` 会对配置中的仪器执行只读身份查询，`run plan` 会执行真实实验；两者不属于本页的无硬件流程。
