"""Manifest builders for TuShare A-share provider mirrors."""

from __future__ import annotations

from typing import Any


def trade_date_manifest(
    context: dict[str, Any],
    partitions: dict[str, Any],
    request_policy: dict[str, Any],
) -> dict[str, Any]:
    written_dates = partitions["written_dates"]
    skipped_dates = partitions["skipped_dates"]
    empty_dates = partitions["empty_dates"]
    requested_fields = context["requested_fields"]
    manifest = {
        "schema_version": f"tushare.{context['api_name']}.v1",
        "dataset": context["dataset"],
        "market": context.get("market", "a_share"),
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(context["output_dir"]),
        "query": {
            "api": context["api_name"],
            "start_date": context["start"],
            "end_date": context["end"],
            "fields": list(requested_fields) if requested_fields else None,
            "partition_by": "trade_date",
            "request_interval_seconds": context["interval_seconds"],
            "query_options": context["query_options"],
        },
        "api_url": context["api_url"],
        "request_policy": request_policy,
        "totals": {
            "rows": partitions["rows"],
            "symbols": len(partitions["symbols"]),
            "trade_dates_requested": len(context["trade_dates"]),
            "trade_dates_written": len(written_dates),
            "trade_dates_skipped": len(skipped_dates),
            "trade_dates_empty": len(empty_dates),
            "files": len(written_dates),
        },
        "written_trade_dates": written_dates,
        "skipped_trade_dates": skipped_dates,
        "empty_trade_dates": empty_dates,
    }
    if context["dataset"] == "dc_concept_cons":
        date_completeness = {
            trade_date: partitions["date_completeness"][trade_date]
            for trade_date in context["trade_dates"]
            if trade_date in partitions["date_completeness"]
        }
        complete_dates = [
            trade_date
            for trade_date, receipt in date_completeness.items()
            if receipt.get("complete") is True
        ]
        incomplete_dates = [
            trade_date for trade_date in context["trade_dates"] if trade_date not in complete_dates
        ]
        complete = bool(context["trade_dates"]) and not incomplete_dates
        manifest["complete"] = complete
        manifest["completeness"] = {
            "complete": complete,
            "method": "offset_pagination_with_boundary_theme_repair_and_theme_code_population",
            "complete_trade_dates": complete_dates,
            "incomplete_trade_dates": incomplete_dates,
            "trade_dates": date_completeness,
        }
        manifest["totals"].update(
            {
                "pages": sum(
                    int(receipt.get("page_count") or 0) for receipt in date_completeness.values()
                ),
                "distinct_themes": len(partitions["themes"]),
                "complete_trade_dates": len(complete_dates),
                "incomplete_trade_dates": len(incomplete_dates),
            }
        )
    return manifest


def fund_portfolio_manifest(
    context: dict[str, Any],
    partitions: dict[str, Any],
    request_policy: dict[str, Any],
) -> dict[str, Any]:
    written_periods = partitions["written_periods"]
    skipped_periods = partitions["skipped_periods"]
    empty_periods = partitions["empty_periods"]
    requested_fields = context["requested_fields"]
    pages_by_period = partitions["pages_by_period"]
    return {
        "schema_version": "tushare.fund_portfolio.v1",
        "dataset": "fund_portfolio",
        "market": "a_share",
        "provider": "tushare",
        "status": "completed",
        "output_dir": str(context["output_dir"]),
        "retrieved_at": context.get("retrieved_at"),
        "vintage_id": context.get("vintage_id"),
        "query": {
            "api": "fund_portfolio",
            "start_date": context["start"],
            "end_date": context["end"],
            "fields": list(requested_fields) if requested_fields else None,
            "partition_by": "end_date",
        },
        "api_url": context["api_url"],
        "request_policy": request_policy,
        "totals": {
            "rows": partitions["rows"],
            "symbols": len(partitions["symbols"]),
            "funds": len(partitions["funds"]),
            "periods_requested": len(context["periods"]),
            "periods_written": len(written_periods),
            "periods_skipped": len(skipped_periods),
            "periods_empty": len(empty_periods),
            "pages": sum(pages_by_period.values()),
            "files": len(written_periods),
        },
        "page_size": context["page_size"],
        "pages_by_period": pages_by_period,
        "written_periods": written_periods,
        "skipped_periods": skipped_periods,
        "empty_periods": empty_periods,
    }
