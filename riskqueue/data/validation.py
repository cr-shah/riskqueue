from __future__ import annotations

import numpy as np
import pandas as pd

from riskqueue.config import TRANSACTION_TYPES

BASE_REQUIRED_COLUMNS = {
    "step",
    "type",
    "amount",
    "nameOrig",
    "nameDest",
}
SCORING_REQUIRED_COLUMNS = BASE_REQUIRED_COLUMNS | {"transaction_id"}
REQUIRED_COLUMNS = BASE_REQUIRED_COLUMNS | {"isFraud"}


class DataValidationError(ValueError):
    """Raised when transaction data cannot be used safely."""


def _validate_core(frame: pd.DataFrame, required: set[str]) -> pd.DataFrame:
    missing = required.difference(frame.columns)
    if missing:
        raise DataValidationError(f"Missing columns: {', '.join(sorted(missing))}")
    data = frame.copy()
    if data.empty:
        raise DataValidationError("Dataset is empty")
    if data[list(required)].isnull().any().any():
        raise DataValidationError("Required columns contain null values")
    try:
        data["amount"] = pd.to_numeric(data["amount"], errors="raise").astype(float)
        numeric_steps = pd.to_numeric(data["step"], errors="raise").astype(float)
    except (TypeError, ValueError) as exc:
        raise DataValidationError("amount and step must be numeric") from exc
    if not np.isfinite(data["amount"]).all() or (data["amount"] < 0).any():
        raise DataValidationError("amount must contain finite non-negative values")
    if not np.isfinite(numeric_steps).all() or (numeric_steps < 0).any():
        raise DataValidationError("step must contain finite non-negative integers")
    if not np.equal(numeric_steps, np.floor(numeric_steps)).all():
        raise DataValidationError("step must contain finite non-negative integers")
    data["step"] = numeric_steps.astype(int)
    invalid_types = set(data["type"]).difference(TRANSACTION_TYPES)
    if invalid_types:
        raise DataValidationError(f"Invalid transaction types: {sorted(invalid_types)}")
    return data.sort_values(["step"], kind="stable").reset_index(drop=True)


def validate_scoring_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate fields required for inference and return a chronological copy."""
    data = _validate_core(frame, SCORING_REQUIRED_COLUMNS)
    if data["transaction_id"].astype(str).str.strip().eq("").any():
        raise DataValidationError("transaction_id must be non-empty")
    if data["transaction_id"].duplicated().any():
        raise DataValidationError("transaction_id must be unique within a batch")
    return data


def validate_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate the PaySim-compatible training contract and return a copy."""
    data = _validate_core(frame, REQUIRED_COLUMNS)
    if not set(data["isFraud"].unique()).issubset({0, 1}):
        raise DataValidationError("isFraud must be binary")
    return data
