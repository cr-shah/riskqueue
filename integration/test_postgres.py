"""Executed in CI against its disposable PostgreSQL 16 service."""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from riskqueue.db.models import (
    CapacityPeriod,
    Case,
    OperationalTransaction,
    ProcessedEvent,
    ReviewQueue,
)
from riskqueue.decisions.capacity import allocate_capacity


@pytest.fixture
def postgres_database():
    base_url = make_url(os.environ["TEST_POSTGRES_URL"])
    name = "riskqueue_it_" + uuid4().hex[:12]
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {name}"))
    target = base_url.set(database=name)
    try:
        yield target.render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


def migrate(url: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "riskqueue.db.migrate"],
        env=os.environ | {"DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_fresh_postgres_migration(postgres_database):
    migrate(postgres_database)
    engine = create_engine(postgres_database)
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0002"
        )
        assert connection.execute(text("SELECT count(*) FROM capacity_periods")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM cases")).scalar_one() == 0
    engine.dispose()


def test_legacy_postgres_schema_and_data_upgrade(postgres_database):
    engine = create_engine(postgres_database)
    schema = (Path(__file__).parents[1] / "sql" / "schema.sql").read_text()
    with engine.begin() as connection:
        for statement in schema.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)
        connection.execute(
            text(
                "INSERT INTO processed_events (event_id,idempotency_key,batch_id,source,object_bucket,object_key,status,transaction_count) VALUES ('old-event','old-key','old-batch','test','raw','old.csv','completed',1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO operational_transactions (transaction_id,event_id,step,transaction_type,amount,sender_id,recipient_id,fraud_probability,expected_loss,risk_band,decision,model_version) VALUES ('old-tx','old-event',1,'TRANSFER',100,'A','B',0.5,50,'high','review','old-model')"
            )
        )
    migrate(postgres_database)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT transaction_id,model_version,feature_version,score_kind,policy_version FROM operational_transactions WHERE transaction_id='old-tx'"
            )
        ).one()
        assert tuple(row) == (
            "old-tx",
            "old-model",
            "legacy-unknown",
            "legacy-unknown",
            "legacy-unknown",
        )
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0002"
        )
    engine.dispose()


def test_concurrent_postgres_allocation_is_globally_bounded(postgres_database):
    migrate(postgres_database)
    engine = create_engine(postgres_database)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.add(
            ProcessedEvent(
                event_id="event",
                idempotency_key="idempotency",
                batch_id="batch",
                source="test",
                object_bucket="raw",
                object_key="batch.csv",
                status="completed",
                started_at=now,
                transaction_count=2,
            )
        )
        session.flush()
        session.add(CapacityPeriod(period_date=now.date(), budget=1, consumed=0))
        for index in range(2):
            identifier = f"tx-{index}"
            session.add(
                OperationalTransaction(
                    transaction_id=identifier,
                    event_id="event",
                    ingested_at=now,
                    step=index,
                    transaction_type="TRANSFER",
                    amount=100,
                    sender_id="A",
                    recipient_id="B",
                    fraud_probability=0.5,
                    expected_loss=50,
                    risk_band="high",
                    decision="backlog",
                    model_version="demo-policy-v1",
                    feature_version="behavioral-v1",
                    score_kind="development_heuristic",
                )
            )
            session.add(
                ReviewQueue(
                    created_at=now,
                    transaction_id=identifier,
                    event_id="event",
                    queue_date=now.date(),
                    rank=index + 1,
                    expected_loss=50,
                    priority_score=50 - index,
                    status="backlog",
                )
            )

    ready = Barrier(2)

    def claim():
        with sessions.begin() as session:
            ready.wait(timeout=10)
            return allocate_capacity(session, now.date(), 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: claim(), range(2)))
    assert len(first | second) == 1
    with sessions() as session:
        assert session.get(CapacityPeriod, now.date()).consumed == 1
        assert session.scalar(select(func.count()).select_from(Case)) == 1
        assert (
            session.scalar(
                select(func.count()).select_from(ReviewQueue).where(ReviewQueue.status == "backlog")
            )
            == 1
        )
        assert session.scalar(select(func.count()).select_from(OperationalTransaction)) == 2
    engine.dispose()
