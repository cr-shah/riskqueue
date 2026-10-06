from __future__ import annotations

import io
import logging
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select

from riskqueue.cloud.messages import ProcessingMessage
from riskqueue.cloud.queue import ReceivedMessage, SQSProcessingQueue
from riskqueue.cloud.storage import ObjectStore, build_object_store
from riskqueue.data.validation import validate_scoring_transactions
from riskqueue.db.models import Base, OperationalTransaction, ProcessedEvent, ReviewQueue
from riskqueue.db.session import build_session_factory
from riskqueue.decisions.capacity import POLICY_VERSION, allocate_capacity
from riskqueue.decisions.review_queue import build_review_queue
from riskqueue.features.schema import MODEL_FEATURES
from riskqueue.observability import configure_logging, log_event
from riskqueue.scoring import FraudScorer, risk_band
from riskqueue.scoring_pipeline import score_transactions
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
        clock=None,
    ):
        self.session_factory = session_factory
        self.object_store = object_store
        self.warehouse = warehouse
        self.scorer = scorer or FraudScorer()
        self.review_capacity = review_capacity
        self.review_cost = review_cost
        self.clock = clock or (lambda: datetime.now(UTC))

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
                if existing.status != "completed":
                    raise RuntimeError(
                        f"Event {message.event_id} has non-completed status {existing.status}"
                    )
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
                existing_ids = set(
                    session.scalars(
                        select(OperationalTransaction.transaction_id).where(
                            OperationalTransaction.transaction_id.in_(
                                transactions.transaction_id.tolist()
                            )
                        )
                    )
                )
                transactions = transactions[
                    ~transactions.transaction_id.isin(existing_ids)
                ].reset_index(drop=True)
                now = self.clock()
                if transactions.empty:
                    event.status = "completed"
                    event.completed_at = now
                    session.commit()
                    return ProcessResult(message.event_id, "completed", 0, 0, 0)
                featured, scoring = score_transactions(
                    transactions, scorer=self.scorer, session=session
                )
                ranked = build_review_queue(
                    featured,
                    scoring.probabilities,
                    len(featured),
                    "expected_loss",
                    manual_review_cost=self.review_cost,
                )
                rank_by_transaction = dict(zip(ranked.transaction_id, ranked["rank"], strict=True))
                score_by_transaction = dict(
                    zip(ranked.transaction_id, ranked.priority_score, strict=True)
                )
                warehouse_rows = []
                reviews_written = 0
                transactions_written = 0

                for position, row in enumerate(featured.itertuples()):
                    transaction_id = str(row.transaction_id)
                    probability = float(scoring.probabilities[position])
                    expected_loss = probability * float(row.amount)
                    eligible = expected_loss > self.review_cost
                    decision = "backlog" if eligible else "approve"
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
                            feature_version=scoring.feature_version,
                            score_kind=scoring.score_kind,
                            policy_version=POLICY_VERSION,
                            review_cost=self.review_cost,
                            loss_fraction=1.0,
                            scored_at=scoring.scored_at,
                            feature_snapshot={
                                column: (
                                    str(getattr(row, column))
                                    if column == "type"
                                    else float(getattr(row, column))
                                )
                                for column in MODEL_FEATURES
                            },
                        )
                    )
                    if eligible:
                        session.add(
                            ReviewQueue(
                                event_id=message.event_id,
                                created_at=now,
                                transaction_id=transaction_id,
                                queue_date=now.date(),
                                rank=int(rank_by_transaction[transaction_id]),
                                expected_loss=expected_loss,
                                priority_score=float(score_by_transaction[transaction_id]),
                                status="backlog",
                            )
                        )
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

                session.flush()
                allocated = allocate_capacity(session, now.date(), self.review_capacity)
                reviews_written = len(allocated.intersection(set(featured.transaction_id)))
                for row in warehouse_rows:
                    row["decision"] = session.get(
                        OperationalTransaction, row["transaction_id"]
                    ).decision
                for transaction_id in allocated.difference(set(featured.transaction_id)):
                    previous = session.get(OperationalTransaction, transaction_id)
                    warehouse_rows.append(
                        {
                            "event_id": previous.event_id,
                            "transaction_id": previous.transaction_id,
                            "ingested_at": previous.ingested_at,
                            "step": previous.step,
                            "transaction_type": previous.transaction_type,
                            "amount": previous.amount,
                            "fraud_probability": previous.fraud_probability,
                            "expected_loss": previous.expected_loss,
                            "risk_band": previous.risk_band,
                            "decision": previous.decision,
                            "model_version": previous.model_version,
                        }
                    )

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
