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
