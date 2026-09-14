#!/usr/bin/env bash
set -euo pipefail

ROOT="${MARKET_DATA_ROOT:-${DATA_PLATFORM_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/market-data-platform}}"
DAILY_DIR="$ROOT/assets/tushare/a_share/daily"
INPUTS_DIR="$ROOT/assets/tushare/a_share/daily_clean_inputs"
REPORT_DIR="$ROOT/metadata/retention"
INVENTORY="$ROOT/metadata/lifecycle/inventory.json"
MARKETDATA_CLI="${MARKETDATA_CLI:-${MDP_DIR:-}/.venv/bin/marketdata}"

KEEP_DAILY_CLEAN_TOTAL="${KEEP_DAILY_CLEAN_TOTAL:-2}"
KEEP_DAILY_CLEAN_INPUTS_TOTAL="${KEEP_DAILY_CLEAN_INPUTS_TOTAL:-2}"

usage() {
  cat <<'EOF'
Usage:
  market_data_platform_retention.sh plan
  market_data_platform_retention.sh dry-run
  market_data_platform_retention.sh apply

Environment:
  MARKET_DATA_ROOT / DATA_PLATFORM_ROOT
  KEEP_DAILY_CLEAN_TOTAL            Default: 2
  KEEP_DAILY_CLEAN_INPUTS_TOTAL     Default: 2
  MARKETDATA_CLI                    marketdata executable

Only clearly versioned market-data-platform clean snapshots are eligible:
  assets/tushare/a_share/daily/a_share_all_20150101_YYYYMMDD_daily_clean
  assets/tushare/a_share/daily_clean_inputs/a_share_all_20150101_YYYYMMDD
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing command: $1" >&2
    exit 1
  }
}

human_size() {
  if command -v numfmt >/dev/null 2>&1; then
    numfmt --to=iec-i --suffix=B "$1"
  else
    printf '%sB' "$1"
  fi
}

dir_bytes() { du -sb -- "$1" | awk '{print $1}'; }
dir_file_count() { find "$1" -xdev -type f | wc -l; }

is_symlink_target() {
  local path="$1" parent target link resolved
  parent="$(dirname -- "$path")"
  target="$(readlink -f -- "$path")"
  while IFS= read -r -d '' link; do
    resolved="$(readlink -f -- "$link" 2>/dev/null || true)"
    [[ "$resolved" == "$target" ]] && return 0
  done < <(find "$parent" -maxdepth 1 -type l -print0 2>/dev/null)
  return 1
}

is_allowed_delete_path() {
  local path="$1" parent base
  parent="$(dirname -- "$path")"
  base="$(basename -- "$path")"
  [[ "$parent" == "$DAILY_DIR" && "$base" =~ ^a_share_all_20150101_[0-9]{8}_daily_clean$ ]] && return 0
  [[ "$parent" == "$INPUTS_DIR" && "$base" =~ ^a_share_all_20150101_[0-9]{8}$ ]] && return 0
  return 1
}

delete_dir() {
  local path="$1"
  [[ -d "$path" && ! -L "$path" ]] || { echo "refusing to delete non-directory or symlink: $path" >&2; exit 1; }
  is_allowed_delete_path "$path" || { echo "refusing to delete out-of-policy path: $path" >&2; exit 1; }
  rm -rf --one-file-system -- "$path"
}

report_line() {
  local action="$1" bytes="$2" files="$3" path="$4" reason="$5"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$action" "$bytes" "$(human_size "$bytes")" "$files" "$path" "$reason" | tee -a "$REPORT"
}

scan_versioned_dirs() {
  local parent="$1" regex="$2"; local -n out_ref="$3"; local path base
  out_ref=()
  [[ -d "$parent" ]] || return 0
  while IFS= read -r -d '' path; do
    base="$(basename -- "$path")"
    if [[ "$base" =~ $regex ]]; then
      out_ref+=("${BASH_REMATCH[1]}"$'\t'"$path")
    fi
  done < <(find "$parent" -mindepth 1 -maxdepth 1 -type d -print0)
}

process_group() {
  local label="$1" parent="$2" regex="$3" keep_total="$4" mode="$5"
  local -a candidates sorted; local line date path index bytes files reason
  scan_versioned_dirs "$parent" "$regex" candidates
  mapfile -t sorted < <(printf '%s\n' "${candidates[@]}" | sort -r)
  index=0
  for line in "${sorted[@]}"; do
    [[ -n "$line" ]] || continue
    date="${line%%	*}"; path="${line#*	}"
    bytes="$(dir_bytes "$path")"; files="$(dir_file_count "$path")"
    if (( index < keep_total )); then
      report_line "keep" "$bytes" "$files" "$path" "$label: keep date $date within newest $keep_total"
    elif is_symlink_target "$path"; then
      report_line "keep" "$bytes" "$files" "$path" "$label: protected because a sibling symlink targets it"
    else
      reason="$label: older than newest $keep_total"
      if [[ "$mode" == "apply" ]]; then
        report_line "delete" "$bytes" "$files" "$path" "$reason"
        delete_dir "$path"
      else
        report_line "would-delete" "$bytes" "$files" "$path" "$reason"
      fi
    fi
    index=$((index + 1))
  done
}

refresh_governance_report() {
  [[ -f "$INVENTORY" ]] || { echo "missing lifecycle inventory: $INVENTORY" >&2; return 1; }
  [[ -x "$MARKETDATA_CLI" ]] || { echo "marketdata CLI is not executable: $MARKETDATA_CLI" >&2; return 1; }
  local report="$REPORT_DIR/governance-$(date -u +%Y%m%dT%H%M%SZ).tsv"
  "$MARKETDATA_CLI" governance plan-retention \
    --artifacts-root "$ROOT" --inventory "$INVENTORY" --out "$report" \
    --latest-link "$REPORT_DIR/governance-latest.tsv"
  echo "governance_report: $report"
}

main() {
  local mode="${1:-plan}"
  case "$mode" in
    plan|dry-run) mode=dry-run ;;
    apply) ;;
    -h|--help|help) usage; return 0 ;;
    *) usage >&2; return 2 ;;
  esac
  need_cmd du; need_cmd find; need_cmd readlink; need_cmd sort
  [[ -d "$ROOT" ]] || { echo "missing market-data-platform root: $ROOT" >&2; return 1; }
  mkdir -p "$REPORT_DIR"
  REPORT="$REPORT_DIR/retention-$(date -u +%Y%m%dT%H%M%SZ).tsv"
  printf 'action\tbytes\thuman_size\tfiles\tpath\treason\n' > "$REPORT"
  process_group daily_clean "$DAILY_DIR" '^a_share_all_20150101_([0-9]{8})_daily_clean$' "$KEEP_DAILY_CLEAN_TOTAL" "$mode"
  process_group daily_clean_inputs "$INPUTS_DIR" '^a_share_all_20150101_([0-9]{8})$' "$KEEP_DAILY_CLEAN_INPUTS_TOTAL" "$mode"
  ln -sfn "$(basename -- "$REPORT")" "$REPORT_DIR/scheduled-latest.tsv"
  ln -sfn "$(basename -- "$REPORT")" "$REPORT_DIR/latest.tsv"
  echo "report: $REPORT"
  refresh_governance_report
}

main "$@"
