"""Advance a staged TuShare minute replacement campaign without publishing it.

This module is now a thin re-export surface.  The implementation was split
into the sibling ``_campaign_*`` submodules to keep individual files and
functions within the local complexity budget.  All previously public and
internal names remain importable from this path so that existing callers
(CLI, reconcile helper, and tests) are unaffected.
"""

from __future__ import annotations

import signal
import subprocess
import time
from zoneinfo import ZoneInfo

import market_data_platform._campaign_common as _common
from market_data_platform._campaign_advance import (
    _advance_one_day,
    _advance_with_context,
    _phase_result_after_run,
    _run_pending_phase,
    _select_execution_phase,
    _single_lane_stop_check,
)
from market_data_platform._campaign_common import (
    COMPLETENESS_FILENAME,
    EXPECTED_CHECKPOINT_EXIT_CODES,
    FATAL_EXIT_CODE,
    NO_PROGRESS_REASONS,
    READINESS_SCHEMA_VERSION,
    RETRYABLE_EXIT_CODE,
    SCHEMA_VERSION,
    AdvanceState,
    CampaignAccountingError,
    CampaignFatalError,
    CampaignRetryableError,
    PartitionKey,
)
from market_data_platform._campaign_preflight import (
    _finish_preflight_attempt,
    _preflight_exit_reason,
    _record_completed_preflight,
    _resume_preflight,
    _run_preflight_attempt,
    _write_preflight_entry,
)
from market_data_platform._campaign_progress import (
    _campaign_partition_keys,
    _CampaignProgress,
    _checkpoint_rows,
    _partition_key,
    _phase_partition_keys,
    _progress_stop_check,
)
from market_data_platform._campaign_readiness import (
    _campaign_locations,
    _checkpoint_inventory,
    _readiness_path,
    _validated_readiness,
    _write_readiness_marker,
)
from market_data_platform._campaign_receipts import (
    _date_receipt,
    _load_lane_receipt,
    _partition_shared_quota_stop_reason,
    _phase_receipts,
    _receipt_output_is_staging_only,
    _shared_quota_stop_reason,
    _validate_completed_phase_receipts,
    _validate_phase_outcome,
    _validated_acquisition_date,
    _validated_date,
)
from market_data_platform._campaign_run import (
    _bind_quota_window,
    _CampaignRunContext,
    _check_campaign_blockers,
    _configure_budget,
    _configure_run_window,
    _dispatch_campaign_outcome,
    _finalize_campaign_run,
    _finish_measurement,
    _finished_event,
    _finished_health,
    _no_progress_fuse_gate,
    _poll_campaign_stop,
    _prepare_campaign_run,
    _runtime_campaign_config,
    _start_campaign_run,
    _update_quota_high_water,
    run_budgeted,
    run_next,
)
from market_data_platform._campaign_status import (
    _campaign_status_payload,
    _heartbeat_age,
    _print_campaign_status,
    _recent_throughput_samples,
    _status_eta,
    _status_health,
    _status_run_window,
    campaign_status,
)
from market_data_platform._campaign_supervisor import (
    _active_blockers,
    _combined_stop_check,
    _CommandSupervisor,
    _dry_run_commands,
    _lane_command,
    _lock_is_held,
    _resume_command,
    _run_commands,
    _run_phase,
    _stop_started_children,
)

# Re-export the dataclasses and helpers that live in _campaign_common so that
# attribute access through this module keeps working exactly as before.
_AdvanceContext = _common._AdvanceContext
_AdvanceControls = _common._AdvanceControls
_AdvanceResult = _common._AdvanceResult
_CommandRunOptions = _common._CommandRunOptions
_FinishMeasurement = _common._FinishMeasurement
_PhaseExecution = _common._PhaseExecution
_PreflightAttempt = _common._PreflightAttempt
_PhaseRunResult = _common._PhaseRunResult
_RunWindow = _common._RunWindow
_now = _common._now
_read_json = _common._read_json
_write_ledger = _common._write_ledger
_atomic_write_json = _common._atomic_write_json
_build_run_window = _common._build_run_window
_parse_clock = _common._parse_clock
_quota_window_clock = _common._quota_window_clock
_minute_quota_args = _common._minute_quota_args
_sha256 = _common._sha256
_readiness_summary_is_valid = _common._readiness_summary_is_valid
_load_ledger = _common._load_ledger

__all__ = [
    "READINESS_SCHEMA_VERSION",
    "RETRYABLE_EXIT_CODE",
    "SCHEMA_VERSION",
    "signal",
    "subprocess",
    "time",
    "CampaignAccountingError",
    "CampaignFatalError",
    "CampaignRetryableError",
    "COMPLETENESS_FILENAME",
    "EXPECTED_CHECKPOINT_EXIT_CODES",
    "FATAL_EXIT_CODE",
    "NO_PROGRESS_REASONS",
    "PartitionKey",
    "AdvanceState",
    "ZoneInfo",
    "_AdvanceContext",
    "_AdvanceControls",
    "_AdvanceResult",
    "_CampaignProgress",
    "_CampaignRunContext",
    "_CommandRunOptions",
    "_CommandSupervisor",
    "_FinishMeasurement",
    "_PhaseExecution",
    "_PhaseRunResult",
    "_PreflightAttempt",
    "_RunWindow",
    "_atomic_write_json",
    "_advance_one_day",
    "_advance_with_context",
    "_bind_quota_window",
    "_build_run_window",
    "_campaign_locations",
    "_campaign_partition_keys",
    "_campaign_status_payload",
    "_check_campaign_blockers",
    "_checkpoint_inventory",
    "_checkpoint_rows",
    "_combined_stop_check",
    "_configure_budget",
    "_configure_run_window",
    "_date_receipt",
    "_dispatch_campaign_outcome",
    "_dry_run_commands",
    "_finalize_campaign_run",
    "_finish_measurement",
    "_finished_event",
    "_finished_health",
    "_heartbeat_age",
    "_lane_command",
    "_load_lane_receipt",
    "_load_ledger",
    "_lock_is_held",
    "_minute_quota_args",
    "_no_progress_fuse_gate",
    "_now",
    "_parse_clock",
    "_partition_key",
    "_partition_shared_quota_stop_reason",
    "_phase_partition_keys",
    "_phase_receipts",
    "_phase_result_after_run",
    "_poll_campaign_stop",
    "_preflight_exit_reason",
    "_prepare_campaign_run",
    "_print_campaign_status",
    "_progress_stop_check",
    "_quota_window_clock",
    "_read_json",
    "_readiness_path",
    "_readiness_summary_is_valid",
    "_recent_throughput_samples",
    "_receipt_output_is_staging_only",
    "_resume_command",
    "_resume_preflight",
    "_run_commands",
    "_run_phase",
    "_run_preflight_attempt",
    "_runtime_campaign_config",
    "_select_execution_phase",
    "_shared_quota_stop_reason",
    "_sha256",
    "_single_lane_stop_check",
    "_start_campaign_run",
    "_status_eta",
    "_status_health",
    "_status_run_window",
    "_stop_started_children",
    "_update_quota_high_water",
    "_validate_completed_phase_receipts",
    "_validate_phase_outcome",
    "_validated_date",
    "_validated_acquisition_date",
    "_validated_readiness",
    "_write_ledger",
    "_write_preflight_entry",
    "_write_readiness_marker",
    "campaign_status",
    "run_budgeted",
    "run_next",
]
