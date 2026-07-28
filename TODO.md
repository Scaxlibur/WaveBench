# WaveBench AI-Agent Experience TODO

目标：让 AI agent 能以低风险、结构化、可审计的方式观察实验台、理解已有产物、提出建议，并只通过显式 run plan 执行会改变仪器状态的动作。

## P0: 安全只读上下文

- [x] 增加 MCP 工具 `scope.observe`：读取配置中的示波器，返回 IDN、状态快照、通道高阻判断、当前波形摘要；不保存文件、不改仪器状态、不暴露 raw SCPI。
- [x] `scope.observe` 支持 CH1-CH4 多通道观察；显式 `fetch_waveform=true` 且获取到两个以上通道时返回 pairwise relationships。
- [x] 增加 MCP 工具 `doctor.config`：把现有 `doctor` 结果结构化返回给 agent，支持配置可达性和型号匹配判断。
- [ ] 增加 MCP 工具 `net.discover`：结构化返回只读 LAN 仪器发现结果，默认限制网段大小和端口集合。
- [ ] 所有 MCP 工具都标注 `read_only`、`mutates_instrument=false`、`raw_scpi=false` 等安全元数据。

## P1: 离线产物理解

- [ ] 增强 `capture.inspect`：可选返回 FFT 摘要、质量告警、推荐下一步采集参数，但不读取大数组进响应。
- [ ] 增加 `capture.list`：列出 `data/raw` 下最近采集包，供 agent 找上下文。
- [ ] 增加 `run.list` / `run.inspect`：列出和解释 `data/runs` 下的实验记录。
- [ ] 给采集包生成 agent 友好的 `summary.md` 或结构化 `analysis.json`。

## P2: Agent 建议层

- [x] 增加 `scope.advise`：基于 `scope.observe` 的结果给出时基、垂直档位、触发、点数建议；只返回建议，不应用。
  - [x] 根据实测或期望频率推荐每通道 focus time-range，默认约 10 个周期。
  - [x] 根据实测 Vpp 推荐垂直档位，默认约占 5 格。
  - [x] 识别 CH1/CH2 这类大频率跨度，建议分通道/分 profile 观察，避免单时基误判形状。
- [x] 增加 `scope.observe.expectations`：把已知闭环信号作为结构化依据，例如 CH1 1 kHz/1 Vpp/50% 方波、CH2 50 kHz/1 Vpp/500 mVdc/30% 对称三角波，并返回逐项 pass/warn/fail。
  - [x] 支持 frequency/Vpp/mean/duty/symmetry_percent 的基础断言。
  - [x] 期望断言必须显式 `fetch_waveform=true`，避免 agent 在不知情时触发波形传输状态变化。
  - [x] 返回总体 expectation status，并把 fail/warn 加入 agent hints。
- [x] 增加多时基建议：当多个通道的频率跨度较大时，提醒 agent 不要用单个显示时基同时判断所有通道形状，应分通道或分 profile 采集。
- [x] 多通道关系分析增加交点：对每对通道返回交点数量、采样返回点、交点时间/电压和相对斜率方向；交点过多时截断并给 warning。
- [ ] 让 `scope.expect` 也参与多时基建议：即使当前频率估计低置信，也能利用用户给定的期望频率识别 CH1/CH2 这种 50x 频率跨度。
- [ ] 增加 `plan.propose`：从自然语言目标或结构化目标生成保守 run plan 草案，默认写到 `plans/`，不执行。
- [ ] 增加 `plan.explain`：解释某个 run plan 会读写哪些仪器、哪些步骤会改变输出状态、有哪些保护。

## P3: 可控执行边界

- [ ] MCP 继续保持默认只读；会改仪器状态的能力只通过显式 `run plan` 文件和人工确认入口暴露。
- [x] 增加显式示波器显示控制 CLI：通道显示 on/off、focus 单通道、显式 autoscale；用于人类和 agent 做可审计调参。
- [x] focus 动作只调整示波器显示/采集窗口，不改变信号源、电源或被测对象；执行后输出 mutation manifest。
- [ ] 对所有会改状态的 plan 步骤生成 mutation manifest，便于 agent 在执行前向人类说明。
- [ ] 增加跨进程仪器锁，避免 Windows/WSL/多个 agent 同时打开同一台仪器导致响应串线。

## P4: 人类与 Agent 共用体验

- [x] 增加 `scripts/wsl-run.ps1` 作为 Windows 到 WSL 的标准执行入口，并在 README 中记录。
- [ ] 为 WSL/Windows 推荐环境增加 `doctor environment` 或 `env doctor`，检查 Python、PyVISA、WSL、网络可达性。
- [ ] 增加最小可视化：采集包自动生成 waveform/FFT PNG，报告中可直接查看。
