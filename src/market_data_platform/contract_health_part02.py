from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from market_data_platform.contract_health_part01 import (
    _mapping,
    _quality_gate_exit_code,
    render_current_contract_health_text,
)


def write_current_contract_health_report(
    payload: Mapping[str, Any],
    *,
    output: str | Path | None,
    output_format: str,
) -> None:
    rendered = (
        json.dumps(dict(payload), ensure_ascii=False, indent=2)
        if output_format == "json"
        else render_current_contract_health_text(payload)
    )
    if output is None:
        print(rendered, end="" if rendered.endswith("\n") else "\n")
        return
    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")


def current_contract_health_exit_code(payload: Mapping[str, Any]) -> int:
    return _quality_gate_exit_code(_mapping(payload.get("quality_verdict")))
