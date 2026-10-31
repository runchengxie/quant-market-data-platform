#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Use controlled tool responses so the checks do not inherit host disk/git state.
source "$repo_root/scripts/dev/check_quality.sh"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT
mkdir "$test_root/bin"
cat > "$test_root/bin/git" <<'GIT'
#!/bin/sh
if [ "${QUALITY_TEST_IN_REPO:-0}" = 1 ]; then echo /foreign/source; exit 0; fi
exit 128
GIT
cat > "$test_root/bin/df" <<'DF'
#!/bin/sh
printf 'Filesystem 1024-blocks Used Available Capacity Mounted\nfixture 99999999 1 %s 1%% /\n' "${QUALITY_TEST_FREE_KIB:-50000000}"
DF
chmod +x "$test_root/bin/git" "$test_root/bin/df"
export PATH="$test_root/bin:$PATH"
workspace="$(prepare_quality_workspace "$test_root")"
[[ -d "$workspace" && "$workspace" == "$test_root"/marketdata-quality.* ]]
rmdir "$workspace"
for space in 0 20971519 invalid; do
    if QUALITY_TEST_FREE_KIB="$space" prepare_quality_workspace "$test_root" > /dev/null 2>&1; then
        echo "unsafe disk evidence accepted: $space" >&2; exit 1
    fi
done
workspace="$(QUALITY_TEST_FREE_KIB=20971520 prepare_quality_workspace "$test_root")"
rmdir "$workspace"
if QUALITY_TEST_IN_REPO=1 prepare_quality_workspace "$test_root" > /dev/null 2>&1; then
    echo 'foreign source repository accepted as temporary output' >&2; exit 1
fi
if prepare_quality_workspace "$test_root/missing" > /dev/null 2>&1; then
    echo 'missing temporary root accepted' >&2; exit 1
fi
[[ -z "$(find "$test_root" -maxdepth 1 -type d -name 'marketdata-quality.*' -print)" ]]
echo 'quality environment regressions passed'
# Exercise real entrypoint cleanup/failure propagation with tool-process doubles.
# The nested entrypoint skips this already-running regression script, not its gates.
for tool in bash ty; do
    printf '#!/bin/sh\nexit 0\n' > "$test_root/bin/$tool"
done
cat > "$test_root/bin/python" <<'PYTHON'
#!/bin/sh
case "$*" in *pip_audit*) touch "$QUALITY_TEST_AUDIT_MARKER";; esac
exit 0
PYTHON
cat > "$test_root/bin/uv" <<'UV'
#!/bin/sh
printf 'fixture==1\n'
exit "${QUALITY_TEST_EXPORT_STATUS:-0}"
UV
chmod +x "$test_root/bin/"{bash,ty,python,uv}
export QUALITY_TEST_AUDIT_MARKER="$test_root/audit-called"
TMPDIR="$test_root" "$BASH" "$QUALITY_REPO_ROOT/scripts/dev/check_quality.sh"
[[ -f "$QUALITY_TEST_AUDIT_MARKER" ]]
[[ -z "$(find "$test_root" -maxdepth 1 -type d -name 'marketdata-quality.*' -print)" ]]
rm "$QUALITY_TEST_AUDIT_MARKER"
if TMPDIR="$test_root" QUALITY_TEST_EXPORT_STATUS=53 "$BASH" "$QUALITY_REPO_ROOT/scripts/dev/check_quality.sh" > "$test_root/failure.log" 2>&1; then
    echo 'failed dependency export accepted' >&2; exit 1
else
    [[ "$?" == 53 ]]
fi
[[ ! -e "$QUALITY_TEST_AUDIT_MARKER" ]]
[[ "$(find "$test_root" -maxdepth 1 -type d -name 'marketdata-quality.*' | wc -l)" == 1 ]]
echo 'quality entrypoint cleanup and export-failure regressions passed'
