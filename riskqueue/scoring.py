from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from riskqueue.features.schema import FEATURE_VERSION
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
    feature_version: str = FEATURE_VERSION
    scored_at: datetime | None = None
    score_kind: str = "model_probability"


class FraudScorer:
    """Load the configured trained model or use the deterministic local scorer."""

    def __init__(self, artifact_directory: str | Path | None = None):
        configured = artifact_directory or os.getenv("MODEL_ARTIFACT_DIR")
        mode = os.getenv("SCORING_MODE") or ("trained" if configured else "demo")
        if mode not in {"demo", "trained"}:
            raise ValueError("SCORING_MODE must be demo or trained")
        if mode == "demo" and configured:
            raise ValueError("SCORING_MODE=demo cannot use MODEL_ARTIFACT_DIR")
        if mode == "trained" and not configured:
            raise FileNotFoundError("SCORING_MODE=trained requires MODEL_ARTIFACT_DIR")
        self.model = None
        self.model_version = os.getenv("MODEL_VERSION", "demo-policy-v1")
        self.feature_version = FEATURE_VERSION
        self.score_kind = "development_heuristic"
        if configured:
            if not (Path(configured) / "riskqueue.joblib").is_file():
                raise FileNotFoundError(f"Configured model artifact is missing: {configured}")
            self.model, metadata = load_artifact(configured)
            self.model_version = metadata["model_version"]
            if metadata.get("feature_version") != FEATURE_VERSION:
                raise ValueError(
                    "Model artifact feature version does not match the scoring pipeline"
                )
            self.score_kind = metadata.get("score_kind", "model_probability")
            if self.score_kind not in {"model_probability", "calibrated_probability"}:
                raise ValueError("Model artifact score kind is not supported")

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
        if (
            len(probabilities) != len(featured)
            or not np.isfinite(probabilities).all()
            or (probabilities < 0).any()
            or (probabilities > 1).any()
        ):
            raise ValueError("Model output must contain one finite score in [0, 1] per row")
        return ScoringResult(
            self.model_version,
            probabilities,
            feature_version=self.feature_version,
            scored_at=datetime.now(UTC),
            score_kind=self.score_kind,
        )


@lru_cache(maxsize=16)
def _cached_runtime_scorer(
    mode: str | None, artifact: str | None, version: str | None
) -> FraudScorer:
    return FraudScorer()


def runtime_scorer() -> FraudScorer:
    """Reuse the loaded artifact; restart the API to activate an artifact at the same path."""
    return _cached_runtime_scorer(
        os.getenv("SCORING_MODE"), os.getenv("MODEL_ARTIFACT_DIR"), os.getenv("MODEL_VERSION")
    )
