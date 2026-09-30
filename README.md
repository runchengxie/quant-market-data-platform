# quant-market-data-platform

[中文 README](README.zh-CN.md)

`quant-market-data-platform` ingests, standardizes, validates, versions, and publishes market-data assets for quantitative research. It is maintained independently from related research, consumer, and delivery repositories and integrates with them through published interfaces.

The active development scope is primarily mainland China. Research and backtesting projects consume published assets without depending on this repository's internal implementation. Large datasets, runtime outputs, and credentials stay outside Git.

The current Qlib integration provides a read-only DataLoader adapter for published assets. Standard development checks install only the `dev` extra. See [downstream integration](docs/integrations.en.md) for installation options and boundaries.

[Online documentation](https://runchengxie.github.io/quant-market-data-platform/) is built with MkDocs from this repository's `docs/` source. Browse it online or read the files locally; documentation changes are made in the repository.

## Quick start

The project uses `uv` to manage its Python environment:

```bash
uv sync --extra dev
marketdata --help
```

To read or build real data, configure the data root and provider credentials. Start with [local development and operations](docs/operations/backup-and-dev.en.md) and [credential configuration](docs/operations/credentials.en.md).

## Documentation

- [Documentation home](docs/index.md): project overview and key entry points
- [Operations overview](docs/operations.en.md): data commands and routine workflows
- [Data contracts](docs/contracts.en.md): published asset formats and conventions
- [Downstream integration](docs/integrations.en.md): how other projects consume published assets
- [Documentation index](docs/index.md): technical references by topic

## Repository boundaries

This repository owns market-data production and publication. Shared backtesting and portfolio capabilities belong to [`quant-platform`](https://github.com/runchengxie/quant-platform), strategy research and experiment management belong to the private `quant-research` repository, and durable backtest jobs run in [`quant-backtest-runtime`](https://github.com/runchengxie/quant-backtest-runtime).

Current data scope, provider capabilities, quality rules, CLI options, and operations are documented under `docs/`.

Local development checks include `ty check` and other quality gates. See [testing](docs/operations/testing.en.md) for the complete command list.
