from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

try:
    from sklearn.frozen import FrozenEstimator
except ImportError:  # scikit-learn 1.5 uses the older prefit calibration API.
    FrozenEstimator = None

from riskqueue.features.schema import CATEGORICAL_FEATURES, MODEL_FEATURES, assert_leakage_safe


@dataclass
class TrainedModel:
    name: str
    estimator: object
    feature_names: list[str]

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return self.estimator.predict_proba(frame[self.feature_names])[:, 1]


def _preprocessor() -> ColumnTransformer:
    numeric = [c for c in MODEL_FEATURES if c not in CATEGORICAL_FEATURES]
    return ColumnTransformer(
        [
            (
                "numeric",
                Pipeline([("impute", SimpleImputer()), ("scale", StandardScaler())]),
                numeric,
            ),
            (
                "category",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                CATEGORICAL_FEATURES,
            ),
        ]
    )


def train_logistic(frame: pd.DataFrame, labels) -> TrainedModel:
    assert_leakage_safe(MODEL_FEATURES)
    estimator = Pipeline(
        [
            ("prepare", _preprocessor()),
            ("model", LogisticRegression(class_weight="balanced", max_iter=500, C=0.5)),
        ]
    )
    estimator.fit(frame[MODEL_FEATURES], labels)
    return TrainedModel("Logistic Regression", estimator, MODEL_FEATURES)


def train_boosted(frame: pd.DataFrame, labels) -> TrainedModel:
    """Train XGBoost when installed, otherwise a deterministic sklearn fallback."""
    assert_leakage_safe(MODEL_FEATURES)
    try:
        from xgboost import XGBClassifier

        positives = max(1, int(np.sum(labels)))
        scale = max(1.0, (len(labels) - positives) / positives)
        model = XGBClassifier(
            n_estimators=180,
            max_depth=4,
            learning_rate=0.06,
            subsample=0.85,
            colsample_bytree=0.85,
            objective="binary:logistic",
            eval_metric="aucpr",
            scale_pos_weight=scale,
            random_state=42,
            n_jobs=2,
        )
        name = "XGBoost"
    except Exception:  # XGBoost may be installed while its native OpenMP runtime is unavailable.
        model = HistGradientBoostingClassifier(
            max_iter=160,
            max_leaf_nodes=24,
            learning_rate=0.07,
            l2_regularization=0.2,
            random_state=42,
        )
        name = "Histogram Gradient Boosting (demo fallback)"
    estimator = Pipeline([("prepare", _preprocessor()), ("model", model)])
    estimator.fit(frame[MODEL_FEATURES], labels)
    return TrainedModel(name, estimator, MODEL_FEATURES)


def calibrate_prefit(model: TrainedModel, validation: pd.DataFrame, labels) -> TrainedModel:
    if FrozenEstimator is None:
        calibrated = CalibratedClassifierCV(model.estimator, method="sigmoid", cv="prefit")
    else:
        indices = np.arange(len(validation))
        calibrated = CalibratedClassifierCV(
            FrozenEstimator(model.estimator),
            method="sigmoid",
            cv=[(indices, indices)],
        )
    calibrated.fit(validation[model.feature_names], labels)
    return TrainedModel(f"{model.name} + sigmoid calibration", calibrated, model.feature_names)
