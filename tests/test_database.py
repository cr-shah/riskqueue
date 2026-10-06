from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from riskqueue.db.logging import log_prediction
from riskqueue.db.models import Base, ModelVersion, PredictionEvent
from riskqueue.db.session import normalize_database_url


def test_render_postgres_url_uses_installed_psycopg_driver():
    assert (
        normalize_database_url("postgresql://user:secret@db.internal:5432/riskqueue")
        == "postgresql+psycopg://user:secret@db.internal:5432/riskqueue"
    )
    assert normalize_database_url("sqlite+pysqlite:///:memory:") == "sqlite+pysqlite:///:memory:"


def test_prediction_is_persisted_with_model_reference():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            ModelVersion(
                model_version="test-v1",
                created_at=datetime.now(UTC),
                algorithm="test",
                training_end_step=10,
                average_precision=0.4,
                roc_auc=0.8,
                brier=0.1,
                decision_threshold=0.2,
            )
        )
        session.commit()
        event = log_prediction(
            session,
            {
                "transaction_id": "tx-1",
                "model_version": "test-v1",
                "step": 11,
                "transaction_type": "TRANSFER",
                "amount": 100,
                "fraud_probability": 0.6,
                "risk_band": "high",
                "review_priority_score": 60,
                "decision": "review",
            },
        )
        assert event.prediction_id is not None
        assert session.scalar(select(PredictionEvent)).transaction_id == "tx-1"
