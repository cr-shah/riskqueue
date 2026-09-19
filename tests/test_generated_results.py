import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_generated_results_use_calibrated_models():
    summary = json.loads((ROOT / "artifacts/figures/summary.json").read_text())

    assert "sigmoid calibration" in summary["best_model"]
    assert all("sigmoid calibration" in name for name in summary["metrics"])


def test_readme_headlines_match_generated_results():
    summary = json.loads((ROOT / "artifacts/figures/summary.json").read_text())
    readme = (ROOT / "README.md").read_text()
    best_metrics = summary["metrics"][summary["best_model"]]
    expected_loss = next(
        row
        for row in summary["capacity_results"]
        if row["strategy"] == "expected_loss" and row["capacity"] == 750
    )
    probability = next(
        row
        for row in summary["capacity_results"]
        if row["strategy"] == "probability" and row["capacity"] == 750
    )
    cost_policy = next(
        row for row in summary["threshold_policies"] if row["policy"] == "Cost-optimized"
    )
    default_policy = next(
        row for row in summary["threshold_policies"] if row["policy"] == "Default 0.50"
    )

    assert f"**{best_metrics['average_precision']:.3f} average precision**" in readme
    assert f"**{expected_loss['fraud_value_capture']:.1%} fraud value captured**" in readme
    assert f"vs. {probability['fraud_value_capture']:.1%} using probability alone" in readme
    assert f"**${cost_policy['modeled_cost']:,.0f} modeled cost**" in readme
    assert f"vs. ${default_policy['modeled_cost']:,.0f} at threshold 0.50" in readme
