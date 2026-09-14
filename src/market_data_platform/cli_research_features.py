from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_frame(path: str | Path):
    import pandas as pd

    source = Path(path).expanduser()
    suffix = source.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(source)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(source)
    raise ValueError(f"Unsupported research feature input format: {source}")


def _write_frame(frame, path: str | Path) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        frame.to_parquet(target, index=False)
        return
    if suffix in {".csv", ".txt"}:
        frame.to_csv(target, index=False)
        return
    raise ValueError(f"Unsupported research feature output format: {target}")


def _write_json(payload: dict[str, Any], path: str | Path) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _handle_daily(args: argparse.Namespace) -> int:
    from market_data_platform.research_features import (
        build_daily_microstructure_features,
        feature_receipt,
    )

    source = _read_frame(args.input)
    output = build_daily_microstructure_features(
        source,
        symbol_col=args.symbol_col,
        time_col=args.time_col,
        high_col=args.high_col,
        low_col=args.low_col,
        close_col=args.close_col,
        amount_col=args.amount_col,
        window=args.window,
    )
    _write_frame(output, args.output)
    receipt = feature_receipt(
        output,
        feature_columns=[
            "parkinson_volatility",
            "corwin_schultz_spread",
            "amihud_illiquidity",
            "turnover_shock",
        ],
        source_contract=args.source_contract,
        asof=args.asof,
    )
    receipt.update(
        {
            "input": str(Path(args.input).expanduser()),
            "output": str(Path(args.output).expanduser()),
            "window": int(args.window),
        }
    )
    _write_json(receipt, args.receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
    return 0


def _handle_activity_bars(args: argparse.Namespace) -> int:
    from market_data_platform.research_features import BarBuildConfig, build_activity_bars

    source = _read_frame(args.input)
    config = BarBuildConfig(
        kind=args.kind,
        threshold=args.threshold,
        price_col=args.price_col,
        volume_col=args.volume_col,
        time_col=args.time_col,
        symbol_col=args.symbol_col,
    )
    output = build_activity_bars(source, config=config)
    _write_frame(output, args.output)
    receipt = {
        "schema_version": 1,
        "contract": "market_data_platform.activity_bars.v1",
        "kind": config.kind,
        "threshold": config.threshold,
        "input": str(Path(args.input).expanduser()),
        "output": str(Path(args.output).expanduser()),
        "rows": len(output),
        "symbols": int(output["symbol"].nunique()) if not output.empty else 0,
        "source_contract": args.source_contract,
        "asof": args.asof,
    }
    _write_json(receipt, args.receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
    return 0


def add_research_features_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "research-features",
        help="Build research-ready bars and low-frequency liquidity features",
    )
    commands = parser.add_subparsers(dest="research_feature_command", required=True)

    daily = commands.add_parser(
        "daily",
        help="Build daily OHLCV liquidity and microstructure proxy features",
    )
    daily.add_argument("--input", required=True)
    daily.add_argument("--output", required=True)
    daily.add_argument("--receipt", required=True)
    daily.add_argument("--source-contract", required=True)
    daily.add_argument("--asof", required=True)
    daily.add_argument("--window", type=int, default=20)
    daily.add_argument("--symbol-col", default="symbol")
    daily.add_argument("--time-col", default="trade_date")
    daily.add_argument("--high-col", default="high")
    daily.add_argument("--low-col", default="low")
    daily.add_argument("--close-col", default="close")
    daily.add_argument("--amount-col", default="amount")
    daily.set_defaults(handler=_handle_daily)

    bars = commands.add_parser(
        "activity-bars",
        help="Build tick, volume, or dollar bars from trade-level inputs",
    )
    bars.add_argument("--input", required=True)
    bars.add_argument("--output", required=True)
    bars.add_argument("--receipt", required=True)
    bars.add_argument("--source-contract", required=True)
    bars.add_argument("--asof", required=True)
    bars.add_argument("--kind", choices=("tick", "volume", "dollar"), required=True)
    bars.add_argument("--threshold", type=float, required=True)
    bars.add_argument("--symbol-col", default="symbol")
    bars.add_argument("--time-col", default="timestamp")
    bars.add_argument("--price-col", default="price")
    bars.add_argument("--volume-col", default="volume")
    bars.set_defaults(handler=_handle_activity_bars)


__all__ = ["add_research_features_parser"]
