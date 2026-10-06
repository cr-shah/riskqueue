"""One feature and scoring path for live, API preview, and replay callers."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from riskqueue.db.models import OperationalTransaction
from riskqueue.features.pipeline import build_features
from riskqueue.scoring import FraudScorer, ScoringResult


def _available_history(session: Session, current: pd.DataFrame) -> pd.DataFrame:
    senders = current.nameOrig.astype(str).unique().tolist()
    recipients = current.nameDest.astype(str).unique().tolist()
    maximum_step = int(current.step.max())
    rows: dict[str, dict] = {}
    # Bound each SQL statement; history is restricted to entities in this batch.
    for start in range(0, max(len(senders), len(recipients)), 200):
        sender_slice = senders[start : start + 200]
        recipient_slice = recipients[start : start + 200]
        conditions = []
        if sender_slice:
            conditions.append(OperationalTransaction.sender_id.in_(sender_slice))
        if recipient_slice:
            conditions.append(OperationalTransaction.recipient_id.in_(recipient_slice))
        query = select(OperationalTransaction).where(
            OperationalTransaction.step < maximum_step, or_(*conditions)
        )
        for prior in session.scalars(query):
            rows[prior.transaction_id] = {
                "transaction_id": prior.transaction_id,
                "step": prior.step,
                "type": prior.transaction_type,
                "amount": prior.amount,
                "nameOrig": prior.sender_id,
                "nameDest": prior.recipient_id,
            }
    return pd.DataFrame(rows.values())


def score_transactions(
    transactions: pd.DataFrame,
    *,
    scorer: FraudScorer | None = None,
    session: Session | None = None,
) -> tuple[pd.DataFrame, ScoringResult]:
    """Use only committed, available rows; equal-hour rows remain invisible to peers."""
    current = transactions.copy()
    current["_scoring_current"] = True
    if session is not None:
        history = _available_history(session, current)
        if not history.empty:
            history = history[~history.transaction_id.isin(current.transaction_id)]
            history["_scoring_current"] = False
            current = pd.concat([history, current], ignore_index=True)
    featured = build_features(current)
    featured = featured.loc[featured._scoring_current].drop(columns="_scoring_current")
    featured = featured.reset_index(drop=True)
    return featured, (scorer or FraudScorer()).score(featured)
