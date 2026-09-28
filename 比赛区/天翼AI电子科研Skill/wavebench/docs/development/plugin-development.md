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

## 声明基础源恢复接口

自 Core `0.8.27` 起，source 插件可通过 `InstrumentDescriptor.source_restore` 声明 `wavebench.instruments.source_restore.SourceRestoreProfile`。这是独立于 Source V2 波形配置接口的可选合同，不自动替代其它 V2 写入路径。

| 字段 | 约束 |
| --- | --- |
| `supported` | 严格布尔值；是否实现下述基础恢复方法 |
| `operations` | 唯一的 source capability 名称元组，列出能够恢复基础状态的操作；必须已在 descriptor 声明 |
| `fields` | 唯一的 `SourceStatus` 字段名元组；支持时至少包含 output、function、frequency_hz、amplitude、amplitude_unit、square_duty_cycle_percent |
| `excluded_fields` | 未覆盖的状态名称元组，例如 arbitrary_payload；不得与 fields 重叠 |

`supported=True` 必须同时声明 `source.restore_state` capability，并实现公开 Protocol `SourceBasicRestoreDriver` 的两个方法：

```python
def snapshot_basic_state(self, channel: int) -> SourceStatus: ...
def restore_basic_state(self, snapshot: SourceStatus) -> SourceStatus: ...
```

`snapshot_basic_state` 只读取状态，必须在返回前证明该快照可由当前驱动恢复；未知函数、不可读单位、未支持模式或非有限数值应抛出错误。Core 在执行任何实验步骤前收集全部快照，并保存 driver ID 与仪器身份。驱动不得返回 USER／任意波选择后便假定原内容仍可恢复。

`restore_basic_state` 使用传入的原快照，不通过普通 setter 重新要求当前状态可作为基线。驱动须校验目标、身份及会话状态，先关闭输出，逐项恢复并回读覆盖字段；只有参数验证完成后才能按目标恢复输出 ON。返回值必须是实际回读的 `SourceStatus`，Core 还会按声明字段比较；浮点容差为 `rtol=1e-6, atol=1e-6`。数值匹配不替代驱动侧对错误队列及物理模式的检查。

安全门可把恢复目标的 output 改为 OFF，原始快照继续保留。失败必须抛出错误并保留不确定性，禁止盲目重放、在损坏会话继续写入或把 OFF 当作全部恢复成功。未覆盖状态列入产物与报告。`source.restore_snapshot` 和 `source.restore_state` 两个 Core operation 分别使用 stateful_read 与 write 访问策略；后者需要写权限。

明确不支持时设置 `supported=False`，operations／fields 必须为空，不能声明恢复 capability。此时 RunPlan 请求基础恢复会在写入前拒绝。未提供 profile 的旧插件保持原基础恢复路径，但 `source.arb_load` 与基础恢复组合必须有显式支持声明。没有恢复要求的操作可以执行，产物仍记录未覆盖范围。

Core 内置 DG 回退驱动也提供该接口；内置版本随 Core 发布，外置 DG distribution 独立版本。两侧基础恢复逻辑同步时须保留 Core 现有的 NO_REPLAY 和结构化会话错误处理，不能以复制整个厂商文件覆盖这些约束。

采用新接口的插件可将依赖和 `wavebench_min_version` 提升到 `0.8.27`；若需兼容旧 Core，须在公共模块不可用时同时省略新 profile 字段和 capability，保留旧表面，不能只保留方法声明。声明会进入 execution intent 的恢复合同摘要；能力变化后须重新生成 intent。未声明插件不增加这些字段，保持旧摘要。插件作者应覆盖初态拒绝、不同当前波形、ON／OFF 顺序、读回不符、写入不确定、锁停和 Core Service 集成测试；不能以离线通过宣称新入口已经实机验收。

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
