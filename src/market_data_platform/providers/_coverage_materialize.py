"""Re-export surface for the split modules of _coverage_materialize."""

from market_data_platform.providers._coverage_materialize_part01 import (
    _accepted_diagnostic_names,
    _accepted_diagnostics,
    _fatal_issues,
    _frame_issues,
    _load_tushare_batches,
    _materialize_tushare_partial_dates,
    _partial_materialization_context,
    _partial_materialization_payload,
    _partial_shape_issues,
    _PartialMaterializationContext,
    _PartialMaterializationRequest,
    _prepare_partial_materialization,
    _validate_partial_frame,
    _write_prepared_partial_dates,
    materialize_tushare_partial_dates,
)
from market_data_platform.providers._coverage_materialize_part02 import (
    _coerce_expected_stats,
    _coerce_market_reference,
    _discover_output_layout,
    _FullDayMaterializationRequest,
    _materialize_tushare_full_days,
    _overlay_frame_disposition,
    _read_validated_tushare_full_day,
    _session_semantic_issues,
    _source_reconciliation_issues,
    materialize_tushare_full_days,
)
from market_data_platform.providers._coverage_materialize_part03 import (
    _audit_partition,
    _requirement_mismatches,
    audit_minute_coverage,
)
