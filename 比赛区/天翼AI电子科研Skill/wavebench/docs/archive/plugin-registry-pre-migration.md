# 归档：重构前的 WaveBench 插件注册表

> [!NOTE]
> 本页保留文档重构前的注册表说明。当前 V1、V2 与声明式 SCPI 边界见[插件 Reference](../reference/plugins/index.md)。

WaveBench 同时保留 metadata V1 与可执行 V2，两者用途不同。

| 路径 | Entry point group | API | 是否进入真实执行路径 |
|---|---|---|---|
| Metadata | `wavebench.drivers` | `wavebench.instrument.v1` | 否 |
| 可执行仪器插件 | `wavebench.instruments` | `wavebench.instrument.v2` | 是，仅在被配置选中后 |
| 声明式 SCPI TOML | 本地文件 | 受限 schema | 否，只允许显式 IDN probe |

## 默认加载行为

- `plugin list/info/doctor` 默认只查看内置 V1 metadata。
- `--include-entry-points` 显式加载第三方 `wavebench.drivers` metadata。
- `plugin list --load` / `plugin doctor --load` 显式加载全部 `wavebench.instruments` descriptor。
- `plugin info <id> --load` 只加载选中的可执行插件。
- 仪器 Service 只在实际打开已配置 driver 时加载对应 V2 entry point；未选中的坏插件不会被导入。

## V2 注册与解析

内置 driver 和第三方 driver 统一由 V2 registry/factory 创建。配置可使用 canonical ID；现有短名保持兼容：

```text
rtm2032  -> rohde-schwarz.rtm2032
ds1104   -> rigol.ds1104
ds1000z  -> rigol.ds1104
dg4202   -> rigol.dg4202
dp800    -> rigol.dp800
dm3000   -> rigol.dm3000
dm3058   -> rigol.dm3000
```

第三方插件 canonical ID 只通过同名 entry point 发现。加载后会检查：

- API 版本与 WaveBench 兼容范围；
- expected kind；
- canonical ID / alias 全局冲突；
- 外部包是否尝试覆盖内置 ID 或 alias；
- 插件 options 类型、范围和未知字段；
- factory 返回对象是否满足对应 contract。

失败只禁用对应插件。配置显式选择失败插件时会得到带 driver/kind 的错误；其他内置 driver 与非仪器 CLI 仍可使用。

## 命令

```bash
# V1 metadata
python -m wavebench plugin list
python -m wavebench plugin info rigol.dp800
python -m wavebench plugin doctor
python -m wavebench plugin doctor --include-entry-points

# V2 executable descriptors
python -m wavebench plugin list --load
python -m wavebench plugin info rtm2032 --load
python -m wavebench plugin doctor --load
```

`plugin doctor` 遇到 error 返回退出码 `2`。`plugin ... --load` 会导入第三方 Python 代码，应只对可信环境使用。

## 核心边界

V2 插件可以实现厂商 SCPI 差异，但不能替换核心 Service、安全限制、run plan 或 artifact writer。核心固定 resource、timeout、日志和 transport factory 后才创建 `DriverContext`。

插件不是沙箱。WaveBench 不自动安装插件，不根据 marketplace 数据下载包，不允许声明式 SCPI TOML 变成任意命令执行器，也不自动为每个插件创建独立进程。

插件作者细节见[插件开发](../development/plugin-development.md)，安装与卸载见[管理仪器插件](../how-to/manage-plugins.md)。
