# 无硬件快速开始

本页通过模板生成和离线检查确认 WaveBench 已可运行。不需要仪器或仓库示例文件；检查使用单独的演示配置。

## 1. 确认安装

先按[安装说明](installation.md)准备发行包环境或源码环境。以下 `python` 指该环境的解释器；源码环境也可使用 `uv run --no-sync python`。

```bash
python -m wavebench --version
python -m wavebench --help
```

版本应与所安装的发行包一致；帮助中应列出 `run`、`scope`、`source`、`power`、`dmm`、`doctor`、`plugin` 和 `capability` 等命令域。

## 2. 查看并打印一个模板

```bash
python -m wavebench run template --list
python -m wavebench run template source-scope-sine --print
```

第一条命令应包含 `source-scope-sine`。第二条命令打印包含实验段和 `[[steps]]` 的 TOML，不加载配置或连接仪器。

## 3. 保存模板并离线检查

将以下内容保存为 `offline.toml`。地址只用于离线示例，不能直接用于真实实验：

```toml
[connection]
backend = "lan"
resource = "TCPIP::192.0.2.10::INSTR"

[scope]
driver = "rtm2032"

[source]
driver = "dg4202"
resource = "TCPIP::192.0.2.11::INSTR"

[safety_limits]
max_source_vpp = 1.0
```

在可写目录运行，使用尚不存在的输出文件名：

```bash
python -m wavebench run template source-scope-sine --output example.toml
python -m wavebench run check --plan example.toml --config offline.toml
```

成功时，输出末尾为 `safety_limits=ok / 安全上限=通过`。`run check` 解析 plan，检查 schema、离线安全上限和声明的 capability，不打开 transport 或访问仪器。

## 下一步

- 理解模板如何进入真实实验流程，阅读[从模板到报告](../tutorials/from-template-to-report.md)。
- 连接实验台前阅读[配置实验台](configure-bench.md)和[执行一次实验](../how-to/run-an-experiment.md)。
- 查询字段时运行 `python -m wavebench run schema`，或阅读[run plan Reference](../reference/run-schema.md)。

`run verify` 会查询配置中的仪器，`run plan` 会执行真实实验；两者不属于本页的无硬件流程。
