# 插件 Reference

WaveBench 支持三条彼此独立的插件路径。它们的共同边界是：Core 拥有 resource、transport、Service、安全、run plan、artifact 和 session/recovery；插件实现厂商协议、命令、解析、descriptor 和私有测试。

| 路径 | 入口 | 是否进入真实执行 |
| --- | --- | --- |
| V1 metadata | `wavebench.drivers` / `wavebench.instrument.v1` | 否。用于展示 metadata。 |
| V2 executable plugin | `wavebench.instruments` / `wavebench.instrument.v2` | 仅在配置选中 driver 后。 |
| 声明式 SCPI TOML | 本地 TOML | 否；只允许显式只读 IDN probe。 |

## 加载与信任边界

默认插件命令优先查看内建 metadata。`plugin ... --load` 会导入第三方 V2 descriptor；只在可信环境中使用。Service 仅在实际打开已配置的 driver 时加载对应插件，未选中的坏插件不应阻断其它内建路径。

V2 entry point 名必须与 canonical `driver_id` 一致。可执行插件不能通过 alias 覆盖内建 driver；具体分发包、型号支持和 capability 以 descriptor 与插件仓库为准。

## 声明式 SCPI 与本地市场索引

`plugin scpi check`、`plugin scpi doctor` 和 `plugin scpi info` 默认只读取 TOML。只有显式 `--probe --resource` 或 `plugin scpi probe` 才会发送配置的单行 IDN 查询；该路径不提供任意 SCPI 或写入。

`plugin market` 读取本地 JSON 索引，用于搜索和查看条目。它不下载、安装、导入或执行索引中声明的包。

字段、默认值和 JSON／TOML 校验规则由 parser 与 validator 定义。不要把本页的概览当作完整 schema。

## 相关页面

- [管理仪器插件](../../how-to/manage-plugins.md)
- [插件开发](../../development/plugin-development.md)
- [新增仪器驱动](../../development/instrument-drivers.md)
- [仪器插件仓库](https://github.com/Scaxlibur/wavebench-instrument-plugins)
