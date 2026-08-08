# WaveBench 可安装仪器插件用户指南

可执行仪器插件是普通 Python wheel，通过 `wavebench.instruments` entry point 接入。它是可信代码扩展，不是安全沙箱；只安装来源和版本都可信的包。

> [!IMPORTANT]
> 本指南描述 `v0.8.0` 发布的 Instrument API V2 与受管插件生命周期。`v0.7.0` 只有 V1 只读 metadata、声明式 SCPI 检查和本地只读市场索引，不包含 `wavebench.instruments`、`plugin package/install/upgrade/remove/recover` 或覆盖槽位。配套外置插件要求 `wavebench>=0.8,<0.9`，不能与 `v0.7.0` 配套运行。

## 推荐环境

每套实验环境使用独立 venv，并同时固定 WaveBench 与插件版本：

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

原生 Windows 使用 PowerShell 时，对应命令为：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

上例从 `v0.8.0` 源码树建立开发环境。本指南中的 V2 插件命令不兼容 `wavebench==0.7.0`。

WaveBench 的受管插件命令只允许在当前 venv 中运行，拒绝系统 Python。它只接受用户明确指定的本地源码目录或 wheel，不联网、不自动安装依赖，也不修改 `wavebench.toml`。
当前原生 Windows 生命周期支持纯 Python wheel；包含原生 DLL 的 wheel 需要单独验证，不在本支持声明内。

从 WaveBench 仓库根目录先做包检查和安装 dry-run：

```bash
.venv/bin/python -m wavebench plugin package check \
  ../wavebench-instrument-plugins/packages/wavebench-rigol-ds1000z
.venv/bin/python -m wavebench plugin install \
  ../wavebench-instrument-plugins/packages/wavebench-rigol-ds1000z --dry-run
.venv/bin/python -m wavebench plugin install \
  ../wavebench-instrument-plugins/packages/wavebench-rigol-ds1000z
```

源码目录会先在临时目录中离线构建 wheel。即使是 `package check` 或 `--dry-run`，源码包的受信任 build backend 也会执行；若不执行构建代码，应只检查已经取得并核验来源的 wheel。

安装器会校验 wheel 的 Python/WaveBench 兼容范围、`METADATA`、`WHEEL`、`RECORD`、SHA-256、成员路径和唯一 `wavebench.instruments` entry point。安装固定使用当前解释器的离线 `pip --no-deps --no-index`，因此插件声明的额外依赖必须预先存在于该 venv，缺失时安装后检查会失败并触发回滚。

## 查看与诊断

```bash
.venv/bin/python -m wavebench plugin installed
.venv/bin/python -m wavebench plugin info rigol.ds1000z --installed
.venv/bin/python -m wavebench plugin list --load
.venv/bin/python -m wavebench plugin info rigol.ds1000z --load
.venv/bin/python -m wavebench plugin doctor --load
```

`plugin installed` 和 `info --installed` 会交叉核对受管账本、已安装 distribution、entry point、`RECORD` 与文件摘要，并报告 `healthy`、`missing`、`drifted`、`broken` 或 `unmanaged`。普通 `plugin info` 仍查看 metadata；只有 `--load` 才导入 V2 descriptor。descriptor 导入本身不应连接仪器；真正的 transport 在配置选中该 driver 并执行仪器命令时才创建。

当前 V2 外置插件只接受 entry point 对应的 canonical ID，不解析插件自定义 alias。WaveBench 主包长期预装 RTM2000、DS1000Z、DG4000、DP800 和 DM3000 五个仪器族；外置包是独立发布的可选升级或扩展，不代表内置驱动待移除。除核心显式列出的可选覆盖槽位外，外置插件不能覆盖内置 canonical ID 或 alias。源码沿用历史名称 `migration slot`，这里只表示受限覆盖白名单。

可选覆盖槽位按 canonical ID 与 distribution 双重白名单控制：`wavebench-rigol-dg4000` / `rigol.dg4202`、`wavebench-rigol-dm3000` / `rigol.dm3000`、`wavebench-rigol-dp800` / `rigol.dp800`、`wavebench-rohde-schwarz-rtm2000` / `rohde-schwarz.rtm2032`。安装受支持的包后，显式 canonical ID 选择外置实现；短 alias `dg4202`、`dm3000` / `dm3058`、`dp800`、`rtm2032` 始终选择内置基线。卸载外置包后，共享的 canonical ID 自动回退到内置实现。DM3000 外置 descriptor 的 `resource_schemes=("tcpip",)` 还会在 transport 打开前拒绝 `ASRL`、`USB` 和 `GPIB` VISA resource；内置短 alias 继续提供 serial + PyVISA 兼容路径。

DS1000Z 外置包使用独立 canonical ID `rigol.ds1000z`，不覆盖内置 canonical `rigol.ds1104`；短 alias `ds1104` 与 `ds1000z` 始终选择内置基线。其他同名覆盖请求仍会被安装器拒绝。

## 配置

安装 DS1000Z 外置包后，用 canonical ID 显式选择外部实现：

```toml
[connection]
backend = "lan"
resource = "TCPIP::192.0.2.20::INSTR"

[scope]
driver = "rigol.ds1000z"
model_hint = "DS1104Z Plus"
default_channel = 1
check_errors = true

[scope.options]
max_chunk_points = 250000
```

短 alias `ds1104` 与 `ds1000z` 继续选择 WaveBench 内置基线，不会被外部包覆盖。这样卸载插件后旧配置仍可工作；要验证外部 wheel，必须使用 canonical `rigol.ds1000z`。

RTM2000 外置插件 0.2+ 在 `backend = "lan"` 时默认使用 RsInstrument SocketIO，避开
VXI-11 的接收分块限制；RIGOL 插件的 `lan` 仍解析为 PyVISA。需要诊断兼容路径时，可把
全局 `[connection].backend` 显式设为 `rsinstrument`、`rsinstrument-rsvisa` 或
`rsinstrument-pyvisa-py`，并重新打开会话。后端切换不会在波形读取失败后自动发生，避免
对部分消费的仪器响应做不安全重放。RTM 的 `MAX` / `DMAX` 传输上限可单独配置：

```toml
[scope.options]
long_waveform_timeout_ms = 300000
```

该值不改变普通 SCPI、`DEF` 或 `*OPC?` 的 timeout。

为保持现有配置可用，SocketIO 路径会把简单的 `TCPIP::<host>::INSTR` 规范化为
`TCPIP::<host>::5025::SOCKET`。非默认端口应显式写成
`TCPIP::<host>::<port>::SOCKET`；包含 `inst0` 等设备名的复杂 VXI-11 resource 不会被猜测
转换，需要显式选择 VISA 后端。

SocketIO 会话可为普通 binary block 写入追加 SCPI 终止符，并限制发送分块；这些选项不施加到
VXI-11、RsVisa 或 pyvisa-py 会话。RTM2032 0.2.0 实机验收确认 SocketIO 适合波形数据路径，
但 `SYST:SET <488.2 block>` setup 恢复不能依赖该路径：一次 SocketIO 写入曾只部分生效。
可逆验收工具必须把 setup 写视为一次性事务，使用已验证的 VXI-11 512-byte 协议分片，写后
重连并只读核对完整 setup blob、配置指纹与 acquisition 状态；未知状态下不得自动重复写。

## 升级与卸载

升级与降级都要求提供明确的本地源码目录或 wheel，并按目标版本相对当前受管版本的方向进行校验：

```bash
.venv/bin/python -m wavebench plugin upgrade ./wavebench_rigol_ds1000z-0.2.0-py3-none-any.whl --dry-run
.venv/bin/python -m wavebench plugin upgrade ./wavebench_rigol_ds1000z-0.2.0-py3-none-any.whl
.venv/bin/python -m wavebench plugin downgrade ./wavebench_rigol_ds1000z-0.1.0-py3-none-any.whl
.venv/bin/python -m wavebench plugin remove rigol.ds1000z --dry-run
.venv/bin/python -m wavebench plugin remove rigol.ds1000z
```

安装、替换和卸载都使用环境锁、原子账本和写前 transaction journal。升级/降级失败时会使用缓存的旧 wheel 做 best-effort 回滚；没有已校验旧 wheel、插件文件已漂移或 distribution 不受 WaveBench 管理时，操作会拒绝继续。若进程或主机在 pip 修改环境期间中断，先运行：

```bash
.venv/bin/python -m wavebench plugin recover
```

只有能证明环境处于精确旧态或精确目标态时才会自动恢复；状态不唯一时会停止并要求人工检查。不要通过手工删除账本或 journal 来掩盖未知状态。

PowerShell 中将上例的 `.venv/bin/python` 替换为 `.\.venv\Scripts\python.exe`。插件环境锁只保证
同一原生 Windows 运行环境内的 WaveBench 进程；不要让原生 Windows 与 WSL 同时管理同一 venv。

卸载 DG4000、DM3000、DP800 或 RTM2000 的外置包后，共享的 canonical ID 会自动恢复为内置实现。DS1000Z 的 `rigol.ds1000z` 是独立插件 canonical ID，卸载后会提示该 canonical driver 未安装；可重新安装固定版本，或把配置改为内置 alias `ds1104` / `ds1000z`。内置兼容短名包括 `dg4202`、`dm3000` / `dm3058`、`dp800` 和 `rtm2032`，安装外置包不会改变其解析结果；其中只有 DM3000 内置 alias 保留 serial backend。

## 常见问题

- `kind` 不匹配：检查 driver 是否写在正确的 `[scope]` / `[source]` / `[power]` / `[dmm]` 段。
- 版本不兼容：安装 descriptor 声明范围内的 WaveBench 版本，或升级插件。
- alias / ID：外置 V2 插件只能声明 canonical ID，不能声明或覆盖 alias；内置兼容 alias 不受影响。
- import/factory 失败：运行 `plugin doctor --load` 查看具体包；坏插件不会阻止其他内置 driver。
- `drifted` / `broken`：不要直接执行升级或卸载；先检查 distribution、entry point 和安装文件，再按提示恢复。
- `recovery required`：运行 `plugin recover`；若仍要求人工检查，保留 venv 中的 `.wavebench` 事务证据。
- vendor SDK 依赖互斥：第一阶段不做每插件进程隔离；为不同依赖组合建立独立 venv。
- marketplace：当前只读展示索引，不会下载或自动安装任何包。
