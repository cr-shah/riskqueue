import io
import json

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from riskqueue.cloud.storage import LocalObjectStore, S3ObjectStore, build_object_store


class FragmentedStream(io.BytesIO):
    def read(self, size=-1):
        return super().read(min(size, 2) if size >= 0 else 2)


def s3_fixture(payload, declared_length, *, fragmented=False, stream=None):
    # Stub only the network boundary, using the real boto3 parameter validation and body.
    client = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    if stream is None:
        stream = FragmentedStream(payload) if fragmented else io.BytesIO(payload)
    body = StreamingBody(stream, len(payload))
    response = {"Body": body}
    if declared_length is not None:
        response["ContentLength"] = declared_length
    stub = Stubber(client)
    stub.add_response("get_object", response, {"Bucket": "raw", "Key": "batch.csv"})
    return client, stream, stub


def test_local_download_accepts_limit_and_rejects_one_byte_over(tmp_path):
    store = LocalObjectStore(tmp_path, max_bytes=4)
    store.put_bytes("raw", "ok.csv", b"1234", source="test", content_type="text/csv")
    store.put_bytes("raw", "large.csv", b"12345", source="test", content_type="text/csv")
    assert store.get_bytes("raw", "ok.csv") == b"1234"
    with pytest.raises(ValueError, match="byte limit"):
        store.get_bytes("raw", "large.csv")


@pytest.mark.parametrize("declared_length", [None, 4, 5])
def test_s3_download_rejects_oversize_even_with_missing_or_false_metadata(declared_length):
    client, stream, stub = s3_fixture(b"12345", declared_length)
    with stub, pytest.raises(ValueError, match="byte limit"):
        S3ObjectStore(client, max_bytes=4).get_bytes("raw", "batch.csv")
    assert stream.closed


def test_s3_rejects_declared_oversize_without_reading_body():
    class MustNotRead(io.BytesIO):
        def read(self, size=-1):
            pytest.fail("Oversized declared object was read")

    body = MustNotRead(b"12345")
    client, _, stub = s3_fixture(b"12345", 5, stream=body)
    with stub, pytest.raises(ValueError, match="byte limit"):
        S3ObjectStore(client, max_bytes=4).get_bytes("raw", "batch.csv")
    assert body.closed


@pytest.mark.parametrize("fragmented", [False, True])
def test_s3_download_reads_all_fragments_at_exact_limit_and_closes(fragmented):
    client, stream, stub = s3_fixture(b"1234", 4, fragmented=fragmented)
    with stub:
        assert S3ObjectStore(client, max_bytes=4).get_bytes("raw", "batch.csv") == b"1234"
    assert stream.closed


def test_s3_download_closes_body_on_read_error():
    class BrokenStream(io.BytesIO):
        def read(self, size=-1):
            raise OSError("read failed")

    stream = BrokenStream()
    client, _, stub = s3_fixture(b"", None, stream=stream)
    with stub, pytest.raises(OSError, match="read failed"):
        S3ObjectStore(client, max_bytes=4).get_bytes("raw", "batch.csv")
    assert stream.closed


@pytest.mark.parametrize("limit", ["0", "-1", "invalid", "104857601"])
def test_object_store_rejects_invalid_limit_configuration(tmp_path, monkeypatch, limit):
    monkeypatch.setenv("OBJECT_STORE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_OBJECT_STORE_ROOT", str(tmp_path))
    monkeypatch.setenv("MAX_INGESTION_BYTES", limit)
    with pytest.raises(ValueError, match="MAX_INGESTION_BYTES"):
        build_object_store()


def test_object_store_factory_applies_configured_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("OBJECT_STORE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_OBJECT_STORE_ROOT", str(tmp_path))
    monkeypatch.setenv("MAX_INGESTION_BYTES", "4")
    store = build_object_store()
    store.put_bytes("raw", "large.csv", b"12345", source="test", content_type="text/csv")
    with pytest.raises(ValueError, match="byte limit"):
        store.get_bytes("raw", "large.csv")


def test_local_store_preserves_payload_and_ingestion_metadata(tmp_path):
    store = LocalObjectStore(tmp_path)
    store.put_bytes("raw", "incoming/a.csv", b"payload", source="test", content_type="text/csv")

    assert store.get_bytes("raw", "incoming/a.csv") == b"payload"
    metadata = json.loads((tmp_path / "raw/incoming/a.csv.metadata.json").read_text())
    assert metadata["source"] == "test"
    assert metadata["content_type"] == "text/csv"
    assert metadata["ingested_at"].endswith("+00:00")


def test_local_store_is_immutable(tmp_path):
    store = LocalObjectStore(tmp_path)
    store.put_bytes("raw", "a.csv", b"first", source="test", content_type="text/csv")

    with pytest.raises(FileExistsError):
        store.put_bytes("raw", "a.csv", b"second", source="test", content_type="text/csv")


def test_local_store_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        LocalObjectStore(tmp_path).get_bytes("raw", "../../secret")
