"""Show asset dates, quality gaps and service incidents without repairing them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quant_market_data_platform.operations_overview import OverviewOptions, build_overview


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--expected-date", required=True)
    parser.add_argument("--production-root", type=Path)
    parser.add_argument("--with-systemd", action="store_true")
    parser.add_argument("--max-age-hours", type=float, default=6)
    parser.add_argument("--evidence", action="append", default=[], metavar="NAME=PATH")
    args = parser.parse_args()
    evidence = {}
    for item in args.evidence:
        name, separator, path = item.partition("=")
        if not separator or not name or not path or name in evidence:
            parser.error("evidence requires a unique NAME=PATH")
        evidence[name] = Path(path)
    try:
        report = build_overview(
            args.root,
            args.expected_date,
            options=OverviewOptions(
                max_age_hours=args.max_age_hours,
                production_root=args.production_root,
                with_systemd=args.with_systemd,
                evidence=evidence,
            ),
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
