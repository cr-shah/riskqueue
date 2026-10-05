from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TransactionType = Literal["CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER"]


class Transaction(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=120)
    step: int = Field(ge=0, le=10_000_000, strict=True)
    type: TransactionType
    amount: float = Field(ge=0, le=1e12, strict=True)
    nameOrig: str = Field(min_length=1, max_length=120)
    nameDest: str = Field(min_length=1, max_length=120)

    @field_validator("amount")
    @classmethod
    def amount_is_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("amount must be finite")
        return value


class BatchRequest(BaseModel):
    transactions: list[Transaction] = Field(min_length=1, max_length=1000)

    @field_validator("transactions")
    @classmethod
    def unique_ids(cls, transactions: list[Transaction]) -> list[Transaction]:
        if len({tx.transaction_id for tx in transactions}) != len(transactions):
            raise ValueError("transaction_id must be unique within a batch")
        return transactions


class ScoreResponse(BaseModel):
    transaction_id: str
    fraud_probability: float
    expected_loss: float
    risk_band: str
    decision: str
    model_version: str


class QueueRequest(BatchRequest):
    capacity: int = Field(default=100, ge=1, le=1000, strict=True)
    strategy: Literal["probability", "expected_loss", "expected_review_value"] = "expected_loss"
    manual_review_cost: float = Field(default=4.0, ge=0, le=1000, allow_inf_nan=False)
    loss_fraction: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
