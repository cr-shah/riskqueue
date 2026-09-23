from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from riskqueue.modeling.artifacts import load_artifact


def demo_probability(transaction_id: str, step: int, transaction_type: str, amount: float) -> float:
    """Stable development scorer used when no trained artifact is configured."""
    risky_type = {"TRANSFER": 0.34, "CASH_OUT": 0.22}.get(transaction_type, 0.03)
    amount_component = min(0.42, amount / 100_000)
    hour_component = 0.08 if step % 24 < 5 else 0.0
    jitter = int(hashlib.sha256(transaction_id.encode()).hexdigest()[:4], 16) / 65535 * 0.04
    return min(0.99, 0.015 + risky_type + amount_component + hour_component + jitter)


def risk_band(probability: float) -> str:
    if probability >= 0.75:
        return "critical"
    if probability >= 0.4:
        return "high"
    if probability >= 0.1:
        return "guarded"
    return "low"


@dataclass(frozen=True)
class ScoringResult:
    model_version: str
    probabilities: np.ndarray


class FraudScorer:
    """Load the configured trained model or use the deterministic local scorer."""

    def __init__(self, artifact_directory: str | Path | None = None):
        configured = artifact_directory or os.getenv("MODEL_ARTIFACT_DIR")
        self.model = None
        self.model_version = os.getenv("MODEL_VERSION", "demo-policy-v1")
        if configured and (Path(configured) / "riskqueue.joblib").exists():
            self.model, metadata = load_artifact(configured)
            self.model_version = metadata["model_version"]

    def score(self, featured: pd.DataFrame) -> ScoringResult:
        if self.model is not None:
            probabilities = np.asarray(self.model.predict_proba(featured), dtype=float)
            if probabilities.ndim == 2:
                probabilities = probabilities[:, 1]
        else:
            probabilities = np.array(
                [
                    demo_probability(
                        str(row.transaction_id), int(row.step), str(row.type), float(row.amount)
                    )
                    for row in featured.itertuples()
                ],
                dtype=float,
            )
        return ScoringResult(self.model_version, probabilities)
