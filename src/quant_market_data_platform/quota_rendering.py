"""Formatting helpers for provider quota status shown by data operations tools."""

from __future__ import annotations


def _format_bytes(value: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def _coerce_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _render_pct_bar(pct: float, width: int = 20) -> str:
    filled = 0 if pct <= 0 else width if pct >= 100 else round(width * pct / 100)
    return f"[{'#' * filled}{'-' * (width - filled)}] {pct:.2f}%"


def augment_quota_entry(entry: dict) -> dict:
    bytes_used = _coerce_float(entry.get("bytes_used"))
    bytes_limit = _coerce_float(entry.get("bytes_limit"))
    if bytes_used is None or bytes_limit is None or bytes_limit <= 0:
        return entry
    used_pct = min(bytes_used / bytes_limit * 100.0, 100.0)
    entry["bytes_remaining"] = max(bytes_limit - bytes_used, 0.0)
    entry["used_pct"] = round(used_pct, 2)
    entry["remaining_pct"] = round(max(0.0, 100.0 - used_pct), 2)
    return entry


def augment_quota_payload(payload):
    if isinstance(payload, dict):
        return augment_quota_entry(payload)
    if isinstance(payload, list):
        return [
            augment_quota_entry(entry) if isinstance(entry, dict) else entry for entry in payload
        ]
    return payload


def format_quota_entry(entry: dict, label: str | None = None) -> str:
    lines: list[str] = []
    if label:
        lines.append(label)
    for key in ("license_type", "remaining_days"):
        if key in entry:
            lines.append(f"{key}: {entry.get(key)}")

    values = (
        ("bytes_used", "bytes_used"),
        ("bytes_limit", "bytes_limit"),
        ("bytes_remaining", "bytes_remaining"),
    )
    for key, output_key in values:
        value = entry.get(key)
        numeric = _coerce_float(value)
        if numeric is not None:
            lines.append(f"{output_key}: {_format_bytes(numeric)} ({int(numeric)} B)")
        elif value is not None:
            lines.append(f"{output_key}: {value}")

    for key in ("used_pct", "remaining_pct"):
        value = entry.get(key)
        numeric = _coerce_float(value)
        if numeric is not None:
            lines.append(f"{key}: {numeric:.2f}%")
        elif value is not None:
            lines.append(f"{key}: {value}")
    used_pct = _coerce_float(entry.get("used_pct"))
    if used_pct is not None:
        lines.append(f"usage: {_render_pct_bar(used_pct)} used")
    return "\n".join(lines)


def format_quota_pretty(payload) -> str:
    if isinstance(payload, dict):
        return format_quota_entry(payload, label="Quota usage")
    if isinstance(payload, list):
        return "\n\n".join(
            format_quota_entry(entry, label=f"Quota usage #{idx}")
            if isinstance(entry, dict)
            else f"Quota usage #{idx}\n{entry}"
            for idx, entry in enumerate(payload, start=1)
        )
    return str(payload)


__all__ = [
    "augment_quota_entry",
    "augment_quota_payload",
    "format_quota_entry",
    "format_quota_pretty",
]
