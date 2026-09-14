from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

CoverageTier = Literal[
    "annual_full_sh_sz",
    "deal_full_sh_sz",
    "tushare_full_a_share",
    "guan_partial_session",
    "tushare_partial_top200",
    "missing",
]

ANNUAL_FULL_SH_SZ = "annual_full_sh_sz"
DEAL_FULL_SH_SZ = "deal_full_sh_sz"
TUSHARE_FULL_A_SHARE = "tushare_full_a_share"
GUAN_PARTIAL_SESSION = "guan_partial_session"
TUSHARE_PARTIAL_TOP200 = "tushare_partial_top200"
MISSING = "missing"

_ANNUAL_FULL_TIME_MAX = "15:00:00"
_ANNUAL_PARTIAL_TIME_MAX = "14:57:00"
_DEAL_FULL_TIME_MIN = "09:30:00"
_TUSHARE_FULL_TIME_MIN = "09:30:00"
_TUSHARE_FULL_BARS_PER_SYMBOL = 241

_PARTITION_PATTERN = re.compile(r"trade_date=(\d{8})$")
_DEAL_PATTERN = re.compile(r"deal_(\d{8})\.parquet$", re.IGNORECASE)
_TUSHARE_BATCH_PATTERN = re.compile(r"minute_(\d{8})_batch\d+\.parquet$", re.IGNORECASE)
_TS_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_SH_SZ_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")

TUSHARE_FULL_DAY_PLAN_SCHEMA_VERSION = "a_share.minute_tushare_full_day_plan.v1"
TUSHARE_FULL_DAY_RECEIPT_SCHEMA_VERSION = "a_share.minute_tushare_full_day_receipt.v2"
TushareFullDayPhase = Literal["pilot", "production"]


@dataclass(frozen=True)
class CoverageRequirements:
    """Dataset-level gates for one coverage audit."""

    expected_trade_dates: int | None = None
    expected_annual_full_sh_sz_dates: int | None = None
    expected_deal_full_sh_sz_dates: int | None = None
    expected_tushare_full_a_share_dates: int | None = None
    expected_guan_partial_session_dates: int | None = None
    expected_partial_top200_dates: int | None = None
    expected_accepted_zero_volume_nonzero_amount_rows: int | None = None
    expected_accepted_positive_volume_zero_amount_rows: int | None = None
    require_full_source_stats: bool = False


PRODUCTION_COVERAGE_REQUIREMENTS = CoverageRequirements(
    expected_trade_dates=2_556,
    expected_annual_full_sh_sz_dates=2_430,
    expected_deal_full_sh_sz_dates=37,
    expected_guan_partial_session_dates=58,
    expected_partial_top200_dates=28,
    expected_accepted_zero_volume_nonzero_amount_rows=136_246,
    expected_accepted_positive_volume_zero_amount_rows=1,
    require_full_source_stats=True,
)


@dataclass(frozen=True)
class ExpectedPartitionStats:
    """Exact source-to-canonical reconciliation values for a full Guan date."""

    rows: int
    symbols: int
    traded_symbols: int | None = None
    vol_sum: float | None = None
    amount_sum: float | None = None
    unit_profile: str | None = None
    time_min: str | None = None
    time_max: str | None = None
    output_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in ("rows", "symbols"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.traded_symbols is not None and self.traded_symbols < 1:
            raise ValueError("traded_symbols must be positive when provided")
        for name in ("vol_sum", "amount_sum"):
            value = getattr(self, name)
            if value is not None and (not np.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and non-negative when provided")
        for name in ("time_min", "time_max"):
            value = getattr(self, name)
            if value is not None:
                pd.to_datetime(value, errors="raise")
        if (
            self.output_sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", self.output_sha256) is None
        ):
            raise ValueError("output_sha256 must be a lowercase SHA-256 digest when provided")


@dataclass(frozen=True)
class AnnualMinbarCoverageResult:
    """Coverage facts extracted from an annual minbar build manifest.

    Iteration intentionally yields the historical ``(dates, stats)`` pair so
    callers can migrate to the explicit session sets without a flag day.
    """

    dates: set[str]
    stats: dict[str, ExpectedPartitionStats]
    full_dates: set[str]
    partial_session_dates: set[str]

    def __iter__(self) -> Iterator[Any]:
        yield self.dates
        yield self.stats


@dataclass(frozen=True)
class GuanDealCoverageResult:
    """Coverage facts extracted from a Guan deal build manifest."""

    dates: set[str]
    stats: dict[str, ExpectedPartitionStats]
    override_dates: set[str]

    def __iter__(self) -> Iterator[Any]:
        yield self.dates
        yield self.stats


@dataclass(frozen=True)
class MarketReferenceStats:
    """Independent daily breadth reference, normally from TuShare daily clean."""

    sh_sz_symbols: int
    bj_symbols: int = 0

    def __post_init__(self) -> None:
        if self.sh_sz_symbols < 0 or self.bj_symbols < 0:
            raise ValueError("Market reference symbol counts must be non-negative")


@dataclass(frozen=True)
class TushareFullDayPlan:
    """Explicit whole-day replacement plan for trusted TuShare mirrors."""

    phase: TushareFullDayPhase
    dates: tuple[str, ...]
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.phase not in {"pilot", "production"}:
            raise ValueError(f"Unsupported TuShare full-day phase: {self.phase!r}")
        normalized = tuple(_validate_date(value) for value in self.dates)
        if not normalized:
            raise ValueError("TuShare full-day plan must contain at least one date")
        if len(set(normalized)) != len(normalized):
            raise ValueError("TuShare full-day plan dates must be unique")
        if normalized != tuple(sorted(normalized)):
            raise ValueError("TuShare full-day plan dates must be sorted")
        if self.sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ValueError("TuShare full-day plan sha256 must be a lowercase SHA-256 digest")
        if self.phase == "production" and self.sha256 is None:
            raise ValueError("Production TuShare full-day plans must be bound to plan bytes")
        object.__setattr__(self, "dates", normalized)


@dataclass(frozen=True)
class DailyCoverageExpectation:
    """The canonical source decision for one exchange trading date."""

    trade_date: str
    tier: CoverageTier
    canonical_source: str | None
    market_scope: str
    audit_only_sources: tuple[str, ...]
    overlay_sources: tuple[str, ...] = ()
    expected_rows: int | None = None
    expected_symbols: int | None = None

    @property
    def is_full_sh_sz(self) -> bool:
        return self.tier in {ANNUAL_FULL_SH_SZ, DEAL_FULL_SH_SZ, TUSHARE_FULL_A_SHARE}

    @property
    def is_full_a_share(self) -> bool:
        """Whether the partition covers the point-in-time A-share market."""
        return self.market_scope == "SH_SZ_BJ" or (
            self.trade_date < "20211115" and self.is_full_sh_sz
        )

    @property
    def has_guan_source(self) -> bool:
        return self.tier in {
            ANNUAL_FULL_SH_SZ,
            DEAL_FULL_SH_SZ,
            GUAN_PARTIAL_SESSION,
        }


def _validate_date(value: str) -> str:
    text = str(value).strip()
    if not re.fullmatch(r"\d{8}", text):
        raise ValueError(f"Expected YYYYMMDD date, got {value!r}")
    pd.to_datetime(text, format="%Y%m%d", errors="raise")
    return text


def _date_set(values: Iterable[str]) -> set[str]:
    return {_validate_date(value) for value in values}


@dataclass(frozen=True)
class _CoverageSourceDates:
    annual_full: frozenset[str]
    annual_partial: frozenset[str]
    deal: frozenset[str]
    deal_overrides: frozenset[str]
    tushare: frozenset[str]
    tushare_full: frozenset[str]


@dataclass(frozen=True)
class _CoverageClassificationRequest:
    trade_dates: Iterable[str]
    annual_dates: Iterable[str]
    deal_dates: Iterable[str]
    tushare_dates: Iterable[str]
    partial_session_annual_dates: Iterable[str] = ()
    deal_override_dates: Iterable[str] = ()
    tushare_full_dates: Iterable[str] = ()
    partial_symbols: int = 200
    partial_bars_per_symbol: int = 241


def apply_bj_overlay_coverage(
    expectations: Sequence[DailyCoverageExpectation],
    *,
    bj_overlay_dates: Iterable[str],
    tushare_full_dates: Iterable[str] = (),
) -> list[DailyCoverageExpectation]:
    """Annotate complete Guan tiers with a disjoint, canonical BJ component."""
    bj_overlay = _date_set(bj_overlay_dates)
    tushare_full = _date_set(tushare_full_dates)
    duplicate_bj_coverage = sorted(tushare_full & bj_overlay)
    if duplicate_bj_coverage:
        raise ValueError(
            "TuShare full-A dates already contain BJ and cannot also be BJ overlays: "
            f"{duplicate_bj_coverage}"
        )
    result: list[DailyCoverageExpectation] = []
    for expectation in expectations:
        if expectation.trade_date not in bj_overlay:
            result.append(expectation)
            continue
        if expectation.tier not in {ANNUAL_FULL_SH_SZ, DEAL_FULL_SH_SZ}:
            raise ValueError(
                "BJ overlay may annotate only complete Guan annual/deal dates: "
                f"{expectation.trade_date}={expectation.tier}"
            )
        result.append(
            replace(
                expectation,
                market_scope="SH_SZ_BJ",
                overlay_sources=("tushare_bj_overlay",),
            )
        )
    return result


def _partition_path(root: Path, trade_date: str) -> Path:
    return root / f"trade_date={trade_date}" / "part-00000.parquet"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sum_issue_maps(*values: Mapping[str, int]) -> dict[str, int]:
    names = set().union(*(value.keys() for value in values))
    return {
        name: sum(int(value.get(name, 0)) for value in values)
        for name in names
        if sum(int(value.get(name, 0)) for value in values)
    }


def _atomic_write_json(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
