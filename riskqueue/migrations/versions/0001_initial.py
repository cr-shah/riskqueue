"""Original operational schema; safe on an existing sql/schema.sql installation.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table("processed_events"):
        return
    op.create_table(
        "model_versions",
        sa.Column("model_version", sa.String(80), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("algorithm", sa.String(120), nullable=False),
        sa.Column("training_end_step", sa.Integer, nullable=False),
        sa.Column("average_precision", sa.Float, nullable=False),
        sa.Column("roc_auc", sa.Float, nullable=False),
        sa.Column("brier", sa.Float, nullable=False),
        sa.Column("decision_threshold", sa.Float, nullable=False),
        sa.Column("git_commit", sa.String(40)),
    )
    op.create_table(
        "prediction_events",
        sa.Column("prediction_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transaction_id", sa.String(120), nullable=False),
        sa.Column(
            "model_version",
            sa.String(80),
            sa.ForeignKey("model_versions.model_version"),
            nullable=False,
        ),
        sa.Column("step", sa.Integer, nullable=False),
        sa.Column("transaction_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("fraud_probability", sa.Float, nullable=False),
        sa.Column("risk_band", sa.String(20), nullable=False),
        sa.Column("review_priority_score", sa.Float, nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
    )
    op.create_index("ix_prediction_events_transaction_id", "prediction_events", ["transaction_id"])
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("batch_id", sa.String(128), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("object_bucket", sa.String(255), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("transaction_count", sa.Integer, nullable=False),
    )
    op.create_index("ix_processed_events_batch_id", "processed_events", ["batch_id"])
    op.create_index("ix_processed_events_status", "processed_events", ["status"])
    op.create_table(
        "operational_transactions",
        sa.Column("transaction_id", sa.String(120), primary_key=True),
        sa.Column(
            "event_id", sa.String(128), sa.ForeignKey("processed_events.event_id"), nullable=False
        ),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("step", sa.Integer, nullable=False),
        sa.Column("transaction_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("sender_id", sa.String(120), nullable=False),
        sa.Column("recipient_id", sa.String(120), nullable=False),
        sa.Column("fraud_probability", sa.Float, nullable=False),
        sa.Column("expected_loss", sa.Float, nullable=False),
        sa.Column("risk_band", sa.String(20), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("model_version", sa.String(80), nullable=False),
    )
    op.create_index(
        "ix_operational_transactions_event_id", "operational_transactions", ["event_id"]
    )
    op.create_table(
        "review_queue",
        sa.Column("queue_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transaction_id", sa.String(120), nullable=False),
        sa.Column("event_id", sa.String(128), sa.ForeignKey("processed_events.event_id")),
        sa.Column("queue_date", sa.Date, nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("expected_loss", sa.Float, nullable=False),
        sa.Column("priority_score", sa.Float, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("transaction_id", name="uq_review_queue_transaction"),
    )
    op.create_index("ix_review_queue_transaction_id", "review_queue", ["transaction_id"])
    op.create_index("ix_review_queue_event_id", "review_queue", ["event_id"])


def downgrade():
    for table in (
        "review_queue",
        "operational_transactions",
        "processed_events",
        "prediction_events",
        "model_versions",
    ):
        op.drop_table(table)
