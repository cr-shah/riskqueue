import json

import pandas as pd

from riskqueue.orchestration.flows import (
    calculate_monitoring_dataset,
    daily_analytics_flow,
    historical_warehouse_sync,
    monitoring_dataset_flow,
    prepare_monitoring_dataset,
)


def test_monitoring_task_writes_parameterized_output(tmp_path):
    reference = tmp_path / "reference.csv"
    current = tmp_path / "current.csv"
    output = tmp_path / "monitoring/result.json"
    pd.DataFrame({"amount": [1, 2, 3, 4, 5]}).to_csv(reference, index=False)
    pd.DataFrame({"amount": [2, 3, 4, 5, 6]}).to_csv(current, index=False)

    result = calculate_monitoring_dataset(str(reference), str(current), str(output))

    assert result["amount_psi"] >= 0
    assert json.loads(output.read_text())["status"] in {"stable", "watch", "shifted"}
    assert prepare_monitoring_dataset.retries == 1


def test_prefect_flows_have_stable_deployment_names():
    assert historical_warehouse_sync.name == "riskqueue-historical-warehouse-sync"
    assert monitoring_dataset_flow.name == "riskqueue-monitoring-dataset"
    assert daily_analytics_flow.name == "riskqueue-daily-analytics"
