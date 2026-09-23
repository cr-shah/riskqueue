from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any
from urllib.parse import unquote_plus

from pydantic import BaseModel, Field

from riskqueue.cloud.messages import ObjectReference, message_for_object
from riskqueue.cloud.queue import SQSProcessingQueue
from riskqueue.observability import log_event

logger = logging.getLogger("riskqueue.lambda")


class IngestionRequest(BaseModel):
    bucket: str = Field(min_length=1, max_length=255)
    key: str = Field(min_length=1, max_length=1024)
    batch_id: str | None = Field(default=None, max_length=128)
    event_id: str | None = Field(default=None, max_length=128)
    etag: str | None = Field(default=None, max_length=128)
    version_id: str | None = Field(default=None, max_length=1024)
    source: str = Field(default="request", min_length=1, max_length=120)
    occurred_at: datetime | None = None


def _requests_from_event(event: dict[str, Any]) -> list[IngestionRequest]:
    if "Records" in event:
        requests = []
        for record in event["Records"]:
            if record.get("eventSource") != "aws:s3":
                raise ValueError("Only S3 event records are supported")
            s3 = record["s3"]
            requests.append(
                IngestionRequest(
                    bucket=s3["bucket"]["name"],
                    key=unquote_plus(s3["object"]["key"]),
                    etag=s3["object"].get("eTag"),
                    version_id=s3["object"].get("versionId"),
                    source="s3-notification",
                    occurred_at=record.get("eventTime"),
                )
            )
        return requests
    body = event.get("body", event)
    if isinstance(body, str):
        body = json.loads(body)
    return [IngestionRequest.model_validate(body)]


def lambda_handler(event: dict[str, Any], context: Any, queue=None) -> dict[str, Any]:
    """Validate object metadata and enqueue small processing messages."""
    processing_queue = queue or SQSProcessingQueue(os.environ["PROCESSING_QUEUE_URL"])
    sent = []
    for request in _requests_from_event(event):
        reference = ObjectReference(
            bucket=request.bucket,
            key=request.key,
            etag=request.etag,
            version_id=request.version_id,
        )
        message = message_for_object(
            reference,
            batch_id=request.batch_id,
            event_id=request.event_id,
            source=request.source,
            occurred_at=request.occurred_at,
        )
        message_id = processing_queue.send(message)
        sent.append({"event_id": message.event_id, "message_id": message_id})
        log_event(
            logger,
            "ingestion_enqueued",
            event_id=message.event_id,
            batch_id=message.batch_id,
            stage="enqueue",
            status="accepted",
        )
    return {"statusCode": 202, "body": json.dumps({"accepted": len(sent), "messages": sent})}
