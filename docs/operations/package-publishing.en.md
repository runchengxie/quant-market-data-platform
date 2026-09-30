# Python package publishing

[中文页面](package-publishing.md)

This guide covers building `market-data-platform` as an installable Python package for downstream projects such as `strategy-pipeline`. Data assets continue to use the platform's asset-publication workflow; the Python package does not distribute platform datasets.

## Build

```bash
uv build --clear
```

There is no active `Package` workflow in this repository, so builds must be run locally. A disabled workflow template is not evidence that CI has run or validated a build. If automated builds are restored, validate the wheel/sdist and publication-credential contract again.

Package contents:

- The sdist includes `docs/` and `tests/` so downstream users can access full documentation and source-level validation examples.
- The wheel contains only the `market_data_platform` runtime package; it excludes `docs/` and `tests/`.
- `py.typed` is not published yet. The project will consider advertising a type surface after expanding `ty` coverage and validating downstream type contracts.

## Publish

There is no automatic publish job currently. If the `Package` workflow is restored, publishing is allowed only when pushing a `v*` tag or manually running the workflow with `publish=true`.

The destination must be a PyPI-compatible upload endpoint. Configure these GitHub secrets in the `market-data-platform` repository:

| Secret | Purpose |
| --- | --- |
| `MDP_PUBLISH_URL` | Package registry upload endpoint. |
| `MDP_PUBLISH_TOKEN` | Token authentication; choose this or username/password. |
| `MDP_PUBLISH_USERNAME` | Username for username/password authentication. |
| `MDP_PUBLISH_PASSWORD` | Password or token for username/password authentication. |

Increment the version in `pyproject.toml` before publishing. Do not publish different content under the same version.

## Pin an owner commit before a package registry is available

Downstream projects may install an already merged commit while a registry is not configured. Pin the full commit SHA; do not use floating `main`, development branches, or unmerged PR branches. For example:

```toml
[project]
dependencies = ["market-data-platform[research-features,duckdb]>=0.2.0"]

[tool.uv.sources]
market-data-platform = { git = "https://github.com/runchengxie/quant-market-data-platform.git", rev = "<full merged commit SHA>" }
```

Choose extras based on downstream imports. Contract modules that do not import pandas, Parquet, or DuckDB can omit those extras. After the change, run `uv lock` and commit `uv.lock`, then run `uv sync --locked`, public-import checks, and behavior-contract tests. Upgrade only after the platform change has merged and passed validation, then pin the new full SHA.

## Migrate downstream installations to a registry

`strategy-pipeline` currently uses editable source from a neighboring directory for local integration while the package registry is unavailable. The recommended transition is to pin the immutable Git commit first, then switch to the package source after the first registry release:

1. Configure an accessible package index and read token in the downstream validation environment.
2. Run `uv lock --no-sources` downstream and confirm that `market-data-platform>=0.1.0` resolves from the registry.
3. Remove the local path source for `market-data-platform` from the downstream `pyproject.toml`.
4. Remove source-checkout steps from ordinary validation; retain the dedicated platform contract check for cross-repository boundaries.

If `uv lock --no-sources` reports `market-data-platform was not found in the package registry`, the package is not available from a registry readable by the downstream environment. Keep its source checkout until the registry is ready.
