# 插件开发

本页适用于开发外置 V2 可执行仪器插件。目标是以一个 canonical ID、一个 `wavebench.instruments` entry point 和受测试的 descriptor／factory 接入 WaveBench，而不把厂商差异泄漏到 Core。

## 职责边界

插件负责厂商协议、命令、响应解析、descriptor capability、写后读取、错误队列、`close()` 和 fake tests。Core 负责 resource、timeout、transport、日志、Service、CLI、run plan、artifact、安全、session/recovery 和受管安装。

descriptor 导入不得进行仪器 I/O、端口扫描、文件写入或全局状态修改。factory 使用 Core 提供的 context 打开 transport；driver 不读取 CLI 参数、不直接写 run artifact，也不隐式 reset、autoscale、trigger 或开启输出。

## 最小流程

1. 冻结 canonical `driver_id`、kind、支持型号和只读 IDN 样本。
2. 实现 descriptor、factory、`idn()`、`close()` 和一个最小只读 capability。
3. 用 fake transport 覆盖命令、解析、timeout、错误队列和 close。
4. 每新增一个写 capability，都补充前置条件、写后 readback、失败语义和离线测试。
5. 构建 wheel，执行包检查、临时 venv 安装／加载／卸载验证，再单独申请实机验收。

## 验证

```bash
python -m wavebench plugin package check <plugin-path>
python -m wavebench plugin install <plugin-path> --dry-run
python -m wavebench plugin doctor --load
```

API 签名、capability 映射、字段和 validator 以公共源码为准。不要手工复制整张 API 表，也不要把开发线实现或单一型号 profile 写成 Current 用户文档。

## 相关页面

- [插件 Reference](../reference/plugins/index.md)
- [管理仪器插件](../how-to/manage-plugins.md)
- [新增仪器驱动](instrument-drivers.md)
