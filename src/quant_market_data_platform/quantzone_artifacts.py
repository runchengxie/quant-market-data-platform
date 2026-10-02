"""Validation and atomic artifact primitives for QuantZone factor runs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant_market_data_platform.configuration import ConfigurationError
from quant_market_data_platform.quantzone_plan import FactorBatch


class ArtifactError(ConfigurationError):
    """A sanitized local artifact validation failure."""


def validate_factor_batch(
    frame: pd.DataFrame, batch: FactorBatch, symbol_map: Mapping[str, str]
) -> pd.DataFrame:
    if set(frame.columns) != {"date", "ukey", "factor", "value"}:
        raise ArtifactError("Factor response requires exactly date, ukey, factor, value")
    result = frame.copy()
    allowed = {key.split(".")[0] for key in batch.ukeys}
    if any(
        not isinstance(key, str) or key not in allowed or key not in symbol_map
        for key in result["ukey"]
    ):
        raise ArtifactError("Factor response contains invalid stock identifiers")
    if any(not isinstance(key, str) or key not in batch.factors for key in result["factor"]):
        raise ArtifactError("Factor response contains unrequested factor identifiers")
    try:
        dates = pd.to_datetime(result["date"], errors="raise", format="mixed")
        if dates.dt.tz is not None or dates.isna().any() or (dates != dates.dt.normalize()).any():
            raise ValueError("invalid date")
        if ((dates.dt.date < batch.start_date) | (dates.dt.date > batch.end_date)).any():
            raise ValueError("outside query")
        result["date"] = dates
        if any(isinstance(value, (bool, np.bool_, complex)) for value in result["value"]):
            raise ValueError("boolean value")
        values = pd.to_numeric(result["value"], errors="raise").astype("float64")
        if np.isinf(values.to_numpy()).any():
            raise ValueError("infinite value")
        result["value"] = values
    except (TypeError, ValueError, OverflowError):
        raise ArtifactError("Factor response contains invalid dates or numeric values") from None
    if result.duplicated(["date", "ukey", "factor"]).any():
        raise ArtifactError("Duplicate factor observations")
    return result.sort_values(["date", "ukey", "factor"]).reset_index(drop=True)


def project_factor_panel(validated: pd.DataFrame, symbol_map: Mapping[str, str]) -> pd.DataFrame:
    if validated.duplicated(["date", "ukey", "factor"]).any():
        raise ArtifactError("Duplicate factor observations across batches")
    if validated.empty:
        return pd.DataFrame(
            {"symbol": pd.Series(dtype="str"), "trade_date": pd.Series(dtype="datetime64[ns]")}
        )
    if not set(validated["ukey"]).issubset(symbol_map):
        raise ArtifactError("Projection contains unmapped stocks")
    panel = validated.pivot(index=["ukey", "date"], columns="factor", values="value").reset_index()
    panel.columns.name = None
    panel = panel.rename(columns={"date": "trade_date"})
    panel["symbol"] = panel.pop("ukey").map(symbol_map)
    columns = ["symbol", "trade_date", *sorted(set(panel.columns) - {"symbol", "trade_date"})]
    return panel[columns].sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def safe_artifact_path(run: Path, relative: str) -> Path:
    path = run / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or path.is_symlink():
        raise ArtifactError("Invalid artifact path")
    if path.resolve().parent != run.resolve():
        raise ArtifactError("Artifact path escapes run directory")
    return path


def file_hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ArtifactError("Artifact file is missing or invalid")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_parquet(path: Path, frame: pd.DataFrame) -> str:
    if path.exists() or path.is_symlink():
        raise ArtifactError("Artifact collision; preserve and inspect the partial run")
    descriptor, temporary = tempfile.mkstemp(prefix=".batch-", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        frame.to_parquet(temporary_path, index=False)
        with temporary_path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        return file_hash(path)
    finally:
        temporary_path.unlink(missing_ok=True)
