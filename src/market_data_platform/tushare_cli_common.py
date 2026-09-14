from __future__ import annotations

import argparse

DEFAULT_REQUEST_ATTEMPTS = 3
DEFAULT_RETRY_SLEEP_SECONDS = 2.0
DEFAULT_RETRY_MAX_SLEEP_SECONDS = 30.0
DEFAULT_QUOTA_COOLDOWN_SECONDS = 65.0
BACKFILL_DATASETS = ("daily", "adj_factor", "daily_basic", "limit_status")
BACKFILL_SEGMENTS = ("month", "year", "all")
FUNDAMENTALS_DATASETS = (
    "income",
    "balancesheet",
    "cashflow",
    "forecast",
    "express",
    "dividend",
    "fina_indicator",
    "fina_audit",
    "fina_mainbz",
    "disclosure_date",
)
ENTITLEMENT_MODES = ("vip_batch", "non_vip_fallback")


def add_token_env_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--token-env",
        default="TUSHARE_TOKEN",
        help="Environment variable containing the TuShare token (default: TUSHARE_TOKEN).",
    )
    parser.add_argument(
        "--api-url",
        help=(
            "Override the TuShare SDK API URL. Defaults to TUSHARE_API_URL_<suffix> "
            "matching --token-env, then TUSHARE_API_URL."
        ),
    )


def add_provider_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--use-proxy",
        action="store_true",
        help="Allow HTTP(S)/ALL proxy environment variables for TuShare API calls.",
    )
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=DEFAULT_REQUEST_ATTEMPTS,
        help=(
            "Attempts per TuShare request for transient errors "
            f"(default: {DEFAULT_REQUEST_ATTEMPTS})."
        ),
    )
    parser.add_argument(
        "--retry-sleep-seconds",
        type=float,
        default=DEFAULT_RETRY_SLEEP_SECONDS,
        help=(
            "Initial retry sleep for transient TuShare errors "
            f"(default: {DEFAULT_RETRY_SLEEP_SECONDS})."
        ),
    )
    parser.add_argument(
        "--retry-max-sleep-seconds",
        type=float,
        default=DEFAULT_RETRY_MAX_SLEEP_SECONDS,
        help=(
            "Maximum exponential retry sleep for transient TuShare errors "
            f"(default: {DEFAULT_RETRY_MAX_SLEEP_SECONDS})."
        ),
    )
    parser.add_argument(
        "--quota-cooldown-seconds",
        type=float,
        default=DEFAULT_QUOTA_COOLDOWN_SECONDS,
        help=(
            "Cooldown before retrying TuShare quota/rate-limit errors "
            f"(default: {DEFAULT_QUOTA_COOLDOWN_SECONDS})."
        ),
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        help="Maximum seconds allowed for one TuShare HTTP request.",
    )


def add_tushare_date_mirror_parser(
    subparsers: argparse._SubParsersAction,
    *,
    command: str,
    description: str,
) -> None:
    parser = subparsers.add_parser(command, help=description)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--fields", nargs="+")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--request-interval-seconds", type=float, default=0.0)
    add_token_env_argument(parser)
    add_provider_runtime_arguments(parser)


__all__ = [
    "add_tushare_date_mirror_parser",
    "add_token_env_argument",
    "add_provider_runtime_arguments",
    "DEFAULT_REQUEST_ATTEMPTS",
    "DEFAULT_RETRY_SLEEP_SECONDS",
    "DEFAULT_RETRY_MAX_SLEEP_SECONDS",
    "DEFAULT_QUOTA_COOLDOWN_SECONDS",
    "BACKFILL_DATASETS",
    "BACKFILL_SEGMENTS",
    "FUNDAMENTALS_DATASETS",
    "ENTITLEMENT_MODES",
]
