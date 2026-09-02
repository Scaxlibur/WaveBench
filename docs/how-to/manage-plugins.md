# 管理仪器插件

本页适用于需要检查、安装、升级、降级、卸载或恢复本地 WaveBench 仪器插件的维护者。可执行插件是可信 Python 代码，不是安全沙箱；只处理来源、版本和依赖均已确认的本地源码目录或 wheel。

## 前置条件

- 在目标 WaveBench 虚拟环境中运行命令。
- 已确认插件与当前 WaveBench 版本兼容。
- 已确认源码 build backend 或 wheel 的来源可信。

## 检查并安装

```bash
python -m wavebench plugin package check <plugin-path>
python -m wavebench plugin install <plugin-path> --dry-run
python -m wavebench plugin install <plugin-path>
```

源码目录的检查会在临时目录构建 wheel，因此会执行受信任的 build backend。受管安装只使用当前解释器和明确给出的本地路径；它不联网下载依赖，也不修改 `wavebench.toml`。

## 查看已安装插件

```bash
python -m wavebench plugin installed
python -m wavebench plugin list --load
python -m wavebench plugin doctor --load
```

`--load` 会导入可执行插件 descriptor。descriptor 导入不应连接仪器，但导入的 Python 代码仍必须可信。真正的 transport 只在配置选中该 driver 并执行仪器命令时创建。

## 升级、降级、卸载与恢复

```bash
python -m wavebench plugin upgrade <plugin-path> --dry-run
python -m wavebench plugin downgrade <plugin-path>
python -m wavebench plugin remove <driver-id>
python -m wavebench plugin recover
```

在安装、替换或卸载中断后，先运行 `plugin recover`。如果工具无法证明环境处于已知旧态或目标态，应保留环境和诊断信息，进行人工检查；不要通过手工删除记录来掩盖未知状态。

## 边界

- V2 descriptor 只声明已实现和已测试的 capability，不会自动生成 CLI、Service 或 run plan step。
- 具体型号、SCPI、profile、quirk、限制和实机 evidence 由[仪器插件仓库](https://github.com/Scaxlibur/wavebench-instrument-plugins)维护。
- 公共插件模型、V1/V2 路径和声明式 SCPI 的边界见[插件 Reference](../reference/plugins/index.md)。
- 安装插件不等于授权硬件写入；仍需配置 access policy、capability 和安全预检。
