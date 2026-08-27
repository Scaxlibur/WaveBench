# WaveBench Documentation

[中文文档](README.md) · English

WaveBench is a Python measurement bench for laboratory debugging. It combines explicit instrument commands, run plans, capture packages, and offline reports. It requires Python 3.11 or newer. The current development line is `0.8.25`; the latest stable tag is `v0.8.0`.

> [!WARNING]
> Some commands connect to and change real instruments. Check wiring, input impedance, output state, and voltage/current limits before running a hardware action.

## Start without instruments

The following commands generate and check a plan locally. They do not connect to instruments or enable an output:
Native Windows and Linux/WSL are supported for offline commands. Native Windows uses the
`portalocker[win32]` lock backend; native Windows and WSL do not share a lock domain.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
source .venv/bin/activate

wavebench run template --list
wavebench run template source-scope-sine --output /tmp/wavebench-demo.toml --force
wavebench run check --plan /tmp/wavebench-demo.toml
```

PowerShell equivalent:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,analysis,tui]"
.\.venv\Scripts\python.exe -m wavebench run template --list
```

Use `COM3` or `COM10` for native Windows serial resources. `\\.\COM10` is normalized to the
same serial identity. VISA, SocketIO, and USB access still depend on the corresponding vendor
driver or backend. Pure-Python plugins support the native Windows lifecycle; native-DLL plugins
require separate validation. The existing [`scripts/wsl-run.ps1`](../scripts/wsl-run.ps1) bridge
remains available for WSL workflows.

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

- Setup and configuration: [configuration format](project/reference/WaveBench_配置文件格式.md)
- Run plans and reports: [run plan guide](project/guides/WaveBench_run_plan_使用指南.md)
- Capture and run artifacts: [data output format](project/reference/WaveBench_数据输出格式.md)
- Install or develop plugins: [plugin user guide](project/guides/WaveBench_可安装仪器插件.md) and [plugin development guide](project/contributing/WaveBench_插件开发指南.md)
- TUI and read-only HTTP MCP: [TUI](project/guides/WaveBench_TUI终端控制面板.md) and [HTTP MCP](project/guides/WaveBench_HTTP_MCP_只读接口.md)
- Example plans and their hardware boundaries: [plans README](../plans/README.md)
- RF-source domain and milestones: [guide](project/guides/WaveBench_RF信号源使用指南.md), [design](project/design/WaveBench_RF信号源设计.md), and [milestones](project/design/WaveBench_RF信号源开发里程碑.md). Core provides M0–M4 and one bounded A5 Pulse Output contract; DSG830 A1/A2/A3/A4/A4-MO/A5 evidence permits production identity, snapshot, OFF-only `rf_source.cw_configure`, RF-OFF internal-sine `rf_source.modulation_configure`, `rf_source.modulation_disable`, safety-gated `rf_source.output` ON/OFF, fixed-profile modulated output, internal/single Pulse configuration, `rf_source.pulse_output`, and frequency-only Step Sweep configuration that remains disabled. A5 covers only the declared `pulse_in_out` output route, not Pulse input, `TRIGGER IN`, trigger, sync, or Sweep execution. PM is limited to the verified `1.25 rad` production profile.

Most detailed pages are currently maintained in Chinese. Commands, identifiers, and schemas should match across languages.

## Check before running hardware

| Class | Examples | Instrument I/O |
| --- | --- | --- |
| Offline | `run schema`, `run template`, `run check`, `run report`, `capture inspect`, `tui --fake` | No instrument I/O; TUI may write a local log |
| Connected read/preflight | `doctor`, `idn`, `status`, `run verify` | Yes, for queries and checks |
| State-changing | `scope fetch/capture/autoscale`, source/power setters, output commands, `run plan` | Yes; may change setup, trigger acquisition, or switch output |

WaveBench does not implicitly reset instruments, enable outputs, or change oscilloscope input impedance. `power set` and `power output` are separate operations. When enabled, source restoration covers only the documented basic fields; it is not a full channel snapshot.

Executable Python plugins run with the current user's permissions. Install only a trusted local source directory or wheel, and keep real resources, serial numbers, credentials, and generated artifacts out of public documentation.

The root [README](../README.md) is the short project entry point. The [Chinese documentation index](README.md) and [project document map](project/README.md) group the longer pages by task. Version history lives in the root [CHANGELOG](../CHANGELOG.md); vendor manuals are maintained in the [instrument plugin repository](https://github.com/Scaxlibur/wavebench-instrument-plugins).
