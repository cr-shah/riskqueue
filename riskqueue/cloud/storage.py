from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Protocol

DEFAULT_MAX_INGESTION_BYTES = 10 * 1024 * 1024
MAX_ALLOWED_INGESTION_BYTES = 100 * 1024 * 1024


def _ingestion_limit(max_bytes: int | None) -> int:
    if max_bytes is None:
        try:
            max_bytes = int(os.getenv("MAX_INGESTION_BYTES", str(DEFAULT_MAX_INGESTION_BYTES)))
        except ValueError:
            raise ValueError("MAX_INGESTION_BYTES must be an integer") from None
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_ALLOWED_INGESTION_BYTES:
        raise ValueError("MAX_INGESTION_BYTES must be between 1 and 104857600")
    return max_bytes


def _bounded_read(stream: BinaryIO, max_bytes: int) -> bytes:
    payload = bytearray()
    while True:
        chunk = stream.read(min(64 * 1024, max_bytes - len(payload) + 1))
        if not chunk:
            return bytes(payload)
        if len(payload) + len(chunk) > max_bytes:
            raise ValueError("Object exceeds ingestion byte limit")
        payload.extend(chunk)


class ObjectStore(Protocol):
    def get_bytes(self, bucket: str, key: str) -> bytes: ...

    def put_bytes(
        self, bucket: str, key: str, payload: bytes, *, source: str, content_type: str
    ) -> None: ...


class S3ObjectStore:
    def __init__(self, client=None, *, max_bytes: int | None = None):
        self.max_bytes = _ingestion_limit(max_bytes)
        if client is None:
            import boto3

            client = boto3.client("s3", region_name=os.getenv("AWS_REGION"))
        self.client = client

    def get_bytes(self, bucket: str, key: str) -> bytes:
        response = self.client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        try:
            declared_length = response.get("ContentLength")
            if declared_length is not None and declared_length > self.max_bytes:
                raise ValueError("Object exceeds ingestion byte limit")
            return _bounded_read(body, self.max_bytes)
        finally:
            body.close()

    def put_bytes(
        self, bucket: str, key: str, payload: bytes, *, source: str, content_type: str
    ) -> None:
        self.client.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType=content_type,
            IfNoneMatch="*",
            Metadata={"ingested-at": datetime.now(UTC).isoformat(), "source": source},
            ServerSideEncryption=os.getenv("S3_SERVER_SIDE_ENCRYPTION", "AES256"),
        )


class LocalObjectStore:
    """Filesystem-backed raw object store with immutable write semantics."""

    def __init__(self, root: str | Path, *, max_bytes: int | None = None):
        self.max_bytes = _ingestion_limit(max_bytes)
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, bucket: str, key: str) -> Path:
        target = (self.root / bucket / key).resolve()
        if self.root not in target.parents:
            raise ValueError("object key escapes the configured storage root")
        return target

    def get_bytes(self, bucket: str, key: str) -> bytes:
        with self._path(bucket, key).open("rb") as handle:
            if os.fstat(handle.fileno()).st_size > self.max_bytes:
                raise ValueError("Object exceeds ingestion byte limit")
            return _bounded_read(handle, self.max_bytes)

    def put_bytes(
        self, bucket: str, key: str, payload: bytes, *, source: str, content_type: str
    ) -> None:
        target = self._path(bucket, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(payload)
        metadata = {
            "ingested_at": datetime.now(UTC).isoformat(),
            "source": source,
            "content_type": content_type,
        }
        target.with_suffix(target.suffix + ".metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )


def build_object_store() -> ObjectStore:
    backend = os.getenv("OBJECT_STORE_BACKEND", "local").lower()
    if backend == "s3":
        return S3ObjectStore()
    if backend == "local":
        return LocalObjectStore(os.getenv("LOCAL_OBJECT_STORE_ROOT", "data/raw-events"))
    raise ValueError(f"Unsupported OBJECT_STORE_BACKEND: {backend}")
