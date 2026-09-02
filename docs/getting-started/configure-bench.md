# 配置实验台

本页用于从示例配置建立本地实验台配置，并在真实写入前完成只读诊断和 run plan 预检。配置文件可能包含真实资源或本地路径，不应提交到 Git。

> [!WARNING]
> `doctor` 和 `run verify` 会查询真实仪器。执行前确认资源、接线、输入阻抗、输出状态和安全限制。

## 1. 创建本地配置

仓库根目录的 `wavebench.example.toml` 是受跟踪示例。仅在尚未有本地配置时复制：

```bash
cp -n wavebench.example.toml wavebench.toml
```

PowerShell：

```powershell
if (Test-Path wavebench.toml) { throw "wavebench.toml already exists" }
Copy-Item wavebench.example.toml wavebench.toml
```

`wavebench.toml` 缺失时，依赖配置的命令会报错；不会回退到隐式默认实验台配置。

## 2. 填写最小必需表

`[connection]` 和 `[scope]` 是配置 loader 的必需表。先填写资源、backend、scope driver 和 access policy；只有 plan 使用某种仪器时，才配置 `source`、`rf_source`、`power` 或 `dmm` 表。

```toml
[connection]
backend = "lan"
resource = "TCPIP::192.0.2.10::INSTR"

[scope]
driver = "rtm2032"
access = "read_only"
```

示例地址是文档保留地址，必须替换为本地实验台资源。完整字段、访问模式和安全限制见[配置 Reference](../reference/configuration.md)。

## 3. 进行只读诊断

```bash
python -m wavebench doctor --config wavebench.toml
python -m wavebench run verify --config wavebench.toml --plan plans/<plan>.toml
```

`doctor` 检查配置中的仪器身份和资源可达性；`run verify` 只读预检一个特定 plan 所需的仪器和安全前提。两者失败时先修正配置、型号选择或接线，不要跳过预检。

## 下一步

- 需要查询字段、默认值或安全边界时，阅读[配置 Reference](../reference/configuration.md)。
- 已准备好 plan 时，按[执行一次实验](../how-to/run-an-experiment.md)继续。
- 需要了解具体型号支持、SCPI 或私有限制时，查阅[仪器插件仓库](https://github.com/Scaxlibur/wavebench-instrument-plugins)。
