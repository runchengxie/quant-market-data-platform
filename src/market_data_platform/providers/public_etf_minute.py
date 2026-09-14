"""Public-source ETF minute data provider contract and mirror helpers."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from market_data_platform.artifacts import resolve_artifacts_root

ETF_MINUTE_COLUMNS = (
    "ts_code",
    "trade_time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
)
ETF_MINUTE_PERIODS = ("1", "5", "15", "30", "60")
ETF_MINUTE_SOURCES = ("auto", "eastmoney", "sina")
ETF_MINUTE_NETWORK_MODES = ("system", "direct")
_VALID_SUFFIXES = {"SH", "SZ"}
_EASTMONEY_MINUTE_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
_EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_SINA_KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
)
_MANIFEST_SCHEMA_VERSION = "public.etf_minute.manifest.v1"
_RECEIPT_SCHEMA_VERSION = "public.etf_minute.receipt.v1"
_EASTMONEY_HOST = "push2his.eastmoney.com"
_SINA_HOST = "money.finance.sina.com.cn"
_SYNTHETIC_DNS_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@dataclass(frozen=True)
class EtfMinuteMirrorOptions:
    """Inputs for one public ETF minute mirror run."""

    symbols: tuple[str, ...]
    start_date: str
    end_date: str
    period: str = "1"
    source: str = "auto"
    artifacts_root: str | Path | None = None
    version: str | None = None
    output_dir: str | Path | None = None
    skip_existing: bool = True
    attempts: int = 3
    retry_delay: float = 1.0
    dry_run: bool = False
    network_mode: str = "system"


@dataclass(frozen=True)
class _EtfMinuteFetchRequest:
    ts_code: str
    start_date: str
    end_date: str
    period: str
    source: str
    network_mode: str
    attempts: int
    retry_delay: float


@dataclass(frozen=True)
class _RunMetadataContext:
    options: EtfMinuteMirrorOptions
    dates: list[str]
    skipped_dates: list[str]
    written_dates: list[str]
    empty_dates: list[str]
    failed_dates: list[str]
    source_by_symbol: dict[str, str]
    errors: dict[str, str]
    status: str


def normalize_ts_code(value: str) -> str:
    """Normalize a six-digit ETF code to the platform's ``ts_code`` form."""

    raw = str(value).strip().upper()
    if not raw:
        raise ValueError("ETF 代码不能为空")
    if "." in raw:
        code, suffix = raw.rsplit(".", 1)
        if suffix not in _VALID_SUFFIXES:
            raise ValueError(f"不支持的交易所后缀: {value}")
    else:
        code = raw
        suffix = "SH" if code.startswith(("5", "6")) else "SZ"
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"ETF 代码应为 6 位数字: {value!r}")
    return f"{code}.{suffix}"


def _normalize_symbol(ts_code: str) -> str:
    return ts_code.split(".", 1)[0]


def _eastmoney_market_id(symbol: str) -> str:
    return "1" if symbol.startswith(("5", "6")) else "0"


def _sina_market_symbol(symbol: str) -> str:
    market = "sh" if symbol.startswith(("5", "6")) else "sz"
    return f"{market}{symbol}"


def _validate_date(value: str) -> None:
    datetime.strptime(value, "%Y%m%d")


def normalize_etf_minute_frame(raw: pd.DataFrame | None, ts_code: str) -> pd.DataFrame:
    """Map public-source minute frames to the stable ETF minute schema."""

    normalized_code = normalize_ts_code(ts_code)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=pd.Index(ETF_MINUTE_COLUMNS))

    frame = raw.rename(
        columns={
            "时间": "trade_time",
            "day": "trade_time",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "vol",
            "volume": "vol",
            "成交额": "amount",
        }
    ).copy()
    if "trade_time" not in frame.columns:
        raise ValueError("分钟数据返回值缺少时间列")

    for column in ("open", "close", "high", "low", "vol", "amount"):
        if column not in frame.columns:
            frame[column] = pd.NA
        frame[column] = pd.to_numeric(
            frame[column].astype(str).str.replace(",", "", regex=False),
            errors="coerce",
        )

    frame["ts_code"] = normalized_code
    frame["trade_time"] = pd.to_datetime(frame["trade_time"], errors="coerce")
    result = frame[list(ETF_MINUTE_COLUMNS)].dropna(subset=["trade_time"])
    return (
        result.sort_values("trade_time")
        .drop_duplicates(subset=["ts_code", "trade_time"], keep="last")
        .reset_index(drop=True)
    )


def _filter_frame_to_range(
    frame: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    if frame.empty:
        return frame.reset_index(drop=True)
    start = pd.Timestamp(datetime.strptime(start_date, "%Y%m%d").replace(hour=9, minute=30))
    end = pd.Timestamp(datetime.strptime(end_date, "%Y%m%d").replace(hour=15))
    return frame.loc[frame["trade_time"].between(start, end)].reset_index(drop=True)


def _direct_subprocess_env() -> dict[str, str]:
    environment = os.environ.copy()
    for name in _PROXY_ENV_NAMES:
        environment.pop(name, None)
    environment["NO_PROXY"] = "*"
    environment["no_proxy"] = "*"
    return environment


def _proxy_environment_detected() -> bool:
    return any(os.environ.get(name) for name in _PROXY_ENV_NAMES)


def _curl_json(
    url: str,
    params: dict[str, str],
    *,
    network_mode: str = "system",
) -> Any:
    curl_path = shutil.which("curl")
    if curl_path is None:
        raise FileNotFoundError("未找到 curl；无法使用公共行情源直连回退路径")
    command = [
        curl_path,
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--noproxy",
        "*",
        "--connect-timeout",
        "15",
        "--max-time",
        "30",
        "--get",
        url,
    ]
    for key, value in params.items():
        command.extend(["--data-urlencode", f"{key}={value}"])
    run_kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "check": False,
    }
    if network_mode == "direct":
        run_kwargs["env"] = _direct_subprocess_env()
    result = subprocess.run(command, **run_kwargs)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ConnectionError(f"公共行情源 curl 请求失败（exit={result.returncode}）: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"公共行情源返回的 curl 响应不是 JSON: {result.stdout[:200]!r}") from exc


def _fetch_eastmoney_with_akshare(
    ts_code: str,
    start_date: str,
    end_date: str,
    *,
    period: str,
) -> pd.DataFrame:
    import akshare as ak  # ty: ignore[unresolved-import]

    return ak.fund_etf_hist_min_em(
        symbol=_normalize_symbol(ts_code),
        period=period,
        start_date=f"{start_date} 09:30:00",
        end_date=f"{end_date} 15:00:00",
    )


def _fetch_eastmoney_with_curl(
    ts_code: str,
    start_date: str,
    end_date: str,
    *,
    period: str,
    network_mode: str = "system",
) -> pd.DataFrame:
    symbol = _normalize_symbol(ts_code)
    secid = f"{_eastmoney_market_id(symbol)}.{symbol}"
    if period == "1":
        payload = _curl_json(
            _EASTMONEY_MINUTE_URL,
            {
                "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "ndays": "5",
                "iscr": "0",
                "secid": secid,
            },
            network_mode=network_mode,
        )
        rows_key = "trends"
        columns = ["时间", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "均价"]
    else:
        payload = _curl_json(
            _EASTMONEY_KLINE_URL,
            {
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "klt": period,
                "fqt": "0",
                "secid": secid,
                "beg": "0",
                "end": "20500000",
            },
            network_mode=network_mode,
        )
        rows_key = "klines"
        columns = [
            "时间",
            "开盘",
            "收盘",
            "最高",
            "最低",
            "成交量",
            "成交额",
            "振幅",
            "涨跌幅",
            "涨跌额",
            "换手率",
        ]
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get(rows_key) if isinstance(data, dict) else None
    if rows is None:
        if isinstance(data, dict) and data.get("total") == 0:
            return pd.DataFrame(columns=pd.Index(columns))
        raise ValueError(f"东方财富响应缺少 data.{rows_key}: {payload!r}")
    if not rows:
        return pd.DataFrame(columns=pd.Index(columns))
    split_rows = [str(row).split(",") for row in rows]
    if any(len(row) != len(columns) for row in split_rows):
        raise ValueError(f"东方财富 data.{rows_key} 列数异常")
    return pd.DataFrame(split_rows, columns=pd.Index(columns))


def _fetch_sina_with_curl(
    ts_code: str,
    start_date: str,
    end_date: str,
    *,
    period: str,
    network_mode: str = "system",
) -> pd.DataFrame:
    del start_date, end_date
    if period == "1":
        raise ValueError("新浪历史分钟接口不提供可用的 1 分钟回退")
    payload = _curl_json(
        _SINA_KLINE_URL,
        {
            "symbol": _sina_market_symbol(_normalize_symbol(ts_code)),
            "scale": period,
            "ma": "no",
            "datalen": "20000",
        },
        network_mode=network_mode,
    )
    if not isinstance(payload, list):
        raise TypeError(f"新浪历史分钟响应不是列表: {payload!r}")
    if not payload:
        return pd.DataFrame(columns=pd.Index(["day", "open", "close", "high", "low", "volume"]))
    if not all(isinstance(row, dict) for row in payload):
        raise ValueError("新浪历史分钟响应的行不是对象列表")
    return pd.DataFrame(payload)


def _validate_fetch_request(request: _EtfMinuteFetchRequest) -> _EtfMinuteFetchRequest:
    if request.period not in ETF_MINUTE_PERIODS:
        raise ValueError(f"不支持的 period={request.period!r}; 可选值: {list(ETF_MINUTE_PERIODS)}")
    if request.source not in ETF_MINUTE_SOURCES:
        raise ValueError(f"不支持的 source={request.source!r}; 可选值: {list(ETF_MINUTE_SOURCES)}")
    if request.network_mode not in ETF_MINUTE_NETWORK_MODES:
        raise ValueError(
            f"不支持的 network_mode={request.network_mode!r}; "
            f"可选值: {list(ETF_MINUTE_NETWORK_MODES)}"
        )
    if request.attempts < 1:
        raise ValueError("attempts 必须 >= 1")
    if request.retry_delay < 0:
        raise ValueError("retry_delay 必须 >= 0")
    normalized_code = normalize_ts_code(request.ts_code)
    _validate_date(request.start_date)
    _validate_date(request.end_date)
    if request.start_date > request.end_date:
        raise ValueError(f"start_date {request.start_date} 晚于 end_date {request.end_date}")
    if request.source == "sina" and request.period == "1":
        raise ValueError("新浪源不支持可用的 1 分钟历史数据")
    return replace(request, ts_code=normalized_code)


def _direct_network_preflight() -> None:
    for host in (_EASTMONEY_HOST, _SINA_HOST):
        try:
            addresses = {
                ipaddress.ip_address(info[4][0])
                for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            }
        except OSError as exc:
            raise RuntimeError(f"direct network DNS resolution failed for {host}: {exc}") from exc
        synthetic = sorted(
            str(address) for address in addresses if address in _SYNTHETIC_DNS_NETWORK
        )
        if synthetic:
            raise RuntimeError(
                f"direct network preflight detected synthetic DNS address for {host}: "
                f"{', '.join(synthetic)}; disable mihomo fake-IP/TUN or add a DIRECT route"
            )


def _fetch_sina_result(request: _EtfMinuteFetchRequest) -> tuple[pd.DataFrame, str]:
    raw = _fetch_sina_with_curl(
        request.ts_code,
        request.start_date,
        request.end_date,
        period=request.period,
        network_mode=request.network_mode,
    )
    frame = _filter_frame_to_range(
        normalize_etf_minute_frame(raw, request.ts_code),
        request.start_date,
        request.end_date,
    )
    return frame, "sina-curl"


def _try_eastmoney_akshare(
    request: _EtfMinuteFetchRequest,
) -> tuple[pd.DataFrame | None, Exception | None]:
    last_error: Exception | None = None
    for attempt in range(1, request.attempts + 1):
        try:
            raw = _fetch_eastmoney_with_akshare(
                request.ts_code,
                request.start_date,
                request.end_date,
                period=request.period,
            )
            frame = _filter_frame_to_range(
                normalize_etf_minute_frame(raw, request.ts_code),
                request.start_date,
                request.end_date,
            )
            return frame, None
        except Exception as exc:
            last_error = exc
            if attempt < request.attempts:
                time.sleep(request.retry_delay * attempt)
    return None, last_error


def _fetch_after_eastmoney_failure(
    request: _EtfMinuteFetchRequest,
    last_error: Exception | None,
) -> tuple[pd.DataFrame, str]:
    eastmoney_error: Exception | None = None
    try:
        raw = _fetch_eastmoney_with_curl(
            request.ts_code,
            request.start_date,
            request.end_date,
            period=request.period,
            network_mode=request.network_mode,
        )
        frame = _filter_frame_to_range(
            normalize_etf_minute_frame(raw, request.ts_code),
            request.start_date,
            request.end_date,
        )
        if request.period == "1" or not frame.empty or request.source == "eastmoney":
            return frame, "eastmoney-curl"
        eastmoney_error = RuntimeError("东方财富回退没有返回指定日期范围内的数据")
    except Exception as exc:
        eastmoney_error = exc

    if request.source == "eastmoney":
        raise RuntimeError(
            f"AKShare 请求失败: {type(last_error).__name__}: {last_error}; "
            f"东方财富 curl 回退失败: {type(eastmoney_error).__name__}: {eastmoney_error}"
        ) from eastmoney_error

    if request.period != "1":
        try:
            return _fetch_sina_result(request)
        except Exception as exc:
            raise RuntimeError(
                f"AKShare 请求失败: {type(last_error).__name__}: {last_error}; "
                f"东方财富 curl 回退失败: {type(eastmoney_error).__name__}: {eastmoney_error}; "
                f"新浪 curl 回退也失败: {type(exc).__name__}: {exc}"
            ) from exc

    raise RuntimeError(
        f"AKShare 请求失败: {type(last_error).__name__}: {last_error}; "
        f"东方财富 curl 回退失败: {type(eastmoney_error).__name__}: {eastmoney_error}"
    ) from eastmoney_error


def _fetch_direct_range(request: _EtfMinuteFetchRequest) -> tuple[pd.DataFrame, str]:
    if request.source == "sina":
        return _fetch_sina_result(request)
    try:
        raw = _fetch_eastmoney_with_curl(
            request.ts_code,
            request.start_date,
            request.end_date,
            period=request.period,
            network_mode=request.network_mode,
        )
        frame = _filter_frame_to_range(
            normalize_etf_minute_frame(raw, request.ts_code),
            request.start_date,
            request.end_date,
        )
        return frame, "eastmoney-curl"
    except Exception as eastmoney_error:
        if request.source == "eastmoney" or request.period == "1":
            raise RuntimeError(
                f"direct 东方财富请求失败: {type(eastmoney_error).__name__}: {eastmoney_error}"
            ) from eastmoney_error
        try:
            return _fetch_sina_result(request)
        except Exception as sina_error:
            raise RuntimeError(
                f"direct 东方财富请求失败: {type(eastmoney_error).__name__}: {eastmoney_error}; "
                f"direct 新浪回退失败: {type(sina_error).__name__}: {sina_error}"
            ) from sina_error


def fetch_etf_minute_range(  # noqa: PLR0913
    ts_code: str,
    start_date: str,
    end_date: str,
    *,
    period: str = "1",
    source: str = "auto",
    network_mode: str = "system",
    attempts: int = 3,
    retry_delay: float = 1.0,
) -> tuple[pd.DataFrame, str]:
    """Fetch one ETF/date range and return the frame plus selected source."""

    request = _validate_fetch_request(
        _EtfMinuteFetchRequest(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            period=period,
            source=source,
            network_mode=network_mode,
            attempts=attempts,
            retry_delay=retry_delay,
        )
    )

    if request.network_mode == "direct":
        _direct_network_preflight()
        return _fetch_direct_range(request)
    if request.source == "sina":
        return _fetch_sina_result(request)
    frame, last_error = _try_eastmoney_akshare(request)
    if frame is not None:
        return frame, "eastmoney-akshare"
    return _fetch_after_eastmoney_failure(request, last_error)


def _date_range(start_date: str, end_date: str) -> list[str]:
    current = date.fromisoformat(f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}")
    end = date.fromisoformat(f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}")
    result: list[str] = []
    while current <= end:
        result.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return result


def _validate_mirror_options(options: EtfMinuteMirrorOptions) -> EtfMinuteMirrorOptions:
    if not options.symbols:
        raise ValueError("至少需要一只 ETF")
    if options.period not in ETF_MINUTE_PERIODS:
        raise ValueError(f"不支持的 period={options.period!r}")
    if options.source not in ETF_MINUTE_SOURCES:
        raise ValueError(f"不支持的 source={options.source!r}")
    if options.network_mode not in ETF_MINUTE_NETWORK_MODES:
        raise ValueError(f"不支持的 network_mode={options.network_mode!r}")
    if options.attempts < 1:
        raise ValueError("attempts 必须 >= 1")
    if options.retry_delay < 0:
        raise ValueError("retry_delay 必须 >= 0")
    normalized_symbols = tuple(
        dict.fromkeys(normalize_ts_code(symbol) for symbol in options.symbols)
    )
    _validate_date(options.start_date)
    _validate_date(options.end_date)
    if options.start_date > options.end_date:
        raise ValueError(f"start_date {options.start_date} 晚于 end_date {options.end_date}")
    if options.source == "sina" and options.period == "1":
        raise ValueError("新浪源不支持可用的 1 分钟历史数据")
    if options.version is not None:
        version = options.version.strip()
        if not version or Path(version).name != version:
            raise ValueError("version 必须是单个非空目录名")
    return replace(options, symbols=normalized_symbols)


def _resolve_output_dir(options: EtfMinuteMirrorOptions) -> Path:
    if options.output_dir is not None:
        return Path(options.output_dir).expanduser().resolve()
    root = resolve_artifacts_root(options.artifacts_root)
    version = options.version or (
        f"etf_minute_{options.period}m_{options.start_date}_{options.end_date}"
    )
    return root / "assets" / "derived" / "a_share" / f"etf_minute_{options.period}m" / version


def _partition_path(output_dir: Path, trade_date: str) -> Path:
    return output_dir / f"trade_date={trade_date}" / "part-00000.parquet"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_etf_minute_partition(
    frame: pd.DataFrame,
    output_dir: Path,
    trade_date: str,
) -> Path | None:
    """Atomically write one normalized ETF minute partition."""

    if frame.empty:
        return None
    missing = [column for column in ETF_MINUTE_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"ETF minute partition is missing columns: {missing}")
    part_path = _partition_path(output_dir, trade_date)
    part_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = part_path.with_name(f".{part_path.name}.tmp")
    prepared = (
        frame.loc[:, list(ETF_MINUTE_COLUMNS)]
        .sort_values(["ts_code", "trade_time"])
        .reset_index(drop=True)
    )
    try:
        prepared.to_parquet(temporary_path, index=False, engine="pyarrow")
        temporary_path.replace(part_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return part_path


def _fetch_pending_frames(
    options: EtfMinuteMirrorOptions,
    pending_dates: list[str],
) -> tuple[list[pd.DataFrame], dict[str, str], dict[str, str]]:
    frames: list[pd.DataFrame] = []
    sources: dict[str, str] = {}
    errors: dict[str, str] = {}
    for symbol in options.symbols:
        try:
            frame, selected_source = fetch_etf_minute_range(
                symbol,
                min(pending_dates),
                max(pending_dates),
                period=options.period,
                source=options.source,
                network_mode=options.network_mode,
                attempts=options.attempts,
                retry_delay=options.retry_delay,
            )
            frames.append(frame)
            sources[symbol] = selected_source
        except Exception as exc:
            if isinstance(exc, ModuleNotFoundError) and exc.name == "akshare":
                raise
            errors[symbol] = f"{type(exc).__name__}: {exc}"
    return frames, sources, errors


def _write_pending_partitions(
    frames: list[pd.DataFrame],
    pending_dates: list[str],
    output_dir: Path,
) -> tuple[list[str], list[str]]:
    nonempty = [frame for frame in frames if not frame.empty]
    combined = pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame()
    written: list[str] = []
    empty: list[str] = []
    for trade_date in pending_dates:
        if combined.empty:
            day_frame = combined
        else:
            day_frame = combined.loc[
                combined["trade_time"].dt.strftime("%Y%m%d") == trade_date
            ].copy()
        if day_frame.empty:
            empty.append(trade_date)
            continue
        write_etf_minute_partition(day_frame, output_dir, trade_date)
        written.append(trade_date)
    return written, empty


def _partition_records(output_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(output_dir.glob("trade_date=*/part-*.parquet")):
        trade_date = path.parent.name.split("=", 1)[1]
        frame = pd.read_parquet(path, columns=["ts_code", "trade_time"])
        records.append(
            {
                "trade_date": trade_date,
                "path": path.relative_to(output_dir).as_posix(),
                "sha256": _sha256_file(path),
                "rows": int(len(frame)),
                "symbols": sorted(frame["ts_code"].astype(str).unique().tolist()),
                "time_min": frame["trade_time"].min().isoformat() if not frame.empty else None,
                "time_max": frame["trade_time"].max().isoformat() if not frame.empty else None,
            }
        )
    return records


def _atomic_write_text(path: Path, text: str) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        temporary_path.write_text(text, encoding="utf-8")
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _write_run_metadata(
    output_dir: Path,
    context: _RunMetadataContext,
) -> dict[str, Any]:
    options = context.options
    dates = context.dates
    skipped_dates = context.skipped_dates
    written_dates = context.written_dates
    empty_dates = context.empty_dates
    failed_dates = context.failed_dates
    source_by_symbol = context.source_by_symbol
    errors = context.errors
    status = context.status
    files = _partition_records(output_dir)
    manifest = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "dataset": f"etf_minute_{options.period}m",
        "market": "a_share",
        "instrument_type": "etf",
        "provider": "public",
        "status": status,
        "output_dir": str(output_dir),
        "query": {
            "symbols": list(options.symbols),
            "start_date": options.start_date,
            "end_date": options.end_date,
            "period": options.period,
            "source_requested": options.source,
            "network_mode": options.network_mode,
            "partition_by": "trade_date",
        },
        "network": {
            "mode": options.network_mode,
            "proxy_environment_detected": _proxy_environment_detected(),
        },
        "sources": sorted(set(source_by_symbol.values())),
        "source_by_symbol": source_by_symbol,
        "totals": {
            "rows": sum(int(item["rows"]) for item in files),
            "symbols": len({symbol for item in files for symbol in item["symbols"]}),
            "partitions": len(files),
            "dates_requested": len(dates),
            "dates_written": len(written_dates),
            "dates_skipped": len(skipped_dates),
            "dates_empty": len(empty_dates),
            "dates_failed": len(failed_dates),
        },
        "files": files,
        "empty_dates": empty_dates,
        "failed_dates": failed_dates,
        "errors": errors,
    }
    manifest_path = output_dir / "manifest.yml"
    _atomic_write_text(manifest_path, yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    receipt = {
        "schema_version": _RECEIPT_SCHEMA_VERSION,
        "status": status,
        "manifest_path": manifest_path.name,
        "manifest_sha256": _sha256_file(manifest_path),
        "dataset": manifest["dataset"],
        "query": manifest["query"],
        "network": manifest["network"],
        "sources": manifest["source_by_symbol"],
        "files": files,
        "empty_dates": empty_dates,
        "failed_dates": failed_dates,
        "errors": errors,
    }
    _atomic_write_text(
        output_dir / "receipt.json",
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
    )
    return {
        **manifest,
        "manifest_path": str(manifest_path),
        "receipt_path": str(output_dir / "receipt.json"),
    }


def mirror_public_etf_minute(
    options: EtfMinuteMirrorOptions,
) -> dict[str, Any]:
    """Mirror public ETF minute data into a versioned platform asset."""

    options = _validate_mirror_options(options)
    dates = _date_range(options.start_date, options.end_date)
    output_dir = _resolve_output_dir(options)
    output_dir.mkdir(parents=True, exist_ok=True)
    existing_dates = {
        path.parent.name.split("=", 1)[1] for path in output_dir.glob("trade_date=*/part-*.parquet")
    }
    pending_dates = [
        trade_date
        for trade_date in dates
        if not (options.skip_existing and trade_date in existing_dates)
    ]
    skipped_dates = [trade_date for trade_date in dates if trade_date not in pending_dates]
    if options.dry_run:
        return {
            "status": "planned",
            "output_dir": str(output_dir),
            "dates": dates,
            "pending_dates": pending_dates,
            "skipped_dates": skipped_dates,
            "symbols": list(options.symbols),
        }

    frames: list[pd.DataFrame] = []
    source_by_symbol: dict[str, str] = {}
    errors: dict[str, str] = {}
    written_dates: list[str] = []
    empty_dates: list[str] = []
    failed_dates: list[str] = []
    if pending_dates:
        frames, source_by_symbol, errors = _fetch_pending_frames(options, pending_dates)
        if errors:
            failed_dates = pending_dates
        else:
            written_dates, empty_dates = _write_pending_partitions(
                frames, pending_dates, output_dir
            )
    status = "failed" if failed_dates else "completed"
    return _write_run_metadata(
        output_dir,
        _RunMetadataContext(
            options=options,
            dates=dates,
            skipped_dates=skipped_dates,
            written_dates=written_dates,
            empty_dates=empty_dates,
            failed_dates=failed_dates,
            source_by_symbol=source_by_symbol,
            errors=errors,
            status=status,
        ),
    )


__all__ = [
    "ETF_MINUTE_COLUMNS",
    "ETF_MINUTE_PERIODS",
    "ETF_MINUTE_SOURCES",
    "ETF_MINUTE_NETWORK_MODES",
    "EtfMinuteMirrorOptions",
    "fetch_etf_minute_range",
    "mirror_public_etf_minute",
    "normalize_etf_minute_frame",
    "normalize_ts_code",
    "write_etf_minute_partition",
]
