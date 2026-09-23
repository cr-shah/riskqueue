from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    def get_bytes(self, bucket: str, key: str) -> bytes: ...

    def put_bytes(
        self, bucket: str, key: str, payload: bytes, *, source: str, content_type: str
    ) -> None: ...


class S3ObjectStore:
    def __init__(self, client=None):
        if client is None:
            import boto3

            client = boto3.client("s3", region_name=os.getenv("AWS_REGION"))
        self.client = client

    def get_bytes(self, bucket: str, key: str) -> bytes:
        response = self.client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

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

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, bucket: str, key: str) -> Path:
        target = (self.root / bucket / key).resolve()
        if self.root not in target.parents:
            raise ValueError("object key escapes the configured storage root")
        return target

    def get_bytes(self, bucket: str, key: str) -> bytes:
        return self._path(bucket, key).read_bytes()

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
