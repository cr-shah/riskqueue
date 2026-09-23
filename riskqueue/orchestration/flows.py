from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from prefect import flow, get_run_logger, task
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from riskqueue.db.models import OperationalTransaction
from riskqueue.monitoring.drift import drift_status, population_stability_index
from riskqueue.warehouse.snowflake import build_warehouse


@task(retries=2, retry_delay_seconds=10)
def extract_operational_history(database_url: str, since: datetime) -> list[dict]:
    logger = get_run_logger()
    engine = create_engine(database_url, pool_pre_ping=True)
    with Session(engine) as session:
        records = session.scalars(
            select(OperationalTransaction).where(OperationalTransaction.ingested_at >= since)
        ).all()
    rows = [
        {
            "event_id": row.event_id,
            "transaction_id": row.transaction_id,
            "ingested_at": row.ingested_at,
            "step": row.step,
            "transaction_type": row.transaction_type,
            "amount": row.amount,
            "fraud_probability": row.fraud_probability,
            "expected_loss": row.expected_loss,
            "risk_band": row.risk_band,
            "decision": row.decision,
            "model_version": row.model_version,
        }
        for row in records
    ]
    logger.info("Extracted %d operational scoring rows", len(rows))
    return rows


@task(retries=2, retry_delay_seconds=15)
def merge_warehouse_history(rows: list[dict]) -> int:
    loaded = build_warehouse().merge_scoring_history(rows)
    get_run_logger().info("Merged %d scoring rows into Snowflake", loaded)
    return loaded


@flow(name="riskqueue-historical-warehouse-sync", log_prints=True)
def historical_warehouse_sync(database_url: str | None = None, lookback_hours: int = 24) -> int:
    """Incrementally synchronize recent operational scores into Snowflake."""
    url = database_url or os.environ["DATABASE_URL"]
    since = datetime.now(UTC) - timedelta(hours=lookback_hours)
    rows = extract_operational_history(url, since)
    return merge_warehouse_history(rows)


@task(retries=1, retry_delay_seconds=5)
def prepare_monitoring_dataset(
    reference_csv: str, current_csv: str, output_json: str
) -> dict[str, float | str]:
    result = calculate_monitoring_dataset(reference_csv, current_csv, output_json)
    get_run_logger().info("Prepared monitoring dataset with PSI %.4f", result["amount_psi"])
    return result


def calculate_monitoring_dataset(
    reference_csv: str, current_csv: str, output_json: str
) -> dict[str, float | str]:
    """Pure monitoring calculation shared by Prefect and unit tests."""
    reference = pd.read_csv(reference_csv)
    current = pd.read_csv(current_csv)
    psi = population_stability_index(reference["amount"], current["amount"])
    result: dict[str, float | str] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "amount_psi": psi,
        "status": drift_status(psi),
    }
    target = Path(output_json)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n")
    return result


@flow(name="riskqueue-monitoring-dataset", log_prints=True)
def monitoring_dataset_flow(
    reference_csv: str, current_csv: str, output_json: str = "artifacts/reports/drift.json"
) -> dict[str, float | str]:
    return prepare_monitoring_dataset(reference_csv, current_csv, output_json)


@task(retries=2, retry_delay_seconds=15)
def refresh_daily_analytics() -> None:
    build_warehouse().refresh_daily_model_monitoring()
    get_run_logger().info("Refreshed daily Snowflake model-monitoring aggregates")


@flow(name="riskqueue-daily-analytics", log_prints=True)
def daily_analytics_flow() -> None:
    """Refresh warehouse aggregates independently of transaction processing."""
    refresh_daily_analytics()
