# Intraday A-share board fund flow

[中文页面](board-flow.md)

This page covers capturing, archiving, and querying intraday fund flow for A-share industry, concept, and region boards. Data comes from Eastmoney public quote endpoints and is stored in a local SQLite database, so it does not consume TuShare credits.

## Data-source facts

The commands use only two public quote endpoints:

| Purpose | Endpoint | Behaviour |
| --- | --- | --- |
| Board list with current accumulated net inflow | `/api/qt/clist/get`, `fs=m:90+t:N` | At most 100 rows per page; page until the returned `total` is reached. `t:2` industry, `t:3` concept, `t:1` region |
| One board's minute cumulative net inflow for today | `/api/qt/stock/fflow/kline/get`, `klt=1` | Starts at 09:31; `lmt=0` returns every minute up to now |

Field meanings: `f62` is accumulated main-force net inflow in yuan, `f184` its ratio in percent, `f3` the board change in percent, and `f6` turnover in yuan. Minute rows are time, main, small, mid, large, extra-large; every row satisfies `main = large + extra-large`, and parsing verifies it row by row.

The `/api/qt/stock/fflow/KLChart` route often cited by community projects returned `404` on 2026-11-11, so this repository does not implement it. `klt=101` returns a single row for the current day and cannot backfill history; historical daily board flow still comes from TuShare `moneyflow_ind_dc` and related interfaces.

## Storage

The default database is `$DATA_PLATFORM_ROOT/intraday/board_flow.sqlite3`; override it with `--db`. SQLite runs in WAL mode so readers and the writer can coexist.

| Table | Contents | Primary key |
| --- | --- | --- |
| `board_catalog` | Board code, name, type, first and last seen time | `board_code` |
| `flow_snapshot` | Accumulated flow for all boards at one capture time | `(trade_date, board_code, captured_at)` |
| `flow_minute` | Archived minute cumulative flow, including order-size buckets | `(trade_date, board_code, trade_time)` |
| `collection_run` | Duration, status, and failure samples for each monitor or archive run | `run_id` |

Snapshots are only replaced at the same `captured_at`, never backfilled across capture times. Gaps and interruptions therefore stay visible, so collected and reconstructed rows can be told apart later.

## Commands

### Build the board catalog

```bash
marketdata board-flow catalog
```

Fetches all industry, concept, and region boards into `board_catalog`. Minute archiving relies on this catalog.

### Capture one snapshot

```bash
marketdata board-flow snapshot --board-types concept,industry
```

### Capture throughout the session

```bash
marketdata board-flow monitor --interval 30
```

By default it only captures during 09:30–11:30 and 13:00–15:00 and skips weekends. Started before the open, it waits for 09:30; `--no-wait` exits immediately outside trading hours. `--max-minutes` bounds one run for a trial.

### Archive the minute curves after the close

```bash
marketdata board-flow archive-minutes
```

It archives the current day by default and skips boards that already have rows, so rerunning resumes an interrupted job. A failing board does not stop the run; failure samples are stored in `collection_run.detail`. When the requested date differs from the returned data, the board is counted under `date_mismatch` instead of being written under a wrong date.

### Query, export, and chart

```bash
marketdata board-flow status
marketdata board-flow export --date 2026-11-11 --board-type concept --top 30 --out flow.csv
marketdata board-flow report --date 2026-11-11 --board-type concept --top 20 --out race.html
```

`report` writes a self-contained HTML page that draws cumulative net-inflow lines with ECharts and a table of the latest value plus 5, 15, and 30 minute increments. ECharts is loaded from the jsdelivr CDN; offline environments must replace that script source. Without `--out`, output goes to `reports/board-flow/` next to the database.

## Scheduling

| Time | Task |
| --- | --- |
| 09:25 | `board-flow catalog` on the first run of the day |
| 09:30–15:00 | `board-flow monitor --interval 30` |
| 15:05 | `board-flow archive-minutes` |

`monitor` waits for the open and exits after the close, so one start per trading day is enough. Production jobs must run from a versioned checkout, with the database and logs under the data root rather than a development worktree.

## Limitations

- Minute curves can only be captured on the same day. A missed trading day cannot be recovered from this endpoint.
- Trading days are approximated by weekdays without an exchange holiday calendar; holiday runs return nothing and are recorded as `empty`.
- Board constituents overlap heavily. Net inflows of concept boards such as AI, robotics, and chips cannot be summed into market-wide flow.
- Main-force net inflow is a vendor estimate based on trade size and direction; different vendors define it differently.
- These are quote-page endpoints without a stability guarantee. They can rate-limit, change fields, or disappear. Failures are recorded in `collection_run`; never assume a scheduled job succeeded.
