from datetime import UTC, datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from riskqueue.cloud.messages import ObjectReference, ProcessingMessage
from riskqueue.cloud.queue import ReceivedMessage
from riskqueue.cloud.storage import LocalObjectStore
from riskqueue.db.models import Base, OperationalTransaction, ProcessedEvent, ReviewQueue
from riskqueue.worker import EventProcessor, process_received_message


class CollectingWarehouse:
    def __init__(self):
        self.rows = []

    def merge_scoring_history(self, rows):
        values = list(rows)
        self.rows.extend(values)
        return len(values)

    def refresh_daily_model_monitoring(self):
        return None


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _message(event_id="event-1", key="incoming/batch.csv"):
    return ProcessingMessage(
        event_id=event_id,
        batch_id="batch-1",
        object=ObjectReference(bucket="raw", key=key),
        occurred_at=datetime.now(UTC),
        source="test",
    )


def _csv():
    return (
        pd.DataFrame(
            {
                "transaction_id": ["tx-1", "tx-2"],
                "step": [1, 2],
                "type": ["TRANSFER", "PAYMENT"],
                "amount": [1000.0, 5.0],
                "nameOrig": ["C1", "C2"],
                "nameDest": ["M1", "M2"],
            }
        )
        .to_csv(index=False)
        .encode()
    )


def test_worker_persists_once_when_event_is_delivered_twice(tmp_path, session_factory):
    store = LocalObjectStore(tmp_path)
    store.put_bytes("raw", "incoming/batch.csv", _csv(), source="test", content_type="text/csv")
    warehouse = CollectingWarehouse()
    processor = EventProcessor(
        session_factory=session_factory, object_store=store, warehouse=warehouse, review_capacity=2
    )

    first = processor.process(_message())
    duplicate = processor.process(_message())

    assert first.status == "completed"
    assert first.transactions_written == 2
    assert duplicate.status == "duplicate"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProcessedEvent)) == 1
        assert session.scalar(select(func.count()).select_from(OperationalTransaction)) == 2
        assert session.scalar(select(func.count()).select_from(ReviewQueue)) == 1
    assert len(warehouse.rows) == 2


def test_worker_rolls_back_failed_event(tmp_path, session_factory):
    store = LocalObjectStore(tmp_path)
    store.put_bytes(
        "raw", "bad.csv", b"wrong,column\n1,2\n", source="test", content_type="text/csv"
    )
    processor = EventProcessor(
        session_factory=session_factory, object_store=store, warehouse=CollectingWarehouse()
    )

    with pytest.raises(ValueError, match="Missing columns"):
        processor.process(_message(key="bad.csv"))

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProcessedEvent)) == 0


def test_failed_message_is_not_deleted():
    class Queue:
        deleted = []

        def delete(self, receipt_handle):
            self.deleted.append(receipt_handle)

    class Processor:
        def process(self, message):
            raise RuntimeError("temporary failure")

    queue = Queue()
    received = ReceivedMessage(
        body=_message().model_dump_json(),
        receipt_handle="receipt",
        message_id="message",
        receive_count=2,
    )

    with pytest.raises(RuntimeError, match="temporary"):
        process_received_message(queue, Processor(), received)
    assert queue.deleted == []
