# 归档：重构前的 WaveBench 插件市场索引

> [!NOTE]
> 本页保留文档重构前的本地市场索引字段表。当前用途和安全边界见[插件 Reference](../reference/plugins/index.md)；JSON 字段以 parser 为准。

本文档记录 WaveBench 当前只读插件市场索引的设计边界、JSON 格式和命令形态。

当前 marketplace 只是一份本地 JSON index，用于展示「有哪些插件条目」。它不下载、不安装、不导入第三方包，也不改变现有仪器控制路径。

## 目标

当前阶段目标：

- 从本地 JSON 文件读取插件市场条目；
- 支持按文本搜索插件条目；
- 支持查看单个插件条目详情；
- 明确当前本地只读索引的数据结构和安全边界。

## 非目标

当前阶段不做：

- 不联网拉取索引；
- 不安装插件；
- 不导入 index 中声明的 Python 包；
- 不解析包依赖；
- 不做签名、校验和或信任链；
- 不把 market 条目自动注册为可用 driver。

## 命令

搜索默认示例索引：

```bash
python -m wavebench plugin market search rigol
```

查看默认示例索引中的条目：

```bash
python -m wavebench plugin market info wavebench-rigol-dg4202
```

显式指定本地索引文件：

```bash
python -m wavebench plugin market search rigol --index src/wavebench/plugins/market.example.json
python -m wavebench plugin market info wavebench-rs-rtm2032 --index src/wavebench/plugins/market.example.json
```

## JSON 格式

当前示例索引位于：

```text
src/wavebench/plugins/market.example.json
```

格式：

```json
{
  "schema_version": 1,
  "plugins": [
    {
      "plugin_id": "wavebench-rigol-dg4202",
      "driver_id": "rigol.dg4202",
      "name": "RIGOL DG4202 driver",
      "package": "wavebench",
      "version": "0.7.0",
      "kind": "source",
      "summary": "Built-in metadata entry for the WaveBench RIGOL DG4000-series signal source driver.",
      "homepage": "https://github.com/Scaxlibur/wavebench",
      "capabilities": ["source.idn", "source.set_frequency"],
      "tags": ["builtin", "rigol", "source"]
    }
  ]
}
```

必填字段：

```text
plugin_id
driver_id
name
package
version
kind
summary
```

可选字段：

```text
homepage
capabilities
tags
```

`plugin_id` 必须在一个 index 内唯一。当前实现会拒绝重复 `plugin_id`。

## 搜索规则

`plugin market search [query]` 会在以下字段中做大小写不敏感的包含匹配：

```text
plugin_id
driver_id
name
package
kind
summary
capabilities
tags
```

未传 query 时，会列出 index 中所有条目。

## 安全边界

market index 是只读目录，不是安装源。

当前实现只做 JSON 读取和字段校验：

- 不执行 index 中的任何代码；
- 不导入 `package` 字段对应的 Python 包；
- 不访问 `homepage`；
- 不下载 artifact；
- 不修改 `wavebench.toml`；
- 不把条目写入本地插件注册表。
