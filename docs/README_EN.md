# WaveBench Documentation

[中文完整文档](index.md) · [Project README](../README.md) · [Changelog](../CHANGELOG.md)

The current complete documentation is maintained in Chinese. This page is an English orientation page; command syntax, schemas, and model-specific support are defined by the installed version of WaveBench and its instrument plugins.

## Start without instruments

Use a new checkout of the unreleased `Dev` branch, Python 3.11+, and [uv](https://docs.astral.sh/uv/getting-started/installation/) on PATH. The [installation guide](getting-started/installation.md) explains checkout and index configuration and links to released-version instructions.

```bash
uv sync --locked
uv run --no-sync wavebench run template --list
uv run --no-sync wavebench run template source-scope-sine --print
uv run --no-sync wavebench run check --plan plans/closure_sine_1k.toml
```

The last command succeeds with `safety_limits=ok / 安全上限=通过`. It performs no instrument I/O. See the [Chinese Quickstart](getting-started/quickstart.md) for expected output, Windows commands, and the next steps.

## Safety boundary

- `run schema`, `run template`, `run check`, and `run report` are offline.
- `doctor` and `run verify` query configured instruments.
- `run plan`, output commands, capture commands, and non-fake TUI controls may change instrument state.

Before a hardware action, check wiring, input impedance, output state, and voltage/current limits. Model-specific SCPI, limits, and evidence belong to the [instrument plugin repository](https://github.com/Scaxlibur/wavebench-instrument-plugins).

Use the [Chinese documentation index](index.md) to find guides, Reference pages, Concepts, development material, RFCs, and release history.
