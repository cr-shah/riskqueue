from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import confusion_matrix, precision_recall_curve, roc_curve

from riskqueue.decisions.economics import realized_decision_cost
from riskqueue.decisions.review_queue import build_review_queue, queue_metrics
from riskqueue.decisions.thresholds import compare_thresholds, select_threshold
from riskqueue.modeling.evaluate import classification_metrics
from riskqueue.monitoring.drift import drift_status, population_stability_index

COLORS = {"navy": "#16324F", "blue": "#2E86AB", "amber": "#F6AE2D", "red": "#D1495B"}


def _finish(fig, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def generate_report_artifacts(
    test: pd.DataFrame,
    model_probabilities: dict[str, np.ndarray],
    output: str | Path,
    threshold_source: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
    *,
    selected_model: str,
) -> dict:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    labels, amounts = test.isFraud.to_numpy(), test.amount.to_numpy()
    metrics = {
        name: classification_metrics(labels, probs) for name, probs in model_probabilities.items()
    }
    if selected_model not in metrics:
        raise ValueError(f"Selected model is missing probabilities: {selected_model}")
    best_name = selected_model
    best_probs = model_probabilities[best_name]

    fig, ax = plt.subplots(figsize=(8.4, 5.2))
    prevalence = labels.mean()
    for i, (name, probs) in enumerate(model_probabilities.items()):
        precision, recall, _ = precision_recall_curve(labels, probs)
        ax.plot(
            recall,
            precision,
            lw=2.5,
            label=f"{name}  AP {metrics[name]['average_precision']:.3f}",
            color=[COLORS["blue"], COLORS["amber"]][i % 2],
        )
    ax.axhline(prevalence, ls="--", color="#8795A1", label=f"No-skill {prevalence:.2%}")
    ax.set(
        title="Rare-event ranking: precision–recall",
        xlabel="Recall",
        ylabel="Precision",
        xlim=(0, 1),
        ylim=(0, 1),
    )
    ax.grid(alpha=0.18)
    ax.legend(frameon=False)
    _finish(fig, output / "precision_recall.png")

    # Diagnostics used by the model-performance dashboard.
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2))
    false_positive_rate, true_positive_rate, _ = roc_curve(labels, best_probs)
    axes[0].plot(false_positive_rate, true_positive_rate, color=COLORS["blue"], lw=2.5)
    axes[0].plot([0, 1], [0, 1], ls="--", color="#97A6B2")
    axes[0].set(
        title=f"ROC curve · AUC {metrics[best_name]['roc_auc']:.3f}",
        xlabel="False-positive rate",
        ylabel="True-positive rate",
    )
    observed, predicted = calibration_curve(labels, best_probs, n_bins=8, strategy="quantile")
    axes[1].plot(predicted, observed, marker="o", color=COLORS["amber"], lw=2.5)
    axes[1].plot([0, 1], [0, 1], ls="--", color="#97A6B2")
    axes[1].set(
        title=f"Calibration · Brier {metrics[best_name]['brier']:.3f}",
        xlabel="Mean predicted probability",
        ylabel="Observed fraud rate",
    )
    matrix = confusion_matrix(labels, best_probs >= 0.15)
    image = axes[2].imshow(matrix, cmap="Blues")
    for (row, column), value in np.ndenumerate(matrix):
        axes[2].text(column, row, f"{value:,}", ha="center", va="center", fontsize=13)
    axes[2].set(
        title="Confusion matrix · threshold 0.15",
        xlabel="Predicted class",
        ylabel="Actual class",
        xticks=[0, 1],
        yticks=[0, 1],
    )
    fig.colorbar(image, ax=axes[2], fraction=0.045)
    for ax in axes:
        ax.grid(alpha=0.15)
    _finish(fig, output / "model_diagnostics.png")

    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    ax.hist(
        best_probs[labels == 0],
        bins=30,
        alpha=0.7,
        density=True,
        label="Not fraud",
        color=COLORS["blue"],
    )
    ax.hist(
        best_probs[labels == 1],
        bins=30,
        alpha=0.68,
        density=True,
        label="Fraud",
        color=COLORS["red"],
    )
    ax.set(
        title="Held-out risk-score distribution",
        xlabel="Predicted fraud probability",
        ylabel="Density",
    )
    ax.grid(alpha=0.15)
    ax.legend(frameon=False)
    _finish(fig, output / "score_distribution.png")
    capacities = sorted(set([25, 50, 100, 250, 500, min(750, len(test)), min(1000, len(test))]))
    capacity_rows = []
    fig, ax = plt.subplots(figsize=(8.4, 5.2))
    for strategy, label, color in [
        ("probability", "Fraud probability", COLORS["blue"]),
        ("expected_loss", "Expected loss", COLORS["amber"]),
        ("expected_review_value", "Expected review value", COLORS["red"]),
    ]:
        captures = []
        for capacity in capacities:
            queue = build_review_queue(test, best_probs, capacity, strategy)
            row = {"strategy": strategy, "capacity": capacity, **queue_metrics(queue, test)}
            capacity_rows.append(row)
            captures.append(row["fraud_value_capture"] * 100)
        ax.plot(capacities, captures, marker="o", lw=2.4, label=label, color=color)
    ax.set(
        title="Analyst capacity vs. fraud value captured",
        xlabel="Transactions reviewed",
        ylabel="Fraud dollars captured (%)",
    )
    ax.grid(alpha=0.18)
    ax.legend(frameon=False)
    _finish(fig, output / "capacity_value.png")

    if threshold_source is None:
        threshold_source = (labels, best_probs, amounts)
    val_labels, val_probs, val_amounts = threshold_source
    rows = compare_thresholds(val_labels, val_probs, val_amounts)
    f1_threshold, cost_threshold = select_threshold(rows, "f1"), select_threshold(rows, "cost")
    thresholds = np.array([r["threshold"] for r in rows])
    review_costs, missed, totals = [], [], []
    for threshold in thresholds:
        decisions = val_probs >= threshold
        rc = float(decisions.sum() * 4.0)
        total = realized_decision_cost(val_labels, decisions, val_amounts)
        review_costs.append(rc)
        missed.append(total - rc)
        totals.append(total)
    fig, ax = plt.subplots(figsize=(8.4, 5.2))
    ax.plot(thresholds, missed, label="Missed fraud loss", color=COLORS["red"], lw=2)
    ax.plot(thresholds, review_costs, label="Review cost", color=COLORS["blue"], lw=2)
    ax.plot(thresholds, totals, label="Total modeled cost", color=COLORS["navy"], lw=3)
    for value, label in [(0.5, "0.50"), (f1_threshold, "F1"), (cost_threshold, "Cost")]:
        ax.axvline(value, ls="--", alpha=0.65, label=f"{label}: {value:.2f}")
    ax.set(
        title="The operating threshold changes the cost tradeoff",
        xlabel="Decision threshold",
        ylabel="Illustrative modeled cost ($)",
    )
    ax.grid(alpha=0.18)
    ax.legend(frameon=False, ncol=2)
    _finish(fig, output / "threshold_cost.png")

    chosen = cost_threshold
    threshold_table = []
    for policy, threshold in [
        ("Default 0.50", 0.5),
        ("F1-optimal", f1_threshold),
        ("Cost-optimized", cost_threshold),
    ]:
        decisions = best_probs >= threshold
        m = classification_metrics(labels, best_probs, threshold)
        threshold_table.append(
            {
                "policy": policy,
                "threshold": threshold,
                "reviews": int(decisions.sum()),
                "precision": m["precision"],
                "recall": m["recall"],
                "f1": m["f1"],
                "modeled_cost": realized_decision_cost(labels, decisions, amounts),
            }
        )
    shifted_amounts = amounts * 1.8
    amount_psi = population_stability_index(amounts, shifted_amounts)
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    upper = float(np.quantile(np.r_[amounts, shifted_amounts], 0.98))
    bins = np.linspace(0, upper, 28)
    ax.hist(
        amounts,
        bins=bins,
        density=True,
        alpha=0.62,
        color=COLORS["blue"],
        label="Reference test period",
    )
    ax.hist(
        shifted_amounts,
        bins=bins,
        density=True,
        alpha=0.52,
        color=COLORS["amber"],
        label="Simulated shifted batch",
    )
    ax.set(
        title=f"Amount distribution drift demonstration — PSI {amount_psi:.2f}",
        xlabel="Transaction amount ($, clipped at 98th percentile)",
        ylabel="Density",
    )
    ax.grid(alpha=0.16)
    ax.legend(frameon=False)
    _finish(fig, output / "amount_drift.png")

    summary = {
        "dataset": "deterministic synthetic demo; replace with PaySim for portfolio claims",
        "transactions": int(len(test)),
        "fraud_cases": int(labels.sum()),
        "fraud_rate": float(labels.mean()),
        "best_model": best_name,
        "metrics": metrics,
        "selected_threshold": chosen,
        "threshold_policies": threshold_table,
        "capacity_results": capacity_rows,
        "monitoring": {"amount_psi": amount_psi, "status": drift_status(amount_psi)},
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    pd.DataFrame(capacity_rows).to_csv(output / "capacity_results.csv", index=False)
    pd.DataFrame(threshold_table).to_csv(output / "threshold_policies.csv", index=False)

    scored = test.copy()
    scored["fraud_probability"] = best_probs
    scored["expected_loss"] = best_probs * scored.amount
    scored["risk_band"] = pd.cut(
        best_probs,
        bins=[-np.inf, 0.1, 0.4, 0.75, np.inf],
        labels=["low", "guarded", "high", "critical"],
    ).astype(str)
    queue = build_review_queue(scored, best_probs, min(750, len(scored)), "expected_loss")
    queue_columns = [
        "transaction_id",
        "step",
        "type",
        "amount",
        "fraud_probability",
        "expected_loss",
        "risk_band",
        "rank",
        "isFraud",
    ]
    queue[queue_columns].to_csv(output / "review_queue.csv", index=False)
    scored.to_csv(output / "scored_test.csv", index=False)

    error_frame = scored.assign(
        predicted=best_probs >= chosen,
        amount_bucket=pd.qcut(scored.amount, 4, duplicates="drop").astype(str),
        hour=scored.step % 24,
    )
    error_rows = []
    for dimension in ("type", "amount_bucket"):
        for segment, group in error_frame.groupby(dimension, observed=True):
            positives = int(group.isFraud.sum())
            true_positives = int(((group.isFraud == 1) & group.predicted).sum())
            false_positives = int(((group.isFraud == 0) & group.predicted).sum())
            error_rows.append(
                {
                    "dimension": dimension,
                    "segment": str(segment),
                    "transactions": len(group),
                    "fraud_cases": positives,
                    "recall": true_positives / positives if positives else 0.0,
                    "false_positives": false_positives,
                    "fraud_value": float(group.loc[group.isFraud == 1, "amount"].sum()),
                }
            )
    pd.DataFrame(error_rows).to_csv(output / "error_analysis.csv", index=False)

    # A recruiter-facing overview assembled entirely from the generated test results.
    fig = plt.figure(figsize=(14, 8), facecolor="#F4F6F8")
    grid = fig.add_gridspec(3, 4, height_ratios=[0.65, 1, 2.4], hspace=0.42, wspace=0.32)
    title = fig.add_subplot(grid[0, :])
    title.axis("off")
    title.text(
        0, 0.82, "RISKQUEUE", fontsize=12, color="#667085", weight="bold", transform=title.transAxes
    )
    title.text(
        0,
        0.17,
        "Fraud operations decision support",
        fontsize=27,
        color=COLORS["navy"],
        weight="bold",
        transform=title.transAxes,
    )
    cards = [
        ("TEST TRANSACTIONS", f"{len(test):,}"),
        ("FRAUD RATE", f"{labels.mean():.2%}"),
        ("AVERAGE PRECISION", f"{metrics[best_name]['average_precision']:.3f}"),
        ("AMOUNT DRIFT PSI", f"{amount_psi:.2f}"),
    ]
    for idx, (label, value) in enumerate(cards):
        ax = fig.add_subplot(grid[1, idx])
        ax.set_facecolor("white")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color("#E1E6EB")
        ax.text(
            0.07, 0.72, label, fontsize=9, color="#667085", weight="bold", transform=ax.transAxes
        )
        ax.text(
            0.07,
            0.25,
            value,
            fontsize=22,
            color=COLORS["navy"],
            weight="bold",
            transform=ax.transAxes,
        )
    ax = fig.add_subplot(grid[2, :2])
    ax.set_facecolor("white")
    for strategy, label, color in [
        ("probability", "Probability", COLORS["blue"]),
        ("expected_loss", "Expected loss", COLORS["amber"]),
    ]:
        subset = [r for r in capacity_rows if r["strategy"] == strategy]
        ax.plot(
            [r["capacity"] for r in subset],
            [r["fraud_value_capture"] * 100 for r in subset],
            marker="o",
            lw=2.5,
            label=label,
            color=color,
        )
    ax.set(
        title="Fraud value captured under limited review",
        xlabel="Reviews",
        ylabel="Value captured (%)",
    )
    ax.grid(alpha=0.15)
    ax.legend(frameon=False)
    ax = fig.add_subplot(grid[2, 2:])
    ax.set_facecolor("white")
    policy_names = [
        r["policy"].replace("-optimal", "\noptimal").replace("-optimized", "\noptimized")
        for r in threshold_table
    ]
    costs = [r["modeled_cost"] for r in threshold_table]
    bars = ax.bar(policy_names, costs, color=["#97A6B2", COLORS["blue"], COLORS["amber"]])
    ax.bar_label(bars, labels=[f"${x:,.0f}" for x in costs], padding=3, fontsize=9)
    ax.set(title="Held-out modeled cost by policy", ylabel="Illustrative cost ($)")
    ax.grid(axis="y", alpha=0.15)
    fig.savefig(
        output / "dashboard_overview.png",
        dpi=180,
        bbox_inches="tight",
        facecolor=fig.get_facecolor(),
    )
    plt.close(fig)
    return summary
