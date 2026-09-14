"""Owner-native universe and input-timing policy for DailyWatch20."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .daily_watch20_candidate_pool import CandidatePoolMode, candidate_pool_policy_id

UNIVERSE_POLICY_SCHEMA = "daily_watch20.universe_policy.v1"


@dataclass(frozen=True, slots=True)
class DailyWatch20UniversePolicy:
    candidate_pool_mode: CandidatePoolMode = "all_market"
    minute_lag_trade_days: int = 0
    ths_hot_min_symbols: int = 20
    ths_hot_snapshot_min_symbols: int = 80
    require_complete_sh_sz_minute_source: bool = True

    def __post_init__(self) -> None:
        if self.minute_lag_trade_days < 0:
            raise ValueError("minute_lag_trade_days must be non-negative")
        if self.ths_hot_min_symbols < 20:
            raise ValueError("ths_hot_min_symbols must be at least 20")
        if self.ths_hot_snapshot_min_symbols < self.ths_hot_min_symbols:
            raise ValueError("ths_hot_snapshot_min_symbols must cover ths_hot_min_symbols")
        candidate_pool_policy_id(
            self.candidate_pool_mode,
            ths_hot_min_symbols=self.ths_hot_min_symbols,
            ths_hot_snapshot_min_symbols=self.ths_hot_snapshot_min_symbols,
        )

    @property
    def candidate_pool_policy_id(self) -> str:
        return candidate_pool_policy_id(
            self.candidate_pool_mode,
            ths_hot_min_symbols=self.ths_hot_min_symbols,
            ths_hot_snapshot_min_symbols=self.ths_hot_snapshot_min_symbols,
        )

    @property
    def policy_id(self) -> str:
        payload = {"schema_version": UNIVERSE_POLICY_SCHEMA, **asdict(self)}
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        return f"{UNIVERSE_POLICY_SCHEMA}:{digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": UNIVERSE_POLICY_SCHEMA,
            "policy_id": self.policy_id,
            "candidate_pool_policy_id": self.candidate_pool_policy_id,
            **asdict(self),
        }


__all__ = ["UNIVERSE_POLICY_SCHEMA", "DailyWatch20UniversePolicy"]
