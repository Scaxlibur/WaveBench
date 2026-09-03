# 归档：重构前的 WaveBench 声明式 SCPI 插件

> [!NOTE]
> 本页保留文档重构前的声明式 SCPI 字段表。当前用途和安全边界见[插件 Reference](../reference/plugins/index.md)；字段以 parser 和 validator 为准。

本文档记录 WaveBench 当前本地声明式 SCPI 插件 TOML 的格式、命令和安全边界。

当前声明式 SCPI 插件用于 metadata 校验、展示和显式只读 IDN probe。它不会把插件注册到真实执行路径。

## 命令

检查本地 TOML：

```bash
python -m wavebench plugin scpi check docs/project/reference/plugins/scpi-plugin.example.toml
```

诊断本地 TOML，并可选执行只读 IDN probe：

```bash
python -m wavebench plugin scpi doctor docs/project/reference/plugins/scpi-dp800.example.toml
python -m wavebench plugin scpi doctor docs/project/reference/plugins/scpi-dp800.example.toml --probe --resource TCPIP::192.0.2.12::INSTR
```

查看本地 TOML：

```bash
python -m wavebench plugin scpi info docs/project/reference/plugins/scpi-plugin.example.toml
```

对一个显式资源执行只读 IDN 查询：

```bash
python -m wavebench plugin scpi probe docs/project/reference/plugins/scpi-dp800.example.toml --resource TCPIP::192.0.2.12::INSTR
```

## 示例

示例文件位于：

```text
docs/project/reference/plugins/scpi-plugin.example.toml
docs/project/reference/plugins/scpi-dp800.example.toml
```

内容：

```toml
driver_id = "example.scope"
kind = "scope"
display_name = "Example Scope"
manufacturer = "Example"
models = ["EX1"]
capabilities = ["scope.idn"]
summary = "Example local declarative SCPI plugin metadata."
idn_patterns = ["EXAMPLE,EX1"]
config_fields = ["resource"]

[scpi]
idn_query = "*IDN?"
```

## 字段

必填字段：

```text
driver_id
kind
display_name
manufacturer
models
capabilities
summary
```

可选字段：

```text
api_version
package
idn_patterns
config_fields
scpi.idn_query
```

默认值：

```text
api_version = "wavebench.instrument.v1"
package = "local"
scpi.idn_query = "*IDN?"
```

## 校验规则

`plugin scpi check` 会复用插件注册表 metadata 校验：

- `api_version` 必须是当前支持版本；
- `kind` 必须是 `scope`、`source`、`power` 或 `dmm`；
- capability 必须是点分小写标识符；
- capability 必须以插件 `kind` 作为前缀。

同时会检查 `scpi.idn_query`：

- 必须是单行命令；
- 不能包含 `;` 分隔符；
- 必须以 `?` 结尾。

## 安全边界

`plugin scpi check`、`plugin scpi doctor` 和 `plugin scpi info` 默认只读 TOML：

- 不发送 SCPI；
- 不写配置；
- 不导入 Python 包；
- 不把本地 TOML 注册成可执行 driver；
- 不改变 service 层真实仪器控制路径。

`plugin scpi doctor --probe --resource ...` 和 `plugin scpi probe` 会连接一个显式资源，只会发送 TOML 中的 `scpi.idn_query`：

- `scpi.idn_query` 必须是单行查询；
- `scpi.idn_query` 不能包含 `;` 分隔符；
- `scpi.idn_query` 必须以 `?` 结尾；
- 不支持任意 SCPI；
- 不打开或关闭输出；
- 不写仪器状态；
- 不修改配置文件。
