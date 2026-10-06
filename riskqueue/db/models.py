from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from riskqueue.features.schema import FEATURE_VERSION


class Base(DeclarativeBase):
    pass


class ModelVersion(Base):
    __tablename__ = "model_versions"
    model_version: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    algorithm: Mapped[str] = mapped_column(String(120))
    training_end_step: Mapped[int] = mapped_column(Integer)
    average_precision: Mapped[float] = mapped_column(Float)
    roc_auc: Mapped[float] = mapped_column(Float)
    brier: Mapped[float] = mapped_column(Float)
    decision_threshold: Mapped[float] = mapped_column(Float)
    git_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)


class PredictionEvent(Base):
    __tablename__ = "prediction_events"
    prediction_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    transaction_id: Mapped[str] = mapped_column(String(120), index=True)
    model_version: Mapped[str] = mapped_column(ForeignKey("model_versions.model_version"))
    step: Mapped[int] = mapped_column(Integer)
    transaction_type: Mapped[str] = mapped_column(String(20))
    amount: Mapped[float] = mapped_column(Float)
    fraud_probability: Mapped[float] = mapped_column(Float)
    risk_band: Mapped[str] = mapped_column(String(20))
    review_priority_score: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(20))


class ProcessedEvent(Base):
    """Exactly-once operational marker for at-least-once queue delivery."""

    __tablename__ = "processed_events"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    batch_id: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(120))
    object_bucket: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)


class OperationalTransaction(Base):
    """Latest operational scoring state used by the application and analysts."""

    __tablename__ = "operational_transactions"
    transaction_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("processed_events.event_id"), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    step: Mapped[int] = mapped_column(Integer)
    transaction_type: Mapped[str] = mapped_column(String(20))
    amount: Mapped[float] = mapped_column(Float)
    sender_id: Mapped[str] = mapped_column(String(120))
    recipient_id: Mapped[str] = mapped_column(String(120))
    fraud_probability: Mapped[float] = mapped_column(Float)
    expected_loss: Mapped[float] = mapped_column(Float)
    risk_band: Mapped[str] = mapped_column(String(20))
    decision: Mapped[str] = mapped_column(String(20))
    model_version: Mapped[str] = mapped_column(String(80))
    feature_version: Mapped[str] = mapped_column(String(80), default=FEATURE_VERSION)
    score_kind: Mapped[str] = mapped_column(String(40), default="model_probability")
    policy_version: Mapped[str] = mapped_column(String(80), default="daily-backlog-v1")
    review_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    loss_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feature_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)


Index("ix_operational_sender_step", OperationalTransaction.sender_id, OperationalTransaction.step)
Index(
    "ix_operational_recipient_step",
    OperationalTransaction.recipient_id,
    OperationalTransaction.step,
)


class CapacityPeriod(Base):
    __tablename__ = "capacity_periods"
    __table_args__ = (
        CheckConstraint("budget >= 0", name="ck_capacity_budget"),
        CheckConstraint("consumed >= 0 AND consumed <= budget", name="ck_capacity_consumed"),
    )
    period_date: Mapped[date] = mapped_column(Date, primary_key=True)
    budget: Mapped[int] = mapped_column(Integer)
    consumed: Mapped[int] = mapped_column(Integer, default=0)


class ReviewQueue(Base):
    __tablename__ = "review_queue"
    __table_args__ = (UniqueConstraint("transaction_id", name="uq_review_queue_transaction"),)
    queue_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    transaction_id: Mapped[str] = mapped_column(String(120), index=True)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("processed_events.event_id"), index=True, nullable=True
    )
    queue_date: Mapped[date] = mapped_column(Date)
    rank: Mapped[int] = mapped_column(Integer)
    expected_loss: Mapped[float] = mapped_column(Float)
    priority_score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending")


class Case(Base):
    __tablename__ = "cases"
    case_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("operational_transactions.transaction_id"), unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    assigned_to: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="queued")
    disposition: Mapped[str | None] = mapped_column(String(40), nullable=True)


class CaseNote(Base):
    __tablename__ = "case_notes"
    note_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.case_id"), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(String(4000))


class CaseOutcome(Base):
    __tablename__ = "case_outcomes"
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.case_id"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(120))
    finding: Mapped[str] = mapped_column(String(40))
    investigation_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recovered_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    prevented_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String(2000))


class CaseAudit(Base):
    __tablename__ = "case_audit"
    audit_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.case_id"), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(40))
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    previous_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(80))
    feature_version: Mapped[str] = mapped_column(String(80))
    policy_version: Mapped[str] = mapped_column(String(80))
