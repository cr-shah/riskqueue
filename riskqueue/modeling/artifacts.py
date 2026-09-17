from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import joblib


@dataclass(frozen=True)
class ModelMetadata:
    model_version: str
    algorithm: str
    training_end_step: int
    average_precision: float
    roc_auc: float
    brier: float
    decision_threshold: float
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def save_artifact(model, metadata: ModelMetadata, directory: str | Path) -> None:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, target / "riskqueue.joblib")
    (target / "metadata.json").write_text(json.dumps(asdict(metadata), indent=2) + "\n")


def load_artifact(directory: str | Path):
    target = Path(directory)
    return joblib.load(target / "riskqueue.joblib"), json.loads(
        (target / "metadata.json").read_text()
    )
