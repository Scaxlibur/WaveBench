# 天翼AI电子科研 Skill

本目录是“天翼AI”杯重庆邮电大学赛区作品基础包。它把原 WaveBench 测量平台移植为 TeleAgent 可使用的电子科研 Skill：

```text
天翼AI电子科研Skill/
├─ SKILL.md                         TeleAgent 技能说明和安全工作流
├─ agents/openai.yaml               Agent 元数据
├─ src/tianyi_electronics_skill/    轻量设备发现、SCPI 读取和信号分析入口
├─ examples/                        无硬件演示数据
├─ tests/                           离线测试
├─ pyproject.toml                   安装配置
└─ wavebench/                       原完整 WaveBench 项目（驱动、计划、报告和文档）
```

## 安装

```powershell
cd C:\Users\Jsuperster\Desktop\天翼\比赛区\天翼AI电子科研Skill
python -m venv .venv
.venv\Scripts\python -m pip install -e .
```

真实 VISA 设备可额外安装 `pyvisa pyvisa-py`；仅做演示和离线分析不需要 VISA。

## 快速演示

```powershell
.venv\Scripts\python -m tianyi_electronics_skill analyze --demo
```

## 连接示波器

```powershell
.venv\Scripts\python -m tianyi_electronics_skill discover --subnet 192.168.1.0/24
.venv\Scripts\python -m tianyi_electronics_skill inspect --address 192.168.1.20 --port 5025
.venv\Scripts\python -m tianyi_electronics_skill measure --address 192.168.1.20 --port 5025 --channel CHAN1
```

输出统一为 JSON，可直接交给 TeleAgent 继续解释或保存到实验工作区。

## 新增能力

### 自定义协议与 CAN

厂商二进制/CAN 协议不必伪装成 SCPI。把 manifest 放到 `examples/protocols/`，例如：

```powershell
python -m tianyi_electronics_skill protocol identify --file .\vendor_frame.bin --manifest-dir .\examples\protocols
python -m tianyi_electronics_skill protocol decode --file .\vendor_frame.bin --manifest-dir .\examples\protocols --sample-rate 100000 --output-dir .\artifacts
```

Agent 会输出候选协议、置信度、统一波形帧和解析工件。

Arm Linux 的 SocketCAN 采集：

```bash
python -m pip install '.[can]'
python -m tianyi_electronics_skill protocol capture-can --channel can0 --count 16
```

### 单帧可视化工件

```powershell
python -m tianyi_electronics_skill frame --file .\examples\demo_capture.json --output-dir .\artifacts
```

每次生成 `.json`（完整数据）、`.csv`（逐点数据）和 `.svg`（带峰值/频率/RMS 标注的图片）。SVG 不依赖桌面 GUI，浏览器、IDE 和 TeleAgent 工作区都能直接打开。

### 跨主机桥接

设备所在主机主动连接中继端，因此 A/B 不需要处于同一个局域网。具体命令和安全边界见 `SKILL.md` 的“非局域网设备”部分。
