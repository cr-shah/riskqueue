from __future__ import annotations

import pandas as pd

from riskqueue.decisions.review_queue import build_review_queue, queue_metrics


def capacity_views(scored: pd.DataFrame, capacity: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate the scorecard and expected-loss queue for the selected capacity."""
    probabilities = scored["fraud_probability"].to_numpy()
    rows = []
    selected_queue = None
    for strategy in ("probability", "expected_loss", "expected_review_value"):
        queue = build_review_queue(scored, probabilities, capacity, strategy)
        rows.append({"strategy": strategy, "capacity": capacity, **queue_metrics(queue, scored)})
        if strategy == "expected_loss":
            selected_queue = queue
    return pd.DataFrame(rows), selected_queue
