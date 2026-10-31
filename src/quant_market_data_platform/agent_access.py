"""Bounded read-only summaries of existing published-asset checks."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .contract_health import ContractInspectionOptions, inspect_current_contract
from .published_assets import PublishedAssetContract, PublishedAssetContractError, PublishedAssetRef


def capabilities() -> dict[str, Any]:
    return {
        "schema": "quant.agent_capabilities.v1",
        "owner": "quant-market-data-platform",
        "permission_grant": False,
        "operations": [
            {
                "id": f"contract.{name}",
                "owner": "quant-market-data-platform",
                "contract_version": 1,
                "description": description,
                "permission": "read",
                "side_effects": [],
                "retry": "read_again",
                "invocation": f"marketdata contract {name}"
                + (" --format json" if name == "inspect" else ""),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "artifacts_root": {"type": "string"},
                        "current_contract": {"type": "string"},
                        "market": {"type": "string", "enum": ["a_share"]},
                        "target_date": {"type": "string", "description": "YYYYMMDD"},
                        "require_start_date": {"type": "string", "description": "YYYYMMDD"},
                        "asset": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 50,
                        },
                    },
                    "required": ["asset"] if name != "inspect" else [],
                },
                "output_schema": {"type": "object"},
                "output_contract": output,
                "preconditions": ["authorized access to published metadata"],
                "examples": [
                    f"marketdata contract {name} --artifacts-root /tmp/synthetic --asset daily"
                    + (" --format json" if name == "inspect" else "")
                ],
                "limitations": [
                    "No data publication or provider calls",
                    "Metadata identity does not establish data integrity or historical PIT",
                    "inspect --out explicitly writes a report; omit it for read-only access",
                ],
            }
            for name, description, output in (
                (
                    "inspect",
                    "Inspect current publication health with existing severity policy",
                    "current_contract_health",
                ),
                ("capture", "Pin and recheck manifest identities", "marketdata.asset-snapshot.v1"),
                (
                    "assess",
                    "Read scoped trust dimensions; absent evidence stays unknown",
                    "quant.data_assessment.v1",
                ),
            )
        ],
    }


def _check(
    key: str, property_name: str, outcome: str, condition: tuple[Any, str], evidence: dict[str, Any]
) -> dict[str, Any]:
    observed, expected = condition
    return {
        "id": f"{key}:{property_name}",
        "asset_key": key,
        "scope": property_name,
        "outcome": outcome,
        "observed": observed,
        "expected": expected,
        "evidence_ref": evidence,
    }


def assess(args: argparse.Namespace) -> dict[str, Any]:
    keys = list(dict.fromkeys(args.asset))
    if not 1 <= len(keys) <= 50:
        raise ValueError("Select between 1 and 50 unique assets")
    contract = (
        PublishedAssetContract.load(args.current_contract, artifacts_root=args.artifacts_root)
        if args.current_contract
        else PublishedAssetContract.load_current(args.artifacts_root, market=args.market)
    )
    snapshot = contract.capture(keys)
    report = inspect_current_contract(
        ContractInspectionOptions(
            artifacts_root=args.artifacts_root,
            market=args.market,
            current_contract=str(contract.path),
            target_date=args.target_date,
            required_start_date=args.require_start_date,
            assets=keys,
        )
    )
    checks = []
    for ref in snapshot.assets:
        checks.extend(_asset_checks(ref, report, args, snapshot.contract_sha256))
    for issue in report["quality_checks"]:
        checks.append(
            _check(
                str(issue["asset_key"]),
                str(issue["check"]),
                "failed",
                (issue, "existing owner quality check satisfied"),
                {"kind": "contract_sha256", "sha256": snapshot.contract_sha256},
            )
        )
    snapshot.validate_metadata()
    current = PublishedAssetContract.load(
        contract.path, artifacts_root=args.artifacts_root
    ).capture(keys)
    if current.provenance_dict() != snapshot.provenance_dict():
        raise PublishedAssetContractError("Publication changed during assessment")
    return {
        "schema": "quant.data_assessment.v1",
        "research_ready": False,
        "snapshot": snapshot.provenance_dict(),
        "checks": checks,
        "legacy_quality_verdict": report["quality_verdict"],
        "limitations": [
            "Unknown trust dimensions require explicit research validation",
            "No economic or promotion approval",
        ],
    }


def _asset_checks(
    ref: PublishedAssetRef,
    report: dict[str, Any],
    args: argparse.Namespace,
    contract_sha256: str,
) -> list[dict[str, Any]]:
    checks = []
    evidence = {
        "kind": "publication_metadata",
        "manifest_sha256": ref.manifest_sha256,
        "contract_sha256": contract_sha256,
    }
    status = ref.manifest.get("status")
    record = report["assets"][ref.key]
    freshness_issues = {
        "asset_as_of_missing_for_target_date",
        "asset_as_of_lagging_target_date",
    }
    failed = any(
        issue["asset_key"] == ref.key and issue["check"] in freshness_issues
        for issue in report["quality_checks"]
    )
    outcome = (
        "unknown"
        if not args.target_date or not record.get("as_of") or not record.get("exists")
        else ("failed" if failed else "passed")
    )
    checks.append(
        _check(
            ref.key,
            "freshness",
            outcome,
            (
                record.get("as_of"),
                f"publication as_of meets {args.target_date or 'an explicit target date'}",
            ),
            evidence,
        )
    )
    checks.extend(
        [
            _check(
                ref.key,
                "manifest_identity",
                "passed",
                (ref.manifest_sha256, "captured manifest remains unchanged"),
                evidence,
            ),
            _check(
                ref.key,
                "publication_complete",
                "passed" if status == "completed" else "unknown" if status is None else "failed",
                (status, "completed"),
                evidence,
            ),
            _check(
                ref.key,
                "data_content_integrity",
                "unknown",
                (None, "independent verification of data bytes required"),
                evidence,
            ),
            _check(
                ref.key,
                "historical_pit",
                "unknown",
                (None, "research-window-specific observation/disclosure evidence required"),
                evidence,
            ),
            _check(
                ref.key,
                "contiguous_coverage",
                "unknown",
                (None, "per-session coverage evidence required; bounds alone are insufficient"),
                evidence,
            ),
        ]
    )

    return checks


def handle_capabilities(_args: argparse.Namespace) -> int:
    print(json.dumps(capabilities(), sort_keys=True))
    return 0


def handle_assess(args: argparse.Namespace) -> int:
    payload = assess(args)
    print(json.dumps(payload, sort_keys=True))
    return int(
        any(
            check["outcome"] == "failed" and check["scope"] == "publication_complete"
            for check in payload["checks"]
        )
    )
