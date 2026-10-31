#!/usr/bin/env bash
# One locked-environment gate used by both contributors and public CI.
set -euo pipefail
QUALITY_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

prepare_quality_workspace() {
    local base="$1" workspace available
    if [[ ! -d "$base" || ! -w "$base" ]]; then
        echo 'TMPDIR must be an existing writable directory outside source repositories.' >&2
        return 2
    fi
    workspace="$(mktemp -d "$base/marketdata-quality.XXXXXXXX")" || return 2
    if [[ -n "$(git -C "$workspace" rev-parse --show-toplevel 2>/dev/null)" ]]; then
        echo 'TMPDIR is inside a source repository; choose an external temporary root.' >&2
        rmdir "$workspace"
        return 2
    fi
    available="$(LC_ALL=C df -Pk "$workspace" | awk 'NR == 2 {print $4}')" || {
        rmdir "$workspace"; return 2;
    }
    # Offline-equities integration fixtures retain their production 20 GiB guard.
    if [[ ! "$available" =~ ^[0-9]+$ ]] || (( available < 20 * 1024 * 1024 )); then
        echo 'TMPDIR needs at least 20 GiB available for offline-equities fixtures.' >&2
        rmdir "$workspace"
        return 2
    fi
    printf '%s\n' "$workspace"
}

main() {
    cd "$QUALITY_REPO_ROOT"
    local quality_workspace
    quality_workspace="$(prepare_quality_workspace "${TMPDIR:-/tmp}")"
    export TMPDIR="$quality_workspace"
    trap 'status=$?; if (( status == 0 )); then rm -rf -- "$TMPDIR"; else printf "Quality check failed; temporary evidence retained: %s\n" "$TMPDIR" >&2; fi' EXIT
    bash tests/test_quality_environment.sh
    python scripts/dev/audit_public_snapshot.py . --exclude-file scripts/dev/public_snapshot_excludes.txt
    python -m research_code_quality.scanner --root . --scope src --scope scripts --scope tests \
        --baseline config/code-quality-baseline.json --ratchet --json
    python -m ruff check .
    python -m ruff format --check .
    ty check --error-on-warning
    python scripts/dev/quality_debt.py --skip-ruff --complexity --check-baseline --check-ratchet
    python scripts/dev/maintainability_metrics.py --check-baseline
    python scripts/dev/compatibility_governance.py --check
    python scripts/dev/architecture_governance.py --check
    python scripts/dev/run_pytest_isolated.py -- -q --basetemp "$TMPDIR/pytest"
    uv export --locked --all-extras --format requirements-txt --no-hashes \
        --no-emit-project --no-emit-local --no-emit-package research-code-quality \
        > "$TMPDIR/requirements.txt"
    python -m pip_audit --strict --no-deps --disable-pip -r "$TMPDIR/requirements.txt"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
