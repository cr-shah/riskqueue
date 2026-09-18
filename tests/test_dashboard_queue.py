from pathlib import Path

import pandas as pd
import pytest

from dashboard.queue_data import capacity_views


def test_capacity_views_use_selected_capacity():
    scored_path = Path(__file__).resolve().parents[1] / "artifacts/figures/scored_test.csv"
    scored = pd.read_csv(scored_path)

    scorecard, queue = capacity_views(scored, 75)

    assert set(scorecard.strategy) == {
        "probability",
        "expected_loss",
        "expected_review_value",
    }
    assert scorecard.capacity.tolist() == [75, 75, 75]
    assert scorecard.reviews.tolist() == [75, 75, 75]
    assert len(queue) == 75
    assert queue["rank"].tolist() == list(range(1, 76))
    assert queue.iloc[0].expected_loss == pytest.approx(scored.expected_loss.max())
