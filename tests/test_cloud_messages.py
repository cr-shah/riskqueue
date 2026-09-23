from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from riskqueue.cloud.messages import ObjectReference, ProcessingMessage, message_for_object


def test_message_schema_has_stable_idempotency_key():
    reference = ObjectReference(bucket="raw", key="incoming/batch.csv", etag="abc")
    first = message_for_object(reference, occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
    second = message_for_object(reference, occurred_at=datetime(2026, 1, 2, tzinfo=UTC))

    assert first.event_id == second.event_id
    assert first.idempotency_key == second.idempotency_key
    assert len(first.idempotency_key) == 64


def test_message_rejects_naive_timestamp():
    with pytest.raises(ValidationError, match="timezone"):
        ProcessingMessage(
            event_id="evt",
            batch_id="batch",
            object=ObjectReference(bucket="raw", key="batch.csv"),
            occurred_at=datetime(2026, 1, 1),
            source="test",
        )
