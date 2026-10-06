"""Transactional daily capacity shared by all workers and batches."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from riskqueue.db.models import CapacityPeriod, Case, CaseAudit, OperationalTransaction, ReviewQueue

POLICY_VERSION = "daily-backlog-v1"


def allocate_capacity(session: Session, period_date: date, budget: int) -> set[str]:
    if budget < 0:
        raise ValueError("review capacity must be non-negative")
    period = session.get(CapacityPeriod, period_date)
    if period is None:
        try:
            with session.begin_nested():
                session.add(CapacityPeriod(period_date=period_date, budget=budget, consumed=0))
                session.flush()
        except IntegrityError:
            pass  # Another worker created the same period; read its committed budget below.
        period = session.get(CapacityPeriod, period_date)
    if period.budget != budget:
        raise ValueError(f"Capacity budget conflict for {period_date}: {period.budget} != {budget}")
    backlog = session.scalars(
        select(ReviewQueue)
        .where(ReviewQueue.status == "backlog")
        .order_by(ReviewQueue.priority_score.desc(), ReviewQueue.created_at, ReviewQueue.queue_id)
        .with_for_update()
    ).all()
    allocated: set[str] = set()
    for candidate in backlog:
        if candidate.status != "backlog":
            continue
        claim = session.execute(
            update(CapacityPeriod)
            .where(CapacityPeriod.period_date == period_date)
            .where(CapacityPeriod.consumed < CapacityPeriod.budget)
            .values(consumed=CapacityPeriod.consumed + 1)
        )
        if claim.rowcount != 1:
            break
        candidate.status = "pending"
        candidate.queue_date = period_date
        transaction = session.get(OperationalTransaction, candidate.transaction_id)
        transaction.decision = "review"
        case = Case(
            transaction_id=candidate.transaction_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            status="queued",
        )
        session.add(case)
        session.flush()
        session.add(
            CaseAudit(
                case_id=case.case_id,
                actor="system:capacity",
                action="created",
                happened_at=datetime.now(UTC),
                previous_value=None,
                new_value={"status": "queued", "period": period_date.isoformat()},
                model_version=transaction.model_version,
                feature_version=transaction.feature_version,
                policy_version=transaction.policy_version,
            )
        )
        allocated.add(candidate.transaction_id)
    return allocated
