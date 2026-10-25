from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import joblib

from riskqueue.features.schema import FEATURE_VERSION


@dataclass(frozen=True)
class ModelMetadata:
    model_version: str
    algorithm: str
    training_end_step: int
    average_precision: float
    roc_auc: float
    brier: float
    decision_threshold: float
    feature_version: str = FEATURE_VERSION
    score_kind: str = "model_probability"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def save_artifact(model, metadata: ModelMetadata, directory: str | Path) -> None:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, target / "riskqueue.joblib")
    (target / "metadata.json").write_text(json.dumps(asdict(metadata), indent=2) + "\n")
    digest = _artifact_digest(
        (target / "metadata.json").read_bytes(), (target / "riskqueue.joblib").read_bytes()
    )
    (target / "artifact.sha256").write_text(digest + "\n")


def _artifact_digest(metadata: bytes, model: bytes) -> str:
    digest = hashlib.sha256(len(metadata).to_bytes(8, "big"))
    digest.update(metadata)
    digest.update(model)
    return digest.hexdigest()


def load_artifact(directory: str | Path, *, expected_sha256: str | None = None):
    """Load only bytes pinned by trusted deployment configuration, never a local sidecar."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("MODEL_ARTIFACT_SHA256 must be a lowercase SHA-256 fingerprint")
    target = Path(directory)
    metadata_bytes = (target / "metadata.json").read_bytes()
    model_bytes = (target / "riskqueue.joblib").read_bytes()
    if not secrets.compare_digest(_artifact_digest(metadata_bytes, model_bytes), expected_sha256):
        raise ValueError("Model artifact integrity verification failed")
    metadata = json.loads(metadata_bytes)
    # Load the exact verified snapshot, preventing replacement between verification and loading.
    return joblib.load(io.BytesIO(model_bytes)), metadata
