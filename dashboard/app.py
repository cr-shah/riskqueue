from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.queue_data import capacity_views

ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "artifacts/figures/summary.json"
CAPACITY_PATH = ROOT / "artifacts/figures/capacity_results.csv"
THRESHOLD_PATH = ROOT / "artifacts/figures/threshold_policies.csv"
SCORED_PATH = ROOT / "artifacts/figures/scored_test.csv"
ERROR_PATH = ROOT / "artifacts/figures/error_analysis.csv"

st.set_page_config(page_title="RiskQueue", page_icon="▦", layout="wide")
st.markdown(
    """
<style>
.block-container {max-width: 1180px; padding-top: 2.2rem}
[data-testid="stMetric"] {background:#f7f8fa;border:1px solid #e6e9ee;border-radius:10px;padding:16px}
h1,h2,h3 {color:#16324f}
.eyebrow {letter-spacing:.12em;text-transform:uppercase;color:#6b7280;font-size:.78rem;font-weight:700}
.note {padding:16px 18px;border-left:4px solid #f6ae2d;background:#fff9ed;border-radius:4px}
</style>
""",
    unsafe_allow_html=True,
)

if not SUMMARY_PATH.exists():
    st.error("Generated results are missing. Run `python scripts/run_demo.py` first.")
    st.stop()

summary = json.loads(SUMMARY_PATH.read_text())
capacity = pd.read_csv(CAPACITY_PATH)
thresholds = pd.read_csv(THRESHOLD_PATH)
scored = pd.read_csv(SCORED_PATH)
errors = pd.read_csv(ERROR_PATH)
best = summary["metrics"][summary["best_model"]]

page = st.sidebar.radio(
    "View",
    [
        "Executive overview",
        "Model performance",
        "Review queue",
        "Decision policy",
        "Explainability",
        "Monitoring",
    ],
)
st.markdown('<div class="eyebrow">Fraud operations decision support</div>', unsafe_allow_html=True)
st.title("RiskQueue")
st.caption(
    "Scores suspicious transactions, then turns those scores into a financially aware review queue."
)

if page == "Executive overview":
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Test transactions", f"{summary['transactions']:,}")
    c2.metric("Fraud cases", f"{summary['fraud_cases']:,}")
    c3.metric("Average precision", f"{best['average_precision']:.3f}")
    c4.metric("Selected threshold", f"{summary['selected_threshold']:.2f}")
    st.subheader("What limited review capacity captures")
    fig = px.line(
        capacity,
        x="capacity",
        y="fraud_value_capture",
        color="strategy",
        markers=True,
        labels={
            "capacity": "Reviews available",
            "fraud_value_capture": "Fraud value captured",
            "strategy": "Queue policy",
        },
    )
    fig.update_yaxes(tickformat=".0%")
    st.plotly_chart(fig, width="stretch")
    left, right = st.columns([1.15, 0.85])
    with left:
        st.subheader("Risk-score distribution")
        st.image(str(ROOT / "artifacts/figures/score_distribution.png"))
    with right:
        st.subheader("Highest-exposure transaction types")
        exposure = (
            scored.groupby("type", as_index=False)
            .expected_loss.sum()
            .sort_values("expected_loss", ascending=False)
        )
        type_chart = px.bar(
            exposure,
            x="expected_loss",
            y="type",
            orientation="h",
            labels={"expected_loss": "Expected exposure ($)", "type": "Transaction type"},
        )
        type_chart.update_layout(showlegend=False, yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(type_chart, width="stretch")
    st.markdown(
        '<div class="note"><b>What this means.</b> Probability finds likely fraud; expected-loss ranking also considers the dollars exposed. The useful policy depends on whether operations value case capture, dollar capture, or net review value.</div>',
        unsafe_allow_html=True,
    )
elif page == "Model performance":
    rows = pd.DataFrame(summary["metrics"]).T.reset_index(names="model")
    st.subheader("Held-out model comparison")
    st.dataframe(
        rows.style.format({c: "{:.3f}" for c in rows.columns if c != "model"}),
        width="stretch",
    )
    st.image(
        str(ROOT / "artifacts/figures/precision_recall.png"),
        caption="Precision–recall is the primary rare-event metric.",
    )
    st.image(
        str(ROOT / "artifacts/figures/model_diagnostics.png"),
        caption="ROC, probability calibration, and one operating-point confusion matrix.",
    )
    st.subheader("Segment error analysis")
    dimension = st.segmented_control(
        "Break down by",
        options=["type", "amount_bucket"],
        default="type",
        format_func=lambda value: value.replace("_", " ").title(),
    )
    shown_errors = errors[errors.dimension == dimension]
    st.dataframe(
        shown_errors.style.format({"recall": "{:.1%}", "fraud_value": "${:,.0f}"}),
        hide_index=True,
        width="stretch",
    )
elif page == "Review queue":
    selected_capacity = st.slider(
        "Analyst capacity",
        25,
        int(capacity.capacity.max()),
        min(250, int(capacity.capacity.max())),
        25,
    )
    scorecard, ranked_queue = capacity_views(scored, selected_capacity)
    scorecard = scorecard.sort_values("fraud_value_capture", ascending=False)
    st.subheader("Policy scorecard")
    st.dataframe(
        scorecard[
            [
                "strategy",
                "capacity",
                "precision",
                "fraud_recall",
                "fraud_value_capture",
                "fraud_amount_captured",
            ]
        ].style.format(
            {
                "precision": "{:.1%}",
                "fraud_recall": "{:.1%}",
                "fraud_value_capture": "{:.1%}",
                "fraud_amount_captured": "${:,.0f}",
            }
        ),
        width="stretch",
    )
    st.subheader("Ranked transaction queue")
    display_count = st.slider("Rows to show", 10, 100, 25, 5)
    selected_bands = st.multiselect(
        "Risk bands",
        options=["critical", "high", "guarded", "low"],
        default=["critical", "high", "guarded", "low"],
    )
    shown_queue = ranked_queue[ranked_queue.risk_band.isin(selected_bands)].head(display_count)
    shown_queue = shown_queue[
        [
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
    ]
    st.dataframe(
        shown_queue.style.format(
            {
                "amount": "${:,.2f}",
                "fraud_probability": "{:.2%}",
                "expected_loss": "${:,.2f}",
            }
        ).background_gradient(subset=["expected_loss"], cmap="YlOrRd"),
        hide_index=True,
        width="stretch",
    )
elif page == "Decision policy":
    col1, col2, col3, col4 = st.columns(4)
    selected_threshold = col1.slider(
        "Threshold", 0.0, 1.0, float(summary["selected_threshold"]), 0.01
    )
    review_cost = col2.number_input("Review cost ($)", 0.0, 100.0, 4.0, 0.5)
    loss_fraction = col3.slider("Fraud loss fraction", 0.0, 1.0, 1.0, 0.05)
    policy_capacity = col4.number_input(
        "Review capacity", 1, len(scored), min(750, len(scored)), 25
    )
    decisions = scored.fraud_probability >= selected_threshold
    positives = scored.isFraud == 1
    policy_precision = float((positives & decisions).sum() / max(1, decisions.sum()))
    policy_recall = float((positives & decisions).sum() / max(1, positives.sum()))
    missed_loss = float(scored.loc[positives & ~decisions, "amount"].sum() * loss_fraction)
    modeled_cost = float(decisions.sum() * review_cost + missed_loss)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Modeled cost", f"${modeled_cost:,.0f}")
    k2.metric("Queue size", f"{int(decisions.sum()):,}", f"capacity {int(policy_capacity):,}")
    k3.metric("Precision", f"{policy_precision:.1%}")
    k4.metric("Recall", f"{policy_recall:.1%}")
    if decisions.sum() > policy_capacity:
        st.warning(
            "The threshold creates more alerts than the selected capacity; a top-k queue is required."
        )
    st.subheader("Threshold policy comparison")
    st.dataframe(
        thresholds.style.format(
            {
                "threshold": "{:.2f}",
                "precision": "{:.1%}",
                "recall": "{:.1%}",
                "f1": "{:.3f}",
                "modeled_cost": "${:,.0f}",
            }
        ),
        width="stretch",
    )
    st.image(str(ROOT / "artifacts/figures/threshold_cost.png"))
elif page == "Explainability":
    st.subheader("Understandable inputs, careful claims")
    st.info(
        "Feature contribution views explain what influenced a prediction; they do not establish a causal driver of fraud."
    )
    definitions = pd.DataFrame(
        [
            ("amount_to_sender_median", "Amount compared with the sender's prior typical amount"),
            ("sender_count_24h", "Sender transactions in the prior 24 hours"),
            ("first_time_recipient", "Whether this sender-recipient relationship is new"),
            ("recipient_unique_senders_24h", "Distinct incoming senders in the prior 24 hours"),
        ],
        columns=["Feature", "Definition"],
    )
    st.dataframe(definitions, hide_index=True, width="stretch")
elif page == "Monitoring":
    st.subheader("Offline drift monitor")
    st.metric(
        "Amount PSI — shifted demo batch",
        f"{summary['monitoring']['amount_psi']:.2f}",
        summary["monitoring"]["status"],
    )
    st.image(str(ROOT / "artifacts/figures/amount_drift.png"))
    st.warning(
        "Drift means incoming behavior differs from the reference period. It does not prove model quality declined."
    )

st.caption(
    "Demo results are generated from a deterministic synthetic scenario and are not claims about PaySim or a real financial institution."
)
