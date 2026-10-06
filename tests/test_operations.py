from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from riskqueue.api.main import app
from riskqueue.cloud.messages import ObjectReference, ProcessingMessage
from riskqueue.cloud.storage import LocalObjectStore
from riskqueue.db.models import Base, CapacityPeriod, Case, OperationalTransaction, ReviewQueue
from riskqueue.modeling.artifacts import ModelMetadata, save_artifact
from riskqueue.scoring import FraudScorer
from riskqueue.worker import EventProcessor


class Warehouse:
    def merge_scoring_history(self, rows):
        return len(list(rows))


class CountModel:
    def predict_proba(self, features):
        probability = np.minimum(0.95, 0.1 + 0.2 * features.sender_count_24h.to_numpy())
        return np.column_stack([1 - probability, probability])


def tx(identifier, *, step=1, sender="A", recipient="X", amount=100):
    return {
        "transaction_id": identifier,
        "step": step,
        "type": "TRANSFER",
        "amount": amount,
        "nameOrig": sender,
        "nameDest": recipient,
    }


@pytest.fixture
def harness(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'operations.db'}"
    engine = create_engine(url, connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    store = LocalObjectStore(tmp_path / "objects")
    current_time = [datetime(2026, 1, 1, tzinfo=UTC)]

    def processor(capacity=2, scorer=None):
        return EventProcessor(
            session_factory=sessions,
            object_store=store,
            warehouse=Warehouse(),
            scorer=scorer,
            review_capacity=capacity,
            clock=lambda: current_time[0],
        )

    def process(batch, rows, *, capacity=2, scorer=None):
        key = f"{batch}.csv"
        store.put_bytes(
            "raw",
            key,
            pd.DataFrame(rows).to_csv(index=False).encode(),
            source="test",
            content_type="text/csv",
        )
        message = ProcessingMessage(
            event_id=batch,
            batch_id=batch,
            object=ObjectReference(bucket="raw", key=key),
            occurred_at=current_time[0],
            source="test",
        )
        return processor(capacity, scorer).process(message), message

    return url, sessions, current_time, processor, process


def feature(sessions, identifier, name):
    with sessions() as session:
        return session.get(OperationalTransaction, identifier).feature_snapshot[name]


def test_no_history_and_multiple_rows_in_one_batch(harness):
    _, sessions, _, _, process = harness
    process("one", [tx("a", step=1), tx("b", step=2, amount=200)])
    assert feature(sessions, "a", "sender_count_24h") == 0
    assert feature(sessions, "b", "sender_count_24h") == 1
    assert feature(sessions, "b", "sender_historical_median_amount") == 100


def test_later_batch_and_restart_preserve_history(harness):
    _, sessions, _, _, process = harness
    process("one", [tx("a", step=1)])
    process("two", [tx("b", step=2, amount=200)])
    assert feature(sessions, "b", "sender_count_24h") == 1
    assert feature(sessions, "b", "amount_to_sender_median") == 2


def test_future_and_out_of_order_do_not_leak(harness):
    _, sessions, _, _, process = harness
    process("future", [tx("future-tx", step=10)])
    process("late", [tx("late-tx", step=2)])
    assert feature(sessions, "late-tx", "sender_count_24h") == 0
    process("later", [tx("later-tx", step=11)])
    assert feature(sessions, "later-tx", "sender_count_24h") == 2


def test_equal_hour_across_batches_is_frozen(harness):
    _, sessions, _, _, process = harness
    process("one", [tx("a", step=2)])
    process("two", [tx("b", step=2)])
    assert feature(sessions, "b", "sender_count_1h") == 0


def test_duplicate_event_and_transaction_do_not_change_history(harness):
    _, sessions, _, processor, process = harness
    _, message = process("one", [tx("a", step=1)])
    assert processor().process(message).status == "duplicate"
    process("other-event", [tx("a", step=1), tx("b", step=2)])
    assert feature(sessions, "b", "sender_count_24h") == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OperationalTransaction)) == 2


def test_missing_optional_fields_and_sender_isolation(harness):
    _, sessions, _, _, process = harness
    process("one", [tx("a", sender="A"), tx("b", sender="B")])
    process("two", [tx("c", step=2, sender="B")])
    assert feature(sessions, "c", "sender_count_24h") == 1
    assert feature(sessions, "c", "sender_historical_median_amount") == 100


def test_api_and_worker_share_feature_sensitive_model(harness, tmp_path, monkeypatch):
    url, sessions, _, _, process = harness
    model_dir = tmp_path / "model"
    save_artifact(
        CountModel(),
        ModelMetadata(
            model_version="count-v1",
            algorithm="fixture",
            training_end_step=0,
            average_precision=0,
            roc_auc=0,
            brier=0,
            decision_threshold=0.5,
        ),
        model_dir,
    )
    scorer = FraudScorer(model_dir)
    process("one", [tx("a")], scorer=scorer)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("MODEL_ARTIFACT_DIR", str(model_dir))
    response = TestClient(app).post("/v1/score", json=tx("b", step=2))
    assert response.status_code == 200
    assert response.json()["fraud_probability"] == 0.3
    process("two", [tx("b", step=2)], scorer=scorer)
    with sessions() as session:
        saved = session.get(OperationalTransaction, "b")
        assert saved.fraud_probability == pytest.approx(response.json()["fraud_probability"])
        assert saved.model_version == response.json()["model_version"] == "count-v1"
        assert saved.feature_version == response.json()["feature_version"]
        assert saved.policy_version == "daily-backlog-v1"
        assert response.json()["policy_version"] == "api-preview-v1"
        assert saved.feature_snapshot["sender_count_24h"] == 1


def test_missing_configured_model_fails_closed(harness, tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_ARTIFACT_DIR", str(tmp_path / "missing"))
    response = TestClient(app).post("/v1/score", json=tx("a"))
    assert response.status_code == 503
    with pytest.raises(FileNotFoundError):
        FraudScorer()
    monkeypatch.delenv("MODEL_ARTIFACT_DIR")
    monkeypatch.setenv("SCORING_MODE", "trained")
    assert TestClient(app).get("/health").status_code == 503


def test_model_feature_version_mismatch_fails_before_scoring(tmp_path):
    model_dir = tmp_path / "model"
    save_artifact(
        CountModel(),
        ModelMetadata(
            model_version="old-features",
            algorithm="fixture",
            training_end_step=0,
            average_precision=0,
            roc_auc=0,
            brier=0,
            decision_threshold=0.5,
            feature_version="obsolete-features",
        ),
        model_dir,
    )
    with pytest.raises(ValueError, match="feature version"):
        FraudScorer(model_dir)


def test_multiple_batches_share_exact_daily_budget_and_backlog(harness):
    _, sessions, _, _, process = harness
    process("one", [tx("a"), tx("b", amount=200)], capacity=2)
    process("two", [tx("c", amount=300), tx("d", amount=400)], capacity=2)
    with sessions() as session:
        period = session.get(CapacityPeriod, datetime(2026, 1, 1, tzinfo=UTC).date())
        assert (period.budget, period.consumed) == (2, 2)
        assert (
            session.scalar(
                select(func.count()).select_from(ReviewQueue).where(ReviewQueue.status == "pending")
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count()).select_from(ReviewQueue).where(ReviewQueue.status == "backlog")
            )
            == 2
        )
        assert session.scalar(select(func.count()).select_from(Case)) == 2


def test_next_period_allocates_old_backlog_before_new_lower_priority(harness):
    _, sessions, current_time, _, process = harness
    process("one", [tx("a", amount=100), tx("b", amount=500)], capacity=1)
    current_time[0] += timedelta(days=1)
    result, _ = process("two", [tx("c", step=2, amount=50)], capacity=1)
    assert result.warehouse_rows == 2  # new row plus update of the rolled-over case
    with sessions() as session:
        assert session.get(CapacityPeriod, current_time[0].date()).consumed == 1
        assert (
            session.scalar(select(ReviewQueue.status).where(ReviewQueue.transaction_id == "a"))
            == "pending"
        )
        assert (
            session.scalar(select(ReviewQueue.status).where(ReviewQueue.transaction_id == "c"))
            == "backlog"
        )


@pytest.mark.parametrize("capacity,count,expected", [(0, 2, 0), (1, 1, 1), (1, 3, 1), (2, 2, 2)])
def test_capacity_boundaries(harness, capacity, count, expected):
    _, sessions, _, _, process = harness
    process("one", [tx(f"t-{i}", amount=100 + i) for i in range(count)], capacity=capacity)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Case)) == expected
        assert (
            session.get(CapacityPeriod, datetime(2026, 1, 1, tzinfo=UTC).date()).consumed
            == expected
        )


def test_250_limit_cannot_multiply_across_batches(harness):
    _, sessions, _, _, process = harness
    for batch in range(3):
        process(str(batch), [tx(f"{batch}-{i}") for i in range(100)], capacity=250)
    with sessions() as session:
        assert session.get(CapacityPeriod, datetime(2026, 1, 1, tzinfo=UTC).date()).consumed == 250
        assert (
            session.scalar(
                select(func.count()).select_from(ReviewQueue).where(ReviewQueue.status == "backlog")
            )
            == 50
        )


def test_worker_retry_after_warehouse_failure_rolls_back_capacity(harness):
    _, sessions, _, processor, process = harness

    class FailingWarehouse:
        def merge_scoring_history(self, rows):
            raise RuntimeError("warehouse unavailable")

    _, message = process("seed", [tx("seed")], capacity=1)
    with sessions() as session:
        assert session.get(CapacityPeriod, datetime(2026, 1, 1, tzinfo=UTC).date()).consumed == 1
    # A new day leaves capacity available; failure must not consume it.
    current = processor().clock()
    key = "retry.csv"
    store = processor().object_store
    store.put_bytes(
        "raw",
        key,
        pd.DataFrame([tx("retry", step=2)]).to_csv(index=False).encode(),
        source="test",
        content_type="text/csv",
    )
    retry_message = message.model_copy(
        update={"event_id": "retry", "object": ObjectReference(bucket="raw", key=key)}
    )
    failing = processor(capacity=1)
    failing.clock = lambda: current + timedelta(days=1)
    failing.warehouse = FailingWarehouse()
    with pytest.raises(RuntimeError, match="warehouse unavailable"):
        failing.process(retry_message)
    with sessions() as session:
        assert session.get(CapacityPeriod, (current + timedelta(days=1)).date()) is None
    successful = processor(capacity=1)
    successful.clock = lambda: current + timedelta(days=1)
    assert successful.process(retry_message).reviews_written == 1


def test_concurrent_workers_never_exceed_capacity(harness):
    _, sessions, _, _, process = harness
    # Independent events race for one slot; a transient SQLite lock is retryable.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(process, f"batch-{i}", [tx(f"tx-{i}")], capacity=1) for i in range(2)
        ]
        for index, future in enumerate(futures):
            try:
                future.result()
            except Exception as exc:
                assert "locked" in str(exc).lower() or "unique" in str(exc).lower()
                process(f"batch-{index}", [tx(f"tx-{index}")], capacity=1)
    with sessions() as session:
        period = session.get(CapacityPeriod, datetime(2026, 1, 1, tzinfo=UTC).date())
        assert period.consumed == 1
        assert session.scalar(select(func.count()).select_from(Case)) == 1
        assert session.scalar(select(func.count()).select_from(OperationalTransaction)) == 2
        assert (
            session.scalar(
                select(func.count()).select_from(ReviewQueue).where(ReviewQueue.status == "backlog")
            )
            == 1
        )
