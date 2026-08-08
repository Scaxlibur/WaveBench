# WaveBench 仪器插件

> 加载时机：涉及插件发现、驱动能力、打包、安装、升级或插件故障诊断时加载。
> 本文件不依赖其他 reference。

## 发现与能力确认

使用项目环境中的命令：

```bash
.venv/bin/wavebench plugin list --load
.venv/bin/wavebench plugin doctor --load
.venv/bin/wavebench plugin installed
.venv/bin/wavebench plugin info <driver-id> --installed
```

确认：

- canonical driver ID；
- 插件来源和版本；
- 已安装分发包；
- descriptor 是否可加载；
- 实际 capability；
- 当前配置是否选择该驱动。

`plugin list` 不能单独证明外部分发包健康或已生效。`--load` 会导入第三方 Python 代码，只对可信安装内容使用。

能力门拒绝是受支持的 fail-closed 结果。不得为了使命令成功而绕过能力检查或改用未经授权的裸 SCPI。

## 安装边界

只安装用户明确授权的可信本地目录或 wheel：

```bash
.venv/bin/wavebench plugin package check <trusted-local-folder-or-wheel>
.venv/bin/wavebench plugin install <trusted-local-folder-or-wheel> --dry-run
.venv/bin/wavebench plugin install <trusted-local-folder-or-wheel>
.venv/bin/wavebench plugin installed
.venv/bin/wavebench plugin doctor --load
```

- 不安装到系统 Python；
- 不隐式下载 marketplace 包；
- 不为方便而升级共享依赖；
- 不在未审查的来源目录上执行构建；
- 不把 `tavily_hikari` 或任何 MCP 设为插件安装前提。

源目录检查可能执行声明的 build backend；dry-run 不会使不可信源代码变安全。只有预构建 wheel 才能在不执行其构建后端的情况下进行包级检查。

## 安装后验证

安装完成后：

1. 再次运行 `plugin installed`；
2. 用 `plugin info <driver-id> --installed` 确认版本和来源；
3. 运行 `plugin doctor --load`；
4. 读取只读身份和 profile/status；
5. 仅在能力验证通过后，才允许配置切换或实时写入。

插件安装不会自动编辑 `wavebench.toml`。配置切换必须作为独立、可审计的本地修改；不得用 profile 或 example 覆盖现有实验室配置。

## 升级与回滚

升级前记录当前包版本、配置驱动 ID、能力列表和测试结果。升级失败时：

- 不继续进行实时仪器操作；
- 保留安装日志和包文件校验信息；
- 恢复到已验证版本或请求人工处理；
- 重新执行只读身份、能力和 profile 检查。

不要在无法确认插件来源、版本或能力时报告「插件可用」。

## 安全与证据

插件是可执行的第三方 Python 代码。安装或加载前检查来源、构建行为和依赖；不把网络检索结果、临时下载物或私有路径直接变成受信任插件。

交接中记录：

- 执行的插件命令；
- 包来源、版本和校验信息（如可得）；
- doctor 和只读验证结果；
- 配置是否改变；
- 是否接触真实仪器；
- 未解决的能力缺口或安全风险。
