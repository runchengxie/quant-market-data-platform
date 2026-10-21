"""Expose independent acquisition, audit, coverage and publication evidence."""

from typing import Any


def pipeline(progress: dict[str, Any]) -> dict[str, Any]:
    publication = progress["published_asset"]
    return {
        "acquisition": {
            "scope": "reverse_history_staging",
            "metadata_complete_dates": progress["metadata_complete_dates"],
            "accepted_bj_omission_dates": progress["accepted_bj_omission_dates"],
            "settled_dates": progress["settled_dates"],
            "remaining_open_dates": progress["remaining_open_dates"],
            "deferred_dates": progress["deferred_dates"],
        },
        "semantic_audit": {
            "scope": "reverse_history_staging",
            "audited_dates": progress["independently_audited_dates"],
            "scope_dates": progress["audit_scope_dates"],
            "unresolved_expired_dates": progress["unresolved_expired_audit_dates"],
            "quarantined_dates": progress["semantic_quarantine_dates"],
        },
        "coverage": {
            "scope": "reverse_history_staging",
            "full_market_audited_dates": progress["audit_scope_dates"]["passed_full_market"],
            "full_market_ready": progress["full_market_ready"],
        },
        "publication": {
            "scope": "current_published_asset",
            "evidence": "validated_operational_receipt" if publication else "unavailable",
            **dict.fromkeys(("date_min", "date_max", "dates", "rows", "market_scope")),
            **publication,
        },
    }
