from datetime import UTC, datetime
from unittest.mock import patch

from riskqueue.modeling.artifacts import ModelMetadata


def test_model_metadata_records_each_creation_time():
    first_time = datetime(2026, 1, 1, tzinfo=UTC)
    second_time = datetime(2026, 1, 2, tzinfo=UTC)
    with patch("riskqueue.modeling.artifacts.datetime") as clock:
        clock.now.side_effect = [first_time, second_time]
        first = ModelMetadata("v1", "logistic", 10, 0.1, 0.7, 0.2, 0.3)
        second = ModelMetadata("v2", "logistic", 20, 0.2, 0.8, 0.1, 0.4)

    assert first.created_at == first_time.isoformat()
    assert second.created_at == second_time.isoformat()
