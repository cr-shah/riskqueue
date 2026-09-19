from __future__ import annotations

import argparse
from pathlib import Path

from sklearn.metrics import average_precision_score

from riskqueue.data.loader import load_paysim
from riskqueue.data.splits import temporal_split
from riskqueue.features.pipeline import build_features
from riskqueue.modeling.artifacts import ModelMetadata, save_artifact
from riskqueue.modeling.evaluate import classification_metrics
from riskqueue.modeling.train import calibrate_prefit, train_boosted, train_logistic
from riskqueue.reporting import generate_report_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate RiskQueue on PaySim")
    parser.add_argument("csv")
    parser.add_argument("--sample-rows", type=int)
    args = parser.parse_args()
    featured = build_features(load_paysim(args.csv, sample_rows=args.sample_rows))
    split = temporal_split(featured)
    base_models = [
        train_logistic(split.train, split.train.isFraud),
        train_boosted(split.train, split.train.isFraud),
    ]
    base_validation = {
        model.name: model.predict_proba(split.validation) for model in base_models
    }
    selected_base = max(
        base_models,
        key=lambda model: average_precision_score(
            split.validation.isFraud, base_validation[model.name]
        ),
    )
    calibrated = {
        model.name: calibrate_prefit(model, split.validation, split.validation.isFraud)
        for model in base_models
    }
    models = list(calibrated.values())
    best = calibrated[selected_base.name]
    validation = best.predict_proba(split.validation)
    test_probabilities = {m.name: m.predict_proba(split.test) for m in models}
    summary = generate_report_artifacts(
        split.test,
        test_probabilities,
        "artifacts/figures",
        (
            split.validation.isFraud.to_numpy(),
            validation,
            split.validation.amount.to_numpy(),
        ),
        selected_model=best.name,
    )
    metrics = classification_metrics(
        split.test.isFraud, test_probabilities[best.name], summary["selected_threshold"]
    )
    metadata = ModelMetadata(
        "riskqueue-v1",
        best.name,
        int(split.train.step.max()),
        metrics["average_precision"],
        metrics["roc_auc"],
        metrics["brier"],
        summary["selected_threshold"],
    )
    save_artifact(best, metadata, Path("artifacts/models"))
    print(f"Saved {best.name}; held-out AP={metrics['average_precision']:.4f}")


if __name__ == "__main__":
    main()
