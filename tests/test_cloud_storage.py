import json

import pytest

from riskqueue.cloud.storage import LocalObjectStore


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
