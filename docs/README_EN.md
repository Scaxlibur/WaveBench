# WaveBench Documentation

[中文完整文档](index.md) · [Project README](../README.md) · [Changelog](../CHANGELOG.md)

The current complete documentation is maintained in Chinese. This page is an English orientation page; command syntax, schemas, and model-specific support are defined by the installed version of WaveBench and its instrument plugins.

## Start without instruments

Install a release wheel with Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/). The [installation guide](getting-started/installation.md) separates release installations, fixed-tag source checkouts, and the unreleased `Dev` branch. Run the following with the installed environment's Python:

```bash
python -m wavebench --version
python -m wavebench run template --list
python -m wavebench run template source-scope-sine --output example.toml
```

These commands perform no instrument I/O. Follow the [Chinese Quickstart](getting-started/quickstart.md) to create an offline configuration and check the plan. A successful check prints `safety_limits=ok / 安全上限=通过`.

## Safety boundary

- `run schema`, `run template`, `run check`, and `run report` are offline.
- `doctor` and `run verify` query configured instruments.
- `run plan`, output commands, capture commands, and non-fake TUI controls may change instrument state.

Before a hardware action, check wiring, input impedance, output state, and voltage/current limits. Model-specific SCPI, limits, and evidence belong to the [instrument plugin repository](https://github.com/Scaxlibur/wavebench-instrument-plugins).

Use the [Chinese documentation index](index.md) to find guides, Reference pages, Concepts, development material, RFCs, and release history.
