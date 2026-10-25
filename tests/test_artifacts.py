import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import joblib
import pytest

from riskqueue.modeling.artifacts import ModelMetadata, load_artifact, save_artifact
from riskqueue.scoring import FraudScorer, runtime_scorer


def fixture_artifact(tmp_path):
    save_artifact({"fixture": True}, ModelMetadata("v1", "fixture", 0, 0, 0, 0, 0.5), tmp_path)
    metadata = (tmp_path / "metadata.json").read_bytes()
    model = (tmp_path / "riskqueue.joblib").read_bytes()
    # Independently specify the hashed byte framing, including metadata.
    return hashlib.sha256(len(metadata).to_bytes(8, "big") + metadata + model).hexdigest()


def test_saved_artifact_fingerprint_round_trips_model_and_metadata(tmp_path):
    expected = fixture_artifact(tmp_path)
    assert (tmp_path / "artifact.sha256").read_text().strip() == expected
    model, metadata = load_artifact(tmp_path, expected_sha256=expected)
    assert model == {"fixture": True}
    assert metadata["model_version"] == "v1"


@pytest.mark.parametrize("filename", ["riskqueue.joblib", "metadata.json"])
def test_artifact_rejects_tampered_model_or_metadata(tmp_path, filename):
    expected = fixture_artifact(tmp_path)
    target = tmp_path / filename
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError, match="integrity"):
        load_artifact(tmp_path, expected_sha256=expected)


class DeserializationMarker:
    def __init__(self, destination):
        self.destination = destination

    def __reduce__(self):
        return Path.touch, (self.destination,)


def test_untrusted_pickle_never_executes_before_digest_check(tmp_path):
    expected = fixture_artifact(tmp_path)
    marker = tmp_path / "deserialized"
    joblib.dump(DeserializationMarker(marker), tmp_path / "riskqueue.joblib")
    with pytest.raises(ValueError, match="integrity"):
        load_artifact(tmp_path, expected_sha256=expected)
    assert not marker.exists()


@pytest.mark.parametrize("digest", [None, "", "not-a-digest", "0" * 63])
def test_trained_scorer_requires_valid_deployment_fingerprint(tmp_path, monkeypatch, digest):
    fixture_artifact(tmp_path)
    monkeypatch.delenv("MODEL_ARTIFACT_SHA256", raising=False)
    if digest is not None:
        monkeypatch.setenv("MODEL_ARTIFACT_SHA256", digest)
    with pytest.raises(ValueError, match="MODEL_ARTIFACT_SHA256"):
        FraudScorer(tmp_path)


def test_runtime_cache_rechecks_changed_trust_pin(tmp_path, monkeypatch):
    expected = fixture_artifact(tmp_path)
    monkeypatch.setenv("MODEL_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("SCORING_MODE", "trained")
    monkeypatch.setenv("MODEL_ARTIFACT_SHA256", expected)
    assert runtime_scorer().model_version == "v1"
    monkeypatch.setenv("MODEL_ARTIFACT_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="integrity"):
        runtime_scorer()


def test_verified_bytes_are_loaded_even_if_disk_file_is_replaced(tmp_path, monkeypatch):
    expected = fixture_artifact(tmp_path)
    original_load = joblib.load

    def replace_then_load(source):
        joblib.dump({"replacement": True}, tmp_path / "riskqueue.joblib")
        return original_load(source)

    monkeypatch.setattr(joblib, "load", replace_then_load)
    model, metadata = load_artifact(tmp_path, expected_sha256=expected)
    assert model == {"fixture": True}
    assert metadata == json.loads((tmp_path / "metadata.json").read_text())


def test_model_metadata_records_each_creation_time():
    first_time = datetime(2026, 1, 1, tzinfo=UTC)
    second_time = datetime(2026, 1, 2, tzinfo=UTC)
    with patch("riskqueue.modeling.artifacts.datetime") as clock:
        clock.now.side_effect = [first_time, second_time]
        first = ModelMetadata("v1", "logistic", 10, 0.1, 0.7, 0.2, 0.3)
        second = ModelMetadata("v2", "logistic", 20, 0.2, 0.8, 0.1, 0.4)

    assert first.created_at == first_time.isoformat()
    assert second.created_at == second_time.isoformat()
