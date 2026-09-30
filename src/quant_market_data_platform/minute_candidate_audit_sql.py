"""DuckDB query used by the TuShare minute candidate semantic audit."""

SYMBOL_AUDIT_SQL = """
WITH
g AS (
    SELECT ts_code, trade_time, open, close, high, low, vol, amount
    FROM read_parquet(?)
    WHERE right(ts_code, 3) IN ('.SH', '.SZ')
),
t AS (
    SELECT ts_code, trade_time, open, close, high, low, vol, amount
    FROM read_parquet(?)
    WHERE right(ts_code, 3) IN ('.SH', '.SZ')
),
gs AS (
    SELECT
        ts_code,
        count(*) AS g_rows,
        count_if(vol > 0 OR amount > 0) AS g_active_minutes,
        min(trade_time) AS g_time_min,
        max(trade_time) AS g_time_max,
        arg_min(open, trade_time) AS g_first_open,
        arg_max(close, trade_time) AS g_last_close,
        max(high) AS g_high,
        min(low) AS g_low,
        sum(vol) AS g_vol,
        sum(amount) AS g_amount,
        sum(CASE WHEN CAST(trade_time AS TIME) <= TIME '10:00:00' THEN vol ELSE 0 END)
            AS g_open_vol,
        sum(CASE WHEN CAST(trade_time AS TIME) >= TIME '14:30:00' THEN vol ELSE 0 END)
            AS g_close_vol,
        count_if(CAST(trade_time AS TIME) = TIME '09:30:00') AS g_0930_rows
    FROM g
    GROUP BY ts_code
),
ts AS (
    SELECT
        ts_code,
        count(*) AS t_rows,
        count_if(vol > 0 OR amount > 0) AS t_active_minutes,
        count_if(
            CAST(trade_time AS TIME) > TIME '09:30:00' AND (vol > 0 OR amount > 0)
        ) AS t_active_minutes_no_0930,
        min(trade_time) AS t_time_min,
        max(trade_time) AS t_time_max,
        arg_min(open, trade_time) AS t_first_open,
        arg_min(open, trade_time) FILTER (
            WHERE CAST(trade_time AS TIME) > TIME '09:30:00'
        ) AS t_first_open_no_0930,
        arg_max(close, trade_time) AS t_last_close,
        max(high) AS t_high,
        min(low) AS t_low,
        max(high) FILTER (
            WHERE CAST(trade_time AS TIME) > TIME '09:30:00'
        ) AS t_high_no_0930,
        min(low) FILTER (
            WHERE CAST(trade_time AS TIME) > TIME '09:30:00'
        ) AS t_low_no_0930,
        sum(vol) AS t_vol,
        sum(amount) AS t_amount,
        sum(CASE WHEN CAST(trade_time AS TIME) > TIME '09:30:00' THEN vol ELSE 0 END)
            AS t_vol_no_0930,
        sum(CASE WHEN CAST(trade_time AS TIME) > TIME '09:30:00' THEN amount ELSE 0 END)
            AS t_amount_no_0930,
        sum(CASE WHEN CAST(trade_time AS TIME) <= TIME '10:00:00' THEN vol ELSE 0 END)
            AS t_open_vol,
        sum(
            CASE
                WHEN CAST(trade_time AS TIME) > TIME '09:30:00'
                    AND CAST(trade_time AS TIME) <= TIME '10:00:00'
                THEN vol ELSE 0
            END
        ) AS t_open_vol_no_0930,
        sum(CASE WHEN CAST(trade_time AS TIME) >= TIME '14:30:00' THEN vol ELSE 0 END)
            AS t_close_vol,
        count_if(CAST(trade_time AS TIME) = TIME '09:30:00') AS t_0930_rows
    FROM t
    GROUP BY ts_code
),
ks AS (
    SELECT
        g.ts_code,
        count(*) AS common_keys,
        sum(abs(g.close - t.close)) AS close_abs_error_sum,
        max(abs(g.close - t.close)) AS close_abs_error_max,
        sum(CASE WHEN g.close = t.close THEN 1 ELSE 0 END) AS close_exact_count,
        sum(CASE WHEN t.close != 0 THEN abs(g.close - t.close) / abs(t.close) ELSE 0 END)
            AS close_abs_relative_error_sum,
        count_if(t.close != 0) AS close_relative_count,
        sum(abs(g.open - t.open)) AS open_abs_error_sum,
        sum(abs(g.high - t.high)) AS high_abs_error_sum,
        sum(abs(g.low - t.low)) AS low_abs_error_sum
    FROM g
    INNER JOIN t USING (ts_code, trade_time)
    GROUP BY g.ts_code
)
SELECT
    coalesce(gs.ts_code, ts.ts_code) AS ts_code,
    gs.* EXCLUDE (ts_code),
    ts.* EXCLUDE (ts_code),
    ks.* EXCLUDE (ts_code)
FROM gs
FULL OUTER JOIN ts USING (ts_code)
LEFT JOIN ks USING (ts_code)
ORDER BY ts_code
"""
