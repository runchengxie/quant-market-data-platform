from __future__ import annotations

from pathlib import Path

import pytest

from quant_market_data_platform.providers._io import _prepare_output_dir


def test_prepare_output_dir_rejects_existing_parquet(tmp_path: Path) -> None:
    output = tmp_path / "asset"
    output.mkdir()
    (output / "part.parquet").write_bytes(b"old")

    with pytest.raises(FileExistsError, match="non-empty mirror output"):
        _prepare_output_dir(output)


def test_prepare_output_dir_allows_explicit_resume(tmp_path: Path) -> None:
    output = tmp_path / "asset"
    output.mkdir()
    (output / "part.parquet").write_bytes(b"old")

    assert _prepare_output_dir(output, allow_existing=True) == output.resolve()


def test_ths_member_cli_requires_explicit_resume() -> None:
    from quant_market_data_platform.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        ["tushare", "mirror-a-share-ths-member", "--out-dir", "out", "--skip-existing"]
    )
    assert args.skip_existing is True


def test_ths_member_reads_cached_partition_as_file(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from typing import cast

    import pandas as pd

    from quant_market_data_platform.providers.tushare_a_share_ths_member import (
        ThsMemberMirrorContext,
        _fetch_filtered_ths_member_parts,
    )

    part = tmp_path / "parts" / "ts_code=864021_TI" / "part.parquet"
    part.parent.mkdir(parents=True)
    pd.DataFrame({"ts_code": ["864021.TI"], "con_code": ["000001.SZ"]}).to_parquet(
        part, index=False
    )
    context = SimpleNamespace(output_dir=tmp_path, concept_codes=("864021.TI",), pd=pd)
    frames, skipped = _fetch_filtered_ths_member_parts(
        context=cast(ThsMemberMirrorContext, context), request_interval_seconds=0
    )
    assert skipped == 1
    assert frames[0]["ts_code"].tolist() == ["864021.TI"]


def test_daily_clean_staging_preserves_unknown_st_availability(tmp_path: Path) -> None:
    import pandas as pd

    from quant_market_data_platform.standardize.tushare.a_share_daily_part01 import (
        _write_daily_clean_staging_batch,
    )

    frame = pd.DataFrame(
        {
            "symbol": ["000001.SZ"],
            "st_available_from": pd.Series([pd.NA], dtype="string"),
            "optional": [None],
        }
    )
    assert (
        _write_daily_clean_staging_batch(frames=[frame], staging_dir=tmp_path, batch_index=0) == 1
    )
    output = pd.read_parquet(tmp_path / "symbol=000001.SZ" / "part_0000.parquet")
    assert "st_available_from" in output
    assert output["st_available_from"].isna().all()
    assert str(output["st_available_from"].dtype) == "string"
    assert "optional" not in output


def test_ths_member_resume_fetches_only_missing_concepts(tmp_path: Path) -> None:
    import pandas as pd

    from quant_market_data_platform.providers.tushare_a_share import (
        ThsMemberMirrorOptions,
        mirror_a_share_ths_member,
    )

    class Client:
        def ths_index(self, **kwargs):
            return pd.DataFrame({"ts_code": ["864021.TI", "864022.TI"]})

        def ths_member(self, **kwargs):
            assert kwargs["ts_code"] == "864022.TI"
            return pd.DataFrame({"ts_code": ["864022.TI"], "con_code": ["000002.SZ"]})

    part = tmp_path / "parts" / "ts_code=864021_TI" / "part.parquet"
    part.parent.mkdir(parents=True)
    pd.DataFrame({"ts_code": ["864021.TI"], "con_code": ["000001.SZ"]}).to_parquet(
        part, index=False
    )
    manifest = mirror_a_share_ths_member(
        ThsMemberMirrorOptions(
            out_dir=tmp_path,
            skip_existing=True,
            request_interval_seconds=0,
            request_options={"client": Client()},
        )
    )
    assert manifest["totals"]["rows"] == 2
    assert manifest["totals"]["skipped_existing_parts"] == 1
