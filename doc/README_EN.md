# WaveBench Documentation

[中文文档](README.md) | English

WaveBench is a lightweight Python measurement bench for explicit, reproducible control of laboratory instruments. It currently covers oscilloscope capture, signal-generator and power-supply control, digital-multimeter reads, multi-instrument run plans, offline reports, and trusted executable instrument plugins.

The WaveBench distribution includes built-in drivers for the RTM2000/RTM2032, DS1104Z/DS1000Z, DG4000/DG4202, DP800, and DM3000/DM3058 families. These five families are the permanent bundled baseline: first use does not require an external plugin, and they are not scheduled for removal from the main package. External packages are optional, independently released upgrades or extensions. A narrowly allowlisted package may take over a canonical ID, while built-in short names remain pinned to the bundled implementation and uninstalling the package restores the bundled canonical implementation where the IDs are shared.

The current development version is `v0.8.14`. It adds strict DG4000 model, finite-number, and
status-response validation; transactional fixed-wave and output writes; and a fail-closed DAC14
upload path that requires the target channel to be OFF, in FIX mode, with sweep disabled.

> [!IMPORTANT]
> The current formal release is `v0.8.0`. This release introduces Instrument API V2, managed plugin lifecycle operations, canonical override slots, and the SocketIO backend; those capabilities are not part of `v0.7.0`. Users of other releases should follow the documentation stored in the matching tag.

> Warning: WaveBench communicates with real laboratory equipment. Review the active configuration, wiring, voltage/current limits, input impedance, and output state before running any command that can change an instrument.

## Start here

- [Project boundaries](project/WaveBench_项目边界.md)
- [Configuration format](project/WaveBench_配置文件格式.md)
- [Run-plan guide](project/WaveBench_run_plan_使用指南.md)
- [Data and artifact formats](project/WaveBench_数据输出格式.md)
- [Error handling and command logs](project/WaveBench_错误处理和日志策略.md)
- [Executable instrument plugin development](project/WaveBench_插件开发指南.md)
- [Sweep analyzer public contract](project/WaveBench_sweep_analyzer_contract_EN.md)
- [Installing and managing instrument plugins](project/WaveBench_可安装仪器插件.md)

## Managed local plugins

WaveBench treats executable Python plugins as trusted code, not as sandboxed extensions. The managed lifecycle accepts only an explicitly supplied local source directory or wheel, requires the current interpreter to be a virtual environment, does not download packages, installs with dependencies disabled, and never edits `wavebench.toml`.

```bash
python -m wavebench plugin package check <folder-or-wheel>
python -m wavebench plugin install <folder-or-wheel> --dry-run
python -m wavebench plugin install <folder-or-wheel>
python -m wavebench plugin installed
python -m wavebench plugin info <canonical-driver-id> --installed
python -m wavebench plugin upgrade <folder-or-wheel> --dry-run
python -m wavebench plugin downgrade <folder-or-wheel> --dry-run
python -m wavebench plugin remove <canonical-driver-id> --dry-run
python -m wavebench plugin recover
```

Source-directory inspection executes the package's declared build backend in a subprocess, including during dry-run. Wheel inspection is static. Installation, replacement, and removal use an environment lock, an atomic ledger, a write-ahead transaction journal, cached wheel hashes, post-install descriptor validation, and best-effort rollback. Automatic recovery is deliberately limited to states that can be proven to match the exact previous or target installation.

The local marketplace index remains read-only. It does not download or install plugins.

## Optional TUI

The Textual interface is an optional extra. In a source checkout, install it with `python -m pip install -e ".[tui]"`, then run `python -m wavebench tui` or `python -m wavebench tui --fake`.

Its supported product scope is intentionally frozen to the power-supply, DMM, and signal-source panels. CLI commands, run plans, and services remain the primary interfaces; the TUI is not a run-plan editor, plugin manager, full oscilloscope viewer, or reporting system.

Executable plugins use canonical IDs and cannot define aliases. Built-in IDs are protected except for narrowly allowlisted optional-override slots that bind one canonical ID to one distribution. The current shared-ID slots cover DG4000, DM3000, DP800, and RTM2000. Their built-in short aliases always select the bundled baseline, and uninstalling the external distribution restores the bundled canonical implementation. DS1000Z uses the separate external canonical ID `rigol.ds1000z`; its built-in `ds1104` and `ds1000z` aliases remain available without the package. DG4000 source plugins may import the stable `DG4000DacBlock` and `DG4000ByteOrder` types from `wavebench.instruments`; waveform loading, normalization, DAC14 encoding, services, and safety policy remain core responsibilities. The source code retains the historical term `migration slot` for this allowlist, but it does not imply deprecating the bundled drivers.

## Safety defaults

- No implicit instrument reset.
- No hidden output enable/disable.
- No automatic change to oscilloscope input impedance.
- No raw SCPI surface in the HTTP MCP service.
- No third-party plugin import during ordinary metadata listing.
- No system-Python plugin installation, network dependency resolution, or unmanaged-distribution takeover.

Most detailed design documents are currently maintained in Chinese. Commands, identifiers, schemas, and examples are kept stable across languages.
