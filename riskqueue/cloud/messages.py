from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ObjectReference(BaseModel):
    bucket: str = Field(min_length=1, max_length=255)
    key: str = Field(min_length=1, max_length=1024)
    etag: str | None = Field(default=None, max_length=128)
    version_id: str | None = Field(default=None, max_length=1024)


class ProcessingMessage(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    event_id: str = Field(min_length=1, max_length=128)
    batch_id: str = Field(min_length=1, max_length=128)
    object: ObjectReference
    occurred_at: datetime
    source: str = Field(min_length=1, max_length=120)

    @field_validator("occurred_at")
    @classmethod
    def timestamp_must_have_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @property
    def idempotency_key(self) -> str:
        material = "|".join(
            [
                self.schema_version,
                self.event_id,
                self.object.bucket,
                self.object.key,
                self.object.version_id or "",
                self.object.etag or "",
            ]
        )
        return hashlib.sha256(material.encode()).hexdigest()


def deterministic_event_id(reference: ObjectReference) -> str:
    material = "|".join(
        [reference.bucket, reference.key, reference.version_id or "", reference.etag or ""]
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def message_for_object(
    reference: ObjectReference,
    *,
    batch_id: str | None = None,
    event_id: str | None = None,
    source: str = "s3",
    occurred_at: datetime | None = None,
) -> ProcessingMessage:
    return ProcessingMessage(
        event_id=event_id or deterministic_event_id(reference),
        batch_id=batch_id or reference.key.rsplit("/", 1)[-1],
        object=reference,
        occurred_at=occurred_at or datetime.now(UTC),
        source=source,
    )
