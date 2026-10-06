"""Persist feature provenance, daily budgets, cases, outcomes and audit.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "operational_transactions",
        sa.Column(
            "feature_version", sa.String(80), nullable=False, server_default="legacy-unknown"
        ),
    )
    op.add_column("operational_transactions", sa.Column("scored_at", sa.DateTime(timezone=True)))
    op.add_column(
        "operational_transactions",
        sa.Column("score_kind", sa.String(40), nullable=False, server_default="legacy-unknown"),
    )
    op.add_column(
        "operational_transactions",
        sa.Column("policy_version", sa.String(80), nullable=False, server_default="legacy-unknown"),
    )
    op.add_column("operational_transactions", sa.Column("review_cost", sa.Float))
    op.add_column("operational_transactions", sa.Column("loss_fraction", sa.Float))
    op.add_column("operational_transactions", sa.Column("feature_snapshot", sa.JSON))
    op.create_index("ix_operational_sender_step", "operational_transactions", ["sender_id", "step"])
    op.create_index(
        "ix_operational_recipient_step", "operational_transactions", ["recipient_id", "step"]
    )
    op.create_table(
        "capacity_periods",
        sa.Column("period_date", sa.Date, primary_key=True),
        sa.Column("budget", sa.Integer, nullable=False),
        sa.Column("consumed", sa.Integer, nullable=False),
        sa.CheckConstraint("budget >= 0", name="ck_capacity_budget"),
        sa.CheckConstraint("consumed >= 0 AND consumed <= budget", name="ck_capacity_consumed"),
    )
    op.create_table(
        "cases",
        sa.Column("case_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "transaction_id",
            sa.String(120),
            sa.ForeignKey("operational_transactions.transaction_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_to", sa.String(120)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("disposition", sa.String(40)),
    )
    op.create_index("ix_cases_transaction_id", "cases", ["transaction_id"])
    op.create_index("ix_cases_assigned_to", "cases", ["assigned_to"])
    op.create_index("ix_cases_status", "cases", ["status"])
    op.create_table(
        "case_notes",
        sa.Column("note_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("cases.case_id"), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.String(4000), nullable=False),
    )
    op.create_index("ix_case_notes_case_id", "case_notes", ["case_id"])
    op.create_table(
        "case_outcomes",
        sa.Column("case_id", sa.Integer, sa.ForeignKey("cases.case_id"), primary_key=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("finding", sa.String(40), nullable=False),
        sa.Column("investigation_minutes", sa.Integer),
        sa.Column("recovered_amount", sa.Float),
        sa.Column("prevented_amount", sa.Float),
        sa.Column("reason", sa.String(2000), nullable=False),
    )
    op.create_table(
        "case_audit",
        sa.Column("audit_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("cases.case_id"), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_value", sa.JSON),
        sa.Column("new_value", sa.JSON, nullable=False),
        sa.Column("model_version", sa.String(80), nullable=False),
        sa.Column("feature_version", sa.String(80), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
    )
    op.create_index("ix_case_audit_case_id", "case_audit", ["case_id"])


def downgrade():
    for table in ("case_audit", "case_outcomes", "case_notes", "cases", "capacity_periods"):
        op.drop_table(table)
    op.drop_index("ix_operational_recipient_step", "operational_transactions")
    op.drop_index("ix_operational_sender_step", "operational_transactions")
    op.drop_column("operational_transactions", "feature_snapshot")
    op.drop_column("operational_transactions", "scored_at")
    op.drop_column("operational_transactions", "feature_version")
    op.drop_column("operational_transactions", "score_kind")
    op.drop_column("operational_transactions", "policy_version")
    op.drop_column("operational_transactions", "review_cost")
    op.drop_column("operational_transactions", "loss_fraction")
