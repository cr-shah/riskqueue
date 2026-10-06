"""Chronological policy replay over immutable, available operational scores."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from heapq import heappop, heappush
from itertools import groupby
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from riskqueue.db.models import Case, CaseOutcome, OperationalTransaction
from riskqueue.decisions.capacity import POLICY_VERSION


class ReplayPolicy(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    capacity: int | None = Field(default=None, ge=0, le=100_000)
    analysts: int | None = Field(default=None, ge=1, le=1000)
    reviews_per_analyst: int | None = Field(default=None, ge=1, le=1000)
    threshold: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    review_cost: float = Field(default=4, ge=0, le=1e9, allow_inf_nan=False)
    loss_fraction: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    strategy: Literal["probability", "expected_loss", "expected_review_value"] = "expected_loss"

    @model_validator(mode="after")
    def one_capacity_source(self):
        staffing = self.analysts is not None or self.reviews_per_analyst is not None
        if (self.capacity is None) == (not staffing):
            raise ValueError("Provide capacity or analyst staffing, but not both")
        if staffing and (self.analysts is None or self.reviews_per_analyst is None):
            raise ValueError("Staffing needs both analysts and reviews_per_analyst")
        if staffing and self.analysts * self.reviews_per_analyst > 100_000:
            raise ValueError("Effective daily capacity exceeds 100,000")
        return self

    @property
    def daily_capacity(self) -> int:
        if self.capacity is not None:
            return self.capacity
        return self.analysts * self.reviews_per_analyst


class ReplayRequest(BaseModel):
    available_from: datetime
    available_through: datetime
    policies: list[ReplayPolicy] = Field(min_length=1, max_length=8)
    model_version: str | None = None


def replay(session: Session, request: ReplayRequest) -> dict:
    if request.available_from.tzinfo is None or request.available_through.tzinfo is None:
        raise ValueError("Replay bounds must be timezone-aware")
    if request.available_from > request.available_through:
        raise ValueError("available_from must precede available_through")
    transactions = session.scalars(
        select(OperationalTransaction)
        .where(OperationalTransaction.ingested_at >= request.available_from)
        .where(OperationalTransaction.ingested_at <= request.available_through)
        .order_by(
            OperationalTransaction.ingested_at,
            OperationalTransaction.event_id,
            OperationalTransaction.step,
            OperationalTransaction.transaction_id,
        )
        .limit(10_001)
    ).all()
    if len(transactions) > 10_000:
        raise ValueError("Replay range exceeds 10,000 rows; narrow the time range")
    versions = {(tx.model_version, tx.feature_version) for tx in transactions}
    if len(versions) > 1:
        raise ValueError("Replay range contains mixed model or feature versions")
    if request.model_version and versions and request.model_version != next(iter(versions))[0]:
        raise ValueError("Requested model version was not used for these stored scores")
    if any(tx.fraud_probability < 0 or tx.fraud_probability > 1 for tx in transactions):
        raise ValueError("Stored model outputs are outside [0, 1]")
    evidence = [
        (
            tx.transaction_id,
            tx.event_id,
            tx.ingested_at.isoformat(),
            tx.step,
            tx.model_version,
            tx.feature_version,
            tx.amount,
            tx.fraud_probability,
        )
        for tx in transactions
    ]
    dataset_version = hashlib.sha256(
        json.dumps(evidence, separators=(",", ":")).encode()
    ).hexdigest()
    outcomes = {}
    transaction_ids = [tx.transaction_id for tx in transactions]
    for offset in range(0, len(transaction_ids), 500):
        outcomes.update(
            {
                transaction_id: outcome
                for transaction_id, outcome in session.execute(
                    select(Case.transaction_id, CaseOutcome)
                    .join(CaseOutcome, Case.case_id == CaseOutcome.case_id)
                    .where(Case.transaction_id.in_(transaction_ids[offset : offset + 500]))
                    .where(CaseOutcome.observed_at <= request.available_through)
                    .where(CaseOutcome.recorded_at <= request.available_through)
                )
            }
        )
    scenarios = []
    for policy in request.policies:
        pending = []
        selected = []
        capacity_used = defaultdict(int)
        for (available_at, _event_id), batch in groupby(
            transactions, key=lambda tx: (tx.ingested_at, tx.event_id)
        ):
            day = available_at.date()
            for tx in batch:
                probability = tx.fraud_probability
                expected = probability * tx.amount * policy.loss_fraction
                if probability >= policy.threshold and expected > policy.review_cost:
                    priority = {
                        "probability": probability,
                        "expected_loss": expected,
                        "expected_review_value": expected - policy.review_cost,
                    }[policy.strategy]
                    heappush(pending, (-priority, tx.ingested_at, tx.transaction_id, tx))
            slots = max(0, policy.daily_capacity - capacity_used[day])
            claims = min(slots, len(pending))
            selected.extend(heappop(pending)[3] for _ in range(claims))
            capacity_used[day] += claims
        labeled_selected = [
            outcomes[tx.transaction_id] for tx in selected if tx.transaction_id in outcomes
        ]
        confirmed = sum(outcome.finding == "confirmed_fraud" for outcome in labeled_selected)
        legitimate = sum(outcome.finding == "legitimate" for outcome in labeled_selected)
        scenarios.append(
            {
                "policy": policy.model_dump(),
                "effective_daily_capacity": policy.daily_capacity,
                "reviewed_cases": len(selected),
                "reviewed_transaction_ids": [tx.transaction_id for tx in selected],
                "backlog": len(pending),
                "backlog_transaction_ids": [item[3].transaction_id for item in sorted(pending)],
                "known_confirmed_fraud": confirmed,
                "known_legitimate": legitimate,
                "unknown_or_unresolved": len(selected) - confirmed - legitimate,
                "observed_precision_on_known": confirmed / (confirmed + legitimate)
                if confirmed + legitimate
                else None,
                "estimated_review_cost": len(selected) * policy.review_cost,
                "capacity_used_by_day": {str(day): count for day, count in capacity_used.items()},
            }
        )
    return {
        "dataset_version": dataset_version,
        "available_from": request.available_from,
        "available_through": request.available_through,
        "model_version": next(iter(versions))[0] if versions else request.model_version,
        "feature_version": next(iter(versions))[1] if versions else None,
        "transactions": len(transactions),
        "outcome_scope": "Outcomes recorded and observed by available_through; retrospective evaluation only, never ranking inputs.",
        "policy_version": POLICY_VERSION,
        "scenarios": scenarios,
    }
