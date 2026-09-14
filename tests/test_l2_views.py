from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from market_data_platform.l2_views import (
    canonical_columns,
    iter_l2_batches,
)


def _write(path: Path) -> None:
    pq.write_table(
        pa.table(
            {
                "SecuCode": ["000001", "000001", "000002"],
                "TradingDay": [20260424, 20260424, 20260424],
                "DealTime": [1, 2, 3],
                "DealID": [10, 11, 12],
                "Price": [100, 0, 101],
                "Volume": [1, 2, 3],
                "Side": [0, -1, 1],
                "BuyID": [1, 0, 3],
                "SellID": [4, 5, 6],
                "unused": ["a", "b", "c"],
            }
        ),
        path,
    )


def test_canonical_deal_columns_are_explicit() -> None:
    assert canonical_columns("deal") == (
        "SecuCode",
        "TradingDay",
        "DealTime",
        "DealID",
        "Price",
        "Volume",
        "Side",
        "BuyID",
        "SellID",
    )


def test_iter_l2_batches_projects_and_applies_sparse_labels(tmp_path: Path) -> None:
    source = tmp_path / "deal_20260424.parquet"
    labels = tmp_path / "deal_20260424.labels.parquet"
    _write(source)
    pq.write_table(
        pa.table(
            {
                "row_number": [1],
                "decision": ["tag"],
                "reason": ["special_price_or_event"],
                "SecuCode": ["000001"],
                "event_id": [11],
            }
        ),
        labels,
    )

    batches = list(iter_l2_batches(source, "deal", labels_path=labels, batch_size=2))
    assert [batch.num_rows for batch in batches] == [1, 1]
    assert batches[0].column_names == list(canonical_columns("deal")) + [
        "quality_decision",
        "quality_reason",
    ]
    assert batches[0]["DealID"].to_pylist() == [10]
    assert batches[0]["quality_decision"].to_pylist() == [None]
    assert batches[1]["DealID"].to_pylist() == [12]

    tagged = list(
        iter_l2_batches(
            source,
            "deal",
            labels_path=labels,
            batch_size=2,
            include_tagged=True,
        )
    )
    assert [row for batch in tagged for row in batch["DealID"].to_pylist()] == [10, 11, 12]
    assert tagged[0]["quality_reason"].to_pylist() == [None, "special_price_or_event"]
