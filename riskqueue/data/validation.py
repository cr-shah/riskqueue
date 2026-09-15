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


def _numeric_column(data: pd.DataFrame, column: str) -> pd.Series:
    """Return a finite real numeric column or raise the public validation error."""
    try:
        values = pd.to_numeric(data[column], errors="raise")
        raw_values = values.to_numpy()
        if np.iscomplexobj(raw_values):
            raise ValueError
        numeric_values = raw_values.astype(float)
    except (TypeError, ValueError, OverflowError):
        raise DataValidationError(f"{column} must contain numeric values") from None
    if not np.isfinite(numeric_values).all():
        raise DataValidationError(f"{column} must contain finite numeric values")
    return values


def _validate_core(frame: pd.DataFrame, required: set[str]) -> pd.DataFrame:
    missing = required.difference(frame.columns)
    if missing:
        raise DataValidationError(f"Missing columns: {', '.join(sorted(missing))}")
    data = frame.copy()
    if data.empty:
        raise DataValidationError("Dataset is empty")
    if data[list(required)].isnull().any().any():
        raise DataValidationError("Required columns contain null values")
    amount = _numeric_column(data, "amount")
    if (amount < 0).any():
        raise DataValidationError("amount must contain finite non-negative values")
    data["amount"] = amount.astype(float)
    step = _numeric_column(data, "step")
    if (step < 0).any() or (step % 1 != 0).any():
        raise DataValidationError("step must contain finite non-negative integers")
    if (step >= 2**63).any():
        raise DataValidationError("step exceeds the supported integer range")
    data["step"] = step.astype(np.int64)
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
    labels = _numeric_column(data, "isFraud")
    if not labels.isin([0, 1]).all():
        raise DataValidationError("isFraud must be binary")
    data["isFraud"] = labels.astype(np.int64)
    return data
