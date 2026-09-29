---
name: tianyi-electronics-research
description: >-
  面向电子科研的 TeleAgent Skill：通过局域网/VISA 发现示波器、信号源、电源和万用表，
  读取 SCPI 设备信息与波形，计算峰值、峰峰值、频率、均方根等指标，并生成可追溯的 JSON 结果。
license: MIT
compatibility: Python 3.11+；Windows/Linux；真实设备操作需要用户确认网口、接线和写入权限。
metadata:
  version: 1.0.0
  project: 天翼AI杯-重庆邮电大学赛区
---

# 天翼 AI 电子科研赋能 Skill

这个 Skill 将 WaveBench 的设备抽象、SCPI/VISA 通信和信号分析能力封装成 TeleAgent 可调用的本地工具。

## 可调用能力

- `discover`：扫描指定网段的常见仪器端口（默认 5025/5555/4000/502），尝试 `*IDN?`，输出 IP、端口和设备标识。
- `inspect`：只读查询设备 ID、错误队列和示波器通道摘要。
- `measure`：读取常见示波器测量值（频率、Vpp、Vmax、Vmin、Vavg、Vrms），也可以获取原始波形。
- `analyze`：对 CSV/JSON 波形离线计算峰值、峰峰值、频率、RMS、占空比，硬件不可用时使用演示数据。
- `protocol identify/decode`：根据数据内容和厂商 manifest 判别 JSON、SCPI、CAN、固定头二进制等协议，统一转换为波形帧。
- `frame`：把一次波形保存为 JSON、CSV 和带峰值/频率标注的 SVG 单帧图。
- `bridge serve/expose/connect`：通过中继端建立反向 TCP 桥接，让不在同一局域网的两台主机访问各自设备。

## 推荐工作流

1. 先调用 `discover` 或让用户提供 `TCPIP::<ip>::INSTR`，不要猜测设备地址。
2. 调用 `inspect` 做只读确认，记录 IDN、错误队列和通道状态。
3. 解释测量意图后再调用 `measure`；默认只读，不发送 `*RST`，不自动打开输出。
4. 用 `analyze` 对结果进行工程解释，并明确单位、采样率和数据来源。
5. 将完整 JSON 保存到工作区，便于复现实验和生成报告。

## 厂商自定义协议

将一个 JSON manifest 放入 `examples/protocols/` 或自定义目录。manifest 描述 `magic_hex`、字节序、采样类型、缩放系数和采样率，Agent 可先执行 `protocol identify` 再执行 `protocol decode`，不需要改核心源码。Arm Linux 可安装 `python-can` 后直接采集 SocketCAN：

```bash
python -m pip install '.[can]'
python -m tianyi_electronics_skill protocol capture-can --channel can0 --count 16
```

## 非局域网设备

如果设备主机 A 能主动访问中继主机 B，但 A/B 不在同一局域网：

```powershell
# B：公网/VPN/SSH 可访问的中继端
python -m tianyi_electronics_skill bridge serve --listen 0.0.0.0:9000

# A：设备所在主机，主动向 B 注册真实设备
python -m tianyi_electronics_skill bridge expose --server B地址:9000 --token scope-a --local 192.168.1.20:5025

# Agent 所在主机：建立本地代理，再把 127.0.0.1:15025 当作示波器地址
python -m tianyi_electronics_skill bridge connect --server B地址:9000 --token scope-a --listen 127.0.0.1:15025
```

这只转发字节，不改变 SCPI/CAN/厂商协议；若 A 完全无法访问 B，需要先建立 VPN、SSH 反向隧道或 Tailscale/WireGuard 等网络通道。

真实设备操作前必须确认接线、输入阻抗、量程和电压/电流上限。连接失败时保持只读诊断，不要盲目重试或修改设备状态。

## 入口

在本目录执行：

```powershell
python -m tianyi_electronics_skill discover --subnet 192.168.1.0/24
python -m tianyi_electronics_skill inspect --address 192.168.1.20 --port 5025
python -m tianyi_electronics_skill measure --address 192.168.1.20 --port 5025 --channel CHAN1
python -m tianyi_electronics_skill analyze --demo
```

完整 WaveBench 源码、驱动和示例计划位于 `wavebench/`，可用于高级 run plan、报告和仪器插件。
