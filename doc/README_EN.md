# WaveBench Documentation

[中文文档](README.md) · English

WaveBench is a Python measurement bench for laboratory debugging. It combines explicit instrument commands, run plans, capture packages, and offline reports. It requires Python 3.11 or newer. The current development line is `0.8.22`; the latest stable tag is `v0.8.0`.

> [!WARNING]
> Some commands connect to and change real instruments. Check wiring, input impedance, output state, and voltage/current limits before running a hardware action.

## Start without instruments

The following commands generate and check a plan locally. They do not connect to instruments or enable an output:
The example uses Linux/WSL; Windows users should run it in WSL.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
source .venv/bin/activate

wavebench run template --list
wavebench run template source-scope-sine --output /tmp/wavebench-demo.toml --force
wavebench run check --plan /tmp/wavebench-demo.toml
```

For the terminal UI, install `.[tui]` and run `wavebench tui --fake`. The fake mode uses simulated devices.

## Built-in support

| Entry point | Built-in families | Scope |
| --- | --- | --- |
| Oscilloscope | R&S RTM2000/RTM2032, RIGOL DS1104Z/DS1000Z | Waveform reads, captures, multiple channels, screenshots |
| Signal generator | RIGOL DG4000/DG4202 | Basic waveforms, frequency control, sweeps, arbitrary-wave uploads |
| Power supply | RIGOL DP800 | Status, protection, setpoints, and explicit output control |
| DMM | RIGOL DM3000/DM3058 | Common readings and selected function/range/trigger state |
| Run plans | source, power, scope, dmm, sleep, frequency-response steps | Multi-instrument execution and offline checks |
| TUI | Power, DMM, and source panels | Experimental manual control |
| Plugins | `wavebench.instruments` drivers | Optional, explicitly selected extensions |

## Find a guide

- Setup and configuration: [configuration format](project/WaveBench_配置文件格式.md)
- Run plans and reports: [run plan guide](project/WaveBench_run_plan_使用指南.md)
- Capture and run artifacts: [data output format](project/WaveBench_数据输出格式.md)
- Install or develop plugins: [plugin user guide](project/WaveBench_可安装仪器插件.md) and [plugin development guide](project/WaveBench_插件开发指南.md)
- TUI and read-only HTTP MCP: [TUI](project/WaveBench_TUI终端控制面板.md) and [HTTP MCP](project/WaveBench_HTTP_MCP_只读接口.md)
- Public sweep contract: [English contract](project/WaveBench_sweep_analyzer_contract_EN.md)
- Example plans and their hardware boundaries: [plans README](../plans/README.md)

Most detailed pages are currently maintained in Chinese. Commands, identifiers, and schemas should match across languages.

## Check before running hardware

| Class | Examples | Instrument I/O |
| --- | --- | --- |
| Offline | `run schema`, `run template`, `run check`, `run report`, `capture inspect`, `tui --fake` | No instrument I/O; TUI may write a local log |
| Connected read/preflight | `doctor`, `idn`, `status`, `run verify` | Yes, for queries and checks |
| State-changing | `scope fetch/capture/autoscale`, source/power setters, output commands, `run plan` | Yes; may change setup, trigger acquisition, or switch output |

WaveBench does not implicitly reset instruments, enable outputs, or change oscilloscope input impedance. `power set` and `power output` are separate operations. When enabled, source restoration covers only the documented basic fields; it is not a full channel snapshot.

Executable Python plugins run with the current user's permissions. Install only a trusted local source directory or wheel, and keep real resources, serial numbers, credentials, and generated artifacts out of public documentation.

The root [README](../README.md) is the short project entry point. The [Chinese documentation index](README.md) groups the longer pages by task. Older roadmaps and vendor manuals are reference material, not current feature promises.
