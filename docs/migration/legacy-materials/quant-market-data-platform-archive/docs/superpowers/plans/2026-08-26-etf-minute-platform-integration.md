# 公共源 ETF 分钟数据平台集成实施计划

目标：增加平台原生的可选公共源 ETF 分钟数据 provider，写入经过验证的不可变 ETF 分钟资产、manifest 和 receipt，同时保持现有股票分钟数据 alias 不变。

架构：在 `market_data_platform.providers.public_etf_minute` 中实现数据源适配、规范化和分区编排。增加 `marketdata data mirror-public-etf-minute` 命令，解析平台资产根目录，写入 `assets/derived/a_share/etf_minute_<period>m/<version>/`，并记录 `manifest.yml` 和 `receipt.json`。parity 测试完成前，独立的 `etf-minute-fetcher` 仓库保持不变。

技术栈：Python 3.11+、pandas、PyArrow、可选 AKShare、系统 `curl`、argparse、YAML/JSON manifest、pytest、Ruff、ty。

设计依据：`docs/superpowers/specs/2026-08-26-etf-minute-platform-design.md`

## 全局约束

- 保留八列 Parquet 契约：`ts_code, trade_time, open, close, high, low, vol, amount`。
- 支持 `1`、`5`、`15`、`30`、`60` 分钟周期，以及 `auto`、`eastmoney`、`sina` 数据源。
- 拒绝 `source=sina` 与 `period=1` 的组合。
- `akshare`、pandas 和 PyArrow 不进入核心依赖，通过 `etf-minute-public` 暴露。
- ETF 分钟数据与 `assets/derived/a_share/minute_1m` 分开存储。
- 不在 Git 中写入或修改 ETF 数据。
- 本计划不修改 `etf-minute-fetcher`、dashboard 下游或 `a_share_current.json`。
- 每次生产代码修改前都必须先增加失败测试，修改后运行针对性测试命令。

---

### 任务 1：定义 provider 契约和可选依赖

文件：
- 修改：`pyproject.toml:25-65`
- 修改：`src/market_data_platform/cli.py:16-21,43-54`
- 测试：`tests/test_public_etf_minute.py`

接口：
- 在 provider 模块中提供后续任务使用的 `EtfMinuteMirrorOptions`、`ETF_MINUTE_COLUMNS`、`ETF_MINUTE_PERIODS` 和 `ETF_MINUTE_SOURCES`。
- 提供包含 `akshare`、`pandas` 和 `pyarrow` 的 `etf-minute-public` 可选 extra。
- 新命令导入 AKShare 时，缺少依赖的 CLI 提示应建议运行 `uv sync --extra etf-minute-public`。

- [ ] 步骤 1：编写失败的契约测试

  增加测试，导入 provider 契约，断言列元组和允许值完全匹配，并断言项目元数据包含带有 `akshare`、`pandas` 和 `pyarrow` 的 `etf-minute-public` extra。

  ```python
  def test_public_etf_minute_contract_is_explicit():
      from market_data_platform.providers.public_etf_minute import (
          ETF_MINUTE_COLUMNS,
          ETF_MINUTE_PERIODS,
          ETF_MINUTE_SOURCES,
      )

      assert ETF_MINUTE_COLUMNS == (
          "ts_code",
          "trade_time",
          "open",
          "close",
          "high",
          "low",
          "vol",
          "amount",
      )
      assert ETF_MINUTE_PERIODS == ("1", "5", "15", "30", "60")
      assert ETF_MINUTE_SOURCES == ("auto", "eastmoney", "sina")
  ```

- [ ] 步骤 2：运行针对性测试，确认测试失败

  Run: `uv run --locked --extra dev python -m pytest tests/test_public_etf_minute.py::test_public_etf_minute_contract_is_explicit -q`

  预期结果：失败，因为 `market_data_platform.providers.public_etf_minute` 尚不存在。

- [ ] Step 3: Add the minimal provider constants and optional extra

  Create the provider module with only the constants and a frozen options dataclass declaration needed by the test. Add:

  ```toml
  etf-minute-public = [
    "akshare>=1.18.94,<2",
    "pandas>=2.2",
    "pyarrow>=25.0.0",
  ]
  ```

  Add `"akshare"` to `OPTIONAL_DEPENDENCIES` and map `data` commands that miss it to `etf-minute-public` once the parser is registered.

- [ ] Step 4: Run the focused test to verify it passes

  Run: `uv run --locked --extra dev python -m pytest tests/test_public_etf_minute.py::test_public_etf_minute_contract_is_explicit -q`

  Expected: PASS.

- [ ] Step 5: Refresh the lockfile and commit

  Run: `uv lock`.

  Then run: `git add pyproject.toml uv.lock src/market_data_platform/providers/public_etf_minute.py src/market_data_platform/cli.py tests/test_public_etf_minute.py && git commit -m "feat: define public ETF minute provider contract"`.

### Task 2: Implement normalization and source selection

Files:
- Modify: `src/market_data_platform/providers/public_etf_minute.py`
- Test: `tests/test_public_etf_minute.py`

Interfaces:
- Consumes raw AKShare/Eastmoney/Sina frames.
- Produces `normalize_etf_minute_frame(raw: pd.DataFrame | None, ts_code: str) -> pd.DataFrame`.
- Produces `fetch_etf_minute_range(ts_code: str, start_date: str, end_date: str, *, period: str, source: str, attempts: int = 3, retry_delay: float = 1.0) -> tuple[pd.DataFrame, str]`, where the second value is the selected source identity.
- Produces `normalize_ts_code(value: str) -> str` and validates date ordering, source, period, and numeric fields.

- [ ] Step 1: Write failing normalization and source tests

  Add tests for Eastmoney Chinese columns, Sina's nullable `amount`, duplicate `(ts_code, trade_time)` removal, bare-code exchange inference, invalid inputs, explicit Sina selection, and automatic fallback after AKShare failure. Use monkeypatched source functions and never call the network.

  ```python
  def test_normalize_etf_minute_frame_uses_stable_schema():
      raw = pd.DataFrame(
          {
              "时间": ["20260824 09:30:00", "20260824 09:30:00"],
              "开盘": [1.0, 1.0],
              "收盘": [1.01, 1.01],
              "最高": [1.02, 1.02],
              "最低": [0.99, 0.99],
              "成交量": [100, 100],
              "成交额": [10000, 10000],
          }
      )

      result = normalize_etf_minute_frame(raw, "512880.SH")

      assert list(result.columns) == list(ETF_MINUTE_COLUMNS)
      assert len(result) == 1
      assert result.loc[0, "ts_code"] == "512880.SH"
  ```

- [ ] Step 2: Run the focused tests to verify they fail

  Run: `uv run --locked --extra dev python -m pytest tests/test_public_etf_minute.py -k 'normalize or fallback or source or rejects' -q`

  Expected: FAIL because the normalization and fetching functions are not implemented.

- [ ] Step 3: Implement the minimal provider adapters

  Implement date/symbol validation, Chinese-column normalization, nullable numeric coercion, timestamp range filtering, and duplicate-key rejection/removal. Implement AKShare loading inside the function body, direct Eastmoney and Sina curl calls with argv-only subprocess invocation, retry/backoff, and the source-selection result. Keep source-specific fields out of the normalized DataFrame.

- [ ] Step 4: Run the focused tests to verify they pass

  Run: `uv run --locked --extra dev python -m pytest tests/test_public_etf_minute.py -k 'normalize or fallback or source or rejects' -q`

  Expected: all selected tests PASS.

- [ ] Step 5: Run formatting and commit

  Run: `uv run --locked --extra dev python -m ruff check src/market_data_platform/providers/public_etf_minute.py tests/test_public_etf_minute.py`.

  Then run: `git add src/market_data_platform/providers/public_etf_minute.py tests/test_public_etf_minute.py && git commit -m "feat: add public ETF minute source adapters"`.

### Task 3: Add immutable partition orchestration and receipts

Files:
- Modify: `src/market_data_platform/providers/public_etf_minute.py`
- Test: `tests/test_public_etf_minute.py`

Interfaces:
- Produces `write_etf_minute_partition(frame: pd.DataFrame, output_dir: Path, trade_date: str) -> Path | None`.
- Produces `mirror_public_etf_minute(options: EtfMinuteMirrorOptions) -> dict[str, object]`.
- Writes `<version>/trade_date=YYYYMMDD/part-00000.parquet`, `<version>/manifest.yml`, and `<version>/receipt.json`.
- Uses `resolve_artifacts_root()` when `artifacts_root` is omitted and defaults output to `assets/derived/a_share/etf_minute_<period>m/<version>`.

- [ ] Step 1: Write failing orchestration tests

  Add tests that monkeypatch `fetch_etf_minute_range` with deterministic frames and verify atomic partition creation, skipped existing dates, empty-date accounting, versioned default path, manifest totals, selected source, and SHA-256 hashes in `receipt.json`. Add a failed-fetch test that records a failed date and does not create a formal partition.

  ```python
  def test_mirror_public_etf_minute_writes_versioned_partition_and_receipt(
      monkeypatch: pytest.MonkeyPatch, tmp_path: Path
  ):
      monkeypatch.setattr(
          provider,
          "fetch_etf_minute_range",
          lambda ts_code, start_date, end_date, *, period, source, attempts=3, retry_delay=1.0: (
              _fixture_frame(),
              "eastmoney-akshare",
          ),
      )

      result = provider.mirror_public_etf_minute(
          provider.EtfMinuteMirrorOptions(
              symbols=("512880.SH",),
              start_date="20260824",
              end_date="20260824",
              period="1",
              output_dir=tmp_path / "etf_minute_1m" / "v1",
          )
      )

      assert result["status"] == "completed"
      part = tmp_path / "etf_minute_1m/v1/trade_date=20260824/part-00000.parquet"
      assert part.is_file()
      assert (
          yaml.safe_load((tmp_path / "etf_minute_1m/v1/manifest.yml").read_text())["totals"]["rows"]
          == 1
      )
      assert json.loads((tmp_path / "etf_minute_1m/v1/receipt.json").read_text())["files"][0][
          "sha256"
      ]
  ```

- [ ] Step 2: Run orchestration tests to verify they fail

  Run: `uv run --locked --extra dev python -m pytest tests/test_public_etf_minute.py -k 'mirror or partition or receipt or manifest' -q`

  Expected: FAIL because the orchestration and receipt functions are not implemented.

- [ ] Step 3: Implement atomic writes and manifest/receipt generation

  Implement `EtfMinuteMirrorOptions`, date-range expansion, pending-date resolution, per-symbol fetch calls, partition filtering, atomic Parquet writes through a same-directory temporary file, and deterministic manifest/receipt payloads. Hash every written Parquet file. Use YAML for the platform manifest and JSON for the run receipt. Return `completed`, `partial`, `empty`, or `failed` based on written, empty, and failed requested dates.

- [ ] Step 4: Run orchestration tests to verify they pass

  Run: `uv run --locked --extra dev python -m pytest tests/test_public_etf_minute.py -k 'mirror or partition or receipt or manifest' -q`

  Expected: all selected tests PASS.

- [ ] Step 5: Run the complete provider test file and commit

  Run: `uv run --locked --extra dev python -m pytest tests/test_public_etf_minute.py -q`.

  Then run: `git add src/market_data_platform/providers/public_etf_minute.py tests/test_public_etf_minute.py && git commit -m "feat: publish public ETF minute partitions with receipts"`.

### Task 4: Expose the platform-native CLI

Files:
- Modify: `src/market_data_platform/cli_data_part01.py`
- Modify: `src/market_data_platform/cli_data_part02.py`
- Modify: `src/market_data_platform/cli_data.py`
- Modify: `src/market_data_platform/cli.py`
- Test: `tests/test_cli_data_public_etf_minute.py`

Interfaces:
- Adds `marketdata data mirror-public-etf-minute`.
- Accepts repeatable or comma-separated `--symbols`, required `--start-date` and `--end-date`, `--period`, `--source`, `--artifacts-root`, `--version`, `--out-dir`, `--no-skip`, and `--dry-run`.
- Dispatches to `mirror_public_etf_minute` and prints the returned JSON summary.

- [ ] Step 1: Write failing CLI tests

  Add parser tests asserting the command and exact defaults, a dispatch test with a monkeypatched mirror function, and a missing-AKShare test asserting the message recommends `uv sync --extra etf-minute-public`.

  ```python
  def test_public_etf_minute_cli_parser_exposes_platform_command():
      parser = cli.build_parser()
      args = parser.parse_args(
          [
              "data",
              "mirror-public-etf-minute",
              "--symbols",
              "512880.SH,159915.SZ",
              "--start-date",
              "20260824",
              "--end-date",
              "20260825",
          ]
      )

      assert args.data_command == "mirror-public-etf-minute"
      assert args.period == "1"
      assert args.source == "auto"
  ```

- [ ] Step 2: Run the CLI tests to verify they fail

  Run: `uv run --locked --extra dev python -m pytest tests/test_cli_data_public_etf_minute.py -q`

  Expected: FAIL because the parser command and handler are not registered.

- [ ] Step 3: Register the parser and handler

  Add a parser factory in `cli_data_part01.py`, a handler in `cli_data_part02.py`, and wire both through `cli_data.py`. Add `akshare` to the optional dependency set and make `_extra_for_missing_dependency` return `etf-minute-public` for this data command. Keep imports lazy so `marketdata --help` works without AKShare.

- [ ] Step 4: Run the CLI tests to verify they pass

  Run: `uv run --locked --extra dev python -m pytest tests/test_cli_data_public_etf_minute.py -q`.

  Expected: all CLI tests PASS.

- [ ] Step 5: Run the parser boundary tests and commit

  Run: `uv run --locked --extra dev python -m pytest tests/test_cli_data_public_etf_minute.py tests/test_cli_dependency_boundaries.py -q`.

  Then run: `git add src/market_data_platform/cli.py src/market_data_platform/cli_data.py src/market_data_platform/cli_data_part01.py src/market_data_platform/cli_data_part02.py tests/test_cli_data_public_etf_minute.py && git commit -m "feat: expose public ETF minute mirror command"`.

### Task 5: Document the contract and verify the branch

Files:
- Create: `docs/operations/etf-minutes.md`
- Modify: `docs/operations.md`
- Modify: `docs/README.md`
- Modify: `docs/integrations.md`
- Test: `tests/test_framework_documentation.py`

Interfaces:
- Documents the command, optional install, storage layout, source limitations, receipt fields, and the fact that the ETF asset is not in `a_share_current.json`.
- Provides a stable downstream read path through the version manifest/receipt rather than a hard-coded version directory.

- [ ] Step 1: Add the documentation navigation assertion

  Extend `test_qlib_docs_describe_conditional_dataloader_support` with an assertion that `etf-minutes.md` is linked from `docs/README.md`. This makes the new operational entry point discoverable and gives the documentation change a focused regression check.

- [ ] Step 2: Run the focused documentation test to verify the missing link fails

  Run: `uv run --locked --extra dev python -m pytest tests/test_framework_documentation.py -q`.

  Expected: FAIL with an assertion error because `etf-minutes.md` is not yet linked from `docs/README.md`.

- [ ] Step 3: Add operational documentation and links

  Document:

  ```text
  uv sync --extra etf-minute-public
  marketdata data mirror-public-etf-minute \
    --symbols 510050.SH \
    --start-date 20260824 --end-date 20260825 \
    --period 1 --source auto \
    --version etf_minute_1m_20260825
  ```

  State that 1-minute public data has a recent upstream window, Sina lacks `amount`, source provenance is in `receipt.json`, and consumers must not treat this asset as complete all-A-share minute coverage.

- [ ] Step 4: Run focused and full verification

  Run:

  ```bash
  uv run --locked --extra dev python -m pytest tests/test_public_etf_minute.py tests/test_cli_data_public_etf_minute.py tests/test_cli_dependency_boundaries.py tests/test_framework_documentation.py -q
  uv run --locked --extra dev python -m ruff check .
  uv run --locked --extra dev python -m ruff format --check .
  uv run --locked --extra dev ty check --error-on-warning
  ```

  Expected: all commands exit 0 with no test failures, lint errors, formatting differences, or type warnings.

- [ ] Step 5: Review the final diff and commit

  Run: `git diff origin/main...HEAD --stat` and `git diff --check`.

  Confirm no Parquet, credential, cache, or `.venv` files are tracked. Then run: `git add docs/operations/etf-minutes.md docs/operations.md docs/README.md docs/integrations.md tests/test_framework_documentation.py && git commit -m "docs: document public ETF minute asset"`.
