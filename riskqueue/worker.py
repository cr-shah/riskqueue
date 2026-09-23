from __future__ import annotations

import io
import logging
import os
import signal
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pandas as pd

from riskqueue.cloud.messages import ProcessingMessage
from riskqueue.cloud.queue import ReceivedMessage, SQSProcessingQueue
from riskqueue.cloud.storage import ObjectStore, build_object_store
from riskqueue.data.validation import validate_scoring_transactions
from riskqueue.db.models import Base, OperationalTransaction, ProcessedEvent, ReviewQueue
from riskqueue.db.session import build_session_factory
from riskqueue.decisions.review_queue import build_review_queue
from riskqueue.features.pipeline import build_features
from riskqueue.observability import configure_logging, log_event
from riskqueue.scoring import FraudScorer, risk_band
from riskqueue.warehouse.snowflake import AnalyticalWarehouse, build_warehouse

logger = logging.getLogger("riskqueue.worker")


@dataclass(frozen=True)
class ProcessResult:
    event_id: str
    status: str
    transactions_written: int
    reviews_written: int
    warehouse_rows: int


class EventProcessor:
    def __init__(
        self,
        *,
        session_factory,
        object_store: ObjectStore,
        warehouse: AnalyticalWarehouse,
        scorer: FraudScorer | None = None,
        review_capacity: int = 750,
        review_cost: float = 4.0,
    ):
        self.session_factory = session_factory
        self.object_store = object_store
        self.warehouse = warehouse
        self.scorer = scorer or FraudScorer()
        self.review_capacity = review_capacity
        self.review_cost = review_cost

    def process(self, message: ProcessingMessage) -> ProcessResult:
        log_event(
            logger,
            "processing_started",
            event_id=message.event_id,
            batch_id=message.batch_id,
            stage="receive",
        )
        with self.session_factory() as session:
            existing = session.get(ProcessedEvent, message.event_id)
            if existing is not None:
                log_event(
                    logger,
                    "duplicate_event_skipped",
                    event_id=message.event_id,
                    batch_id=message.batch_id,
                    stage="idempotency",
                    status=existing.status,
                )
                return ProcessResult(message.event_id, "duplicate", 0, 0, 0)

            event = ProcessedEvent(
                event_id=message.event_id,
                idempotency_key=message.idempotency_key,
                batch_id=message.batch_id,
                source=message.source,
                object_bucket=message.object.bucket,
                object_key=message.object.key,
                status="processing",
                started_at=datetime.now(UTC),
                transaction_count=0,
            )
            session.add(event)
            session.flush()

            try:
                payload = self.object_store.get_bytes(message.object.bucket, message.object.key)
                raw = pd.read_csv(io.BytesIO(payload))
                transactions = validate_scoring_transactions(raw)
                featured = build_features(transactions)
                scoring = self.scorer.score(featured)
                ranked = build_review_queue(
                    featured,
                    scoring.probabilities,
                    min(self.review_capacity, len(featured)),
                    "expected_loss",
                    manual_review_cost=self.review_cost,
                )
                rank_by_transaction = dict(zip(ranked.transaction_id, ranked["rank"], strict=True))
                score_by_transaction = dict(
                    zip(ranked.transaction_id, ranked.priority_score, strict=True)
                )
                now = datetime.now(UTC)
                warehouse_rows = []
                reviews_written = 0
                transactions_written = 0

                for position, row in enumerate(featured.itertuples()):
                    transaction_id = str(row.transaction_id)
                    if session.get(OperationalTransaction, transaction_id) is not None:
                        continue
                    probability = float(scoring.probabilities[position])
                    expected_loss = probability * float(row.amount)
                    review = (
                        transaction_id in rank_by_transaction and expected_loss > self.review_cost
                    )
                    decision = "review" if review else "approve"
                    band = risk_band(probability)
                    session.add(
                        OperationalTransaction(
                            transaction_id=transaction_id,
                            event_id=message.event_id,
                            ingested_at=now,
                            step=int(row.step),
                            transaction_type=str(row.type),
                            amount=float(row.amount),
                            sender_id=str(row.nameOrig),
                            recipient_id=str(row.nameDest),
                            fraud_probability=probability,
                            expected_loss=expected_loss,
                            risk_band=band,
                            decision=decision,
                            model_version=scoring.model_version,
                        )
                    )
                    if review:
                        session.add(
                            ReviewQueue(
                                event_id=message.event_id,
                                created_at=now,
                                transaction_id=transaction_id,
                                queue_date=date.today(),
                                rank=int(rank_by_transaction[transaction_id]),
                                expected_loss=expected_loss,
                                priority_score=float(score_by_transaction[transaction_id]),
                                status="pending",
                            )
                        )
                        reviews_written += 1
                    warehouse_rows.append(
                        {
                            "event_id": message.event_id,
                            "transaction_id": transaction_id,
                            "ingested_at": now,
                            "step": int(row.step),
                            "transaction_type": str(row.type),
                            "amount": float(row.amount),
                            "fraud_probability": probability,
                            "expected_loss": expected_loss,
                            "risk_band": band,
                            "decision": decision,
                            "model_version": scoring.model_version,
                        }
                    )
                    transactions_written += 1

                loaded = self.warehouse.merge_scoring_history(warehouse_rows)
                event.status = "completed"
                event.completed_at = datetime.now(UTC)
                event.transaction_count = transactions_written
                session.commit()
            except Exception:
                session.rollback()
                log_event(
                    logger,
                    "processing_failed",
                    event_id=message.event_id,
                    batch_id=message.batch_id,
                    stage="process",
                    status="failed",
                )
                raise

        log_event(
            logger,
            "processing_completed",
            event_id=message.event_id,
            batch_id=message.batch_id,
            stage="complete",
            status="success",
            transaction_count=transactions_written,
            review_count=reviews_written,
        )
        return ProcessResult(
            message.event_id, "completed", transactions_written, reviews_written, loaded
        )


def process_received_message(
    queue: SQSProcessingQueue, processor: EventProcessor, received: ReceivedMessage
) -> ProcessResult:
    """Delete only successfully processed messages; SQS retries failures and routes to the DLQ."""
    try:
        message = ProcessingMessage.model_validate_json(received.body)
        result = processor.process(message)
    except Exception:
        log_event(
            logger,
            "message_failed",
            message_id=received.message_id,
            retry_count=received.receive_count,
            stage="message",
            status="failed",
        )
        raise
    queue.delete(received.receipt_handle)
    return result


def main() -> None:
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    queue = SQSProcessingQueue()
    session_factory = build_session_factory()
    if os.getenv("AUTO_CREATE_SCHEMA", "false").lower() in {"1", "true", "yes"}:
        with session_factory() as session:
            Base.metadata.create_all(session.get_bind())
    processor = EventProcessor(
        session_factory=session_factory,
        object_store=build_object_store(),
        warehouse=build_warehouse(),
        review_capacity=int(os.getenv("REVIEW_CAPACITY", "750")),
        review_cost=float(os.getenv("MANUAL_REVIEW_COST", "4")),
    )
    stopping = False

    def stop(*_args):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        for received in queue.receive():
            try:
                process_received_message(queue, processor, received)
            except Exception:
                logger.exception("Processing message failed; SQS will retry it")


if __name__ == "__main__":
    main()
