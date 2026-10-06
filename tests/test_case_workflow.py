from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from riskqueue.api.main import app
from riskqueue.cloud.messages import ObjectReference, ProcessingMessage
from riskqueue.cloud.storage import LocalObjectStore
from riskqueue.db.models import Base, Case, CaseAudit, CaseNote, CaseOutcome, OperationalTransaction
from riskqueue.worker import EventProcessor


class Warehouse:
    def merge_scoring_history(self, rows):
        return len(list(rows))


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'cases.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    store = LocalObjectStore(tmp_path / "objects")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    frame = pd.DataFrame(
        [
            {
                "transaction_id": "case-tx",
                "step": 1,
                "type": "TRANSFER",
                "amount": 1000,
                "nameOrig": "sender",
                "nameDest": "recipient",
            }
        ]
    )
    store.put_bytes(
        "raw",
        "case.csv",
        frame.to_csv(index=False).encode(),
        source="test",
        content_type="text/csv",
    )
    message = ProcessingMessage(
        event_id="case-event",
        batch_id="case-event",
        object=ObjectReference(bucket="raw", key="case.csv"),
        occurred_at=timestamp,
        source="test",
    )
    processor = EventProcessor(
        session_factory=sessions,
        object_store=store,
        warehouse=Warehouse(),
        review_capacity=1,
        clock=lambda: timestamp,
    )
    assert processor.process(message).reviews_written == 1
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ANALYST_TOKENS", '{"analyst-test-token":"alex","second-test-token":"sam"}')
    return TestClient(app), sessions, timestamp


def headers(token="analyst-test-token"):
    return {"Authorization": f"Bearer {token}"}


def test_authenticated_investigation_and_audit_history(workflow):
    client, sessions, _ = workflow
    assert client.get("/operator").status_code == 200
    assert "Investigate with context" in client.get("/operator").text
    assert client.get("/v1/cases").status_code == 401
    assert client.get("/v1/cases", headers=headers("invalid")).status_code == 401
    assert client.get("/v1/me", headers=headers()).json() == {"analyst": "alex"}
    listing = client.get("/v1/cases", headers=headers())
    assert listing.status_code == 200
    case_id = listing.json()[0]["case_id"]
    detail = client.get(f"/v1/cases/{case_id}", headers=headers()).json()
    assert detail["features"]["sender_count_24h"] == 0
    assert detail["audit"][0]["action"] == "created"
    assert detail["model_version"] == "demo-policy-v1"
    assert detail["policy_version"] == "daily-backlog-v1"
    assert detail["score_kind"] == "development_heuristic"

    claim = client.post(f"/v1/cases/{case_id}/claim", headers=headers())
    assert claim.status_code == 200
    assert claim.json()["assigned_to"] == "alex"
    assert client.post(f"/v1/cases/{case_id}/claim", headers=headers()).status_code == 200
    assert (
        client.post(f"/v1/cases/{case_id}/claim", headers=headers("second-test-token")).status_code
        == 409
    )
    assert (
        client.post(
            f"/v1/cases/{case_id}/notes",
            headers=headers("second-test-token"),
            json={"text": "Unauthorized"},
        ).status_code
        == 403
    )
    note = client.post(
        f"/v1/cases/{case_id}/notes", headers=headers(), json={"text": "Verified transfer evidence"}
    )
    assert note.status_code == 201
    assert (
        client.post(
            f"/v1/cases/{case_id}/transition",
            headers=headers(),
            json={"status": "escalated", "reason": "Needs secondary review"},
        ).status_code
        == 200
    )
    resolved = client.post(
        f"/v1/cases/{case_id}/transition",
        headers=headers(),
        json={
            "status": "resolved",
            "disposition": "confirmed_fraud",
            "reason": "Confirmed by investigation",
        },
    )
    assert resolved.status_code == 200
    assert (
        client.post(
            f"/v1/cases/{case_id}/notes", headers=headers(), json={"text": "Late note"}
        ).status_code
        == 409
    )
    outcome = client.post(
        f"/v1/cases/{case_id}/outcome",
        headers=headers(),
        json={
            "finding": "confirmed_fraud",
            "observed_at": datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
            "investigation_minutes": 35,
            "recovered_amount": 25,
            "prevented_amount": 100,
            "reason": "Chargeback corroborated",
        },
    )
    assert outcome.status_code == 201
    assert (
        client.post(
            f"/v1/cases/{case_id}/outcome",
            headers=headers(),
            json={
                "finding": "legitimate",
                "observed_at": datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
                "reason": "duplicate",
            },
        ).status_code
        == 409
    )
    detail = client.get(f"/v1/cases/{case_id}", headers=headers()).json()
    assert detail["status"] == "resolved"
    assert detail["notes"][0]["text"] == "Verified transfer evidence"
    assert detail["outcome"]["recovered_amount"] == 25
    assert detail["outcome"]["prevented_amount"] == 100
    assert [event["action"] for event in detail["audit"]] == [
        "created",
        "claimed",
        "note_added",
        "transition",
        "transition",
        "outcome_recorded",
    ]
    assert all(event["policy_version"] == "daily-backlog-v1" for event in detail["audit"])
    with sessions() as session:
        assert session.get(Case, case_id).disposition == "confirmed_fraud"
        assert session.get(CaseOutcome, case_id).finding == "confirmed_fraud"
        assert len(session.scalars(select(CaseAudit)).all()) == 6
        assert len(session.scalars(select(CaseNote)).all()) == 1


def test_replay_is_reproducible_and_does_not_use_future_outcome(workflow):
    client, _, timestamp = workflow
    case_id = client.get("/v1/cases", headers=headers()).json()[0]["case_id"]
    client.post(f"/v1/cases/{case_id}/claim", headers=headers())
    client.post(
        f"/v1/cases/{case_id}/transition",
        headers=headers(),
        json={
            "status": "resolved",
            "disposition": "confirmed_fraud",
            "reason": "Evidence",
        },
    )
    client.post(
        f"/v1/cases/{case_id}/outcome",
        headers=headers(),
        json={
            "finding": "confirmed_fraud",
            "observed_at": (timestamp + timedelta(days=1)).isoformat(),
            "reason": "Observed later",
        },
    )
    request = {
        "available_from": (timestamp - timedelta(seconds=1)).isoformat(),
        "available_through": (timestamp + timedelta(hours=1)).isoformat(),
        "policies": [
            {"name": "review", "capacity": 1, "threshold": 0},
            {"name": "no staff", "capacity": 0, "threshold": 0},
        ],
    }
    first = client.post("/v1/replay", json=request, headers=headers())
    second = client.post("/v1/replay", json=request, headers=headers())
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    scenarios = first.json()["scenarios"]
    assert scenarios[0]["reviewed_transaction_ids"] == ["case-tx"]
    assert scenarios[0]["known_confirmed_fraud"] == 0
    assert scenarios[0]["unknown_or_unresolved"] == 1
    assert scenarios[1]["backlog_transaction_ids"] == ["case-tx"]
    assert (
        client.post(
            "/v1/replay", json=request | {"model_version": "some-new-model"}, headers=headers()
        ).status_code
        == 422
    )
    request["available_through"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    later = client.post("/v1/replay", json=request, headers=headers())
    assert later.status_code == 200
    assert later.json()["scenarios"][0]["known_confirmed_fraud"] == 1


def test_outcome_input_validation_and_case_status(workflow):
    client, _, _ = workflow
    case_id = client.get("/v1/cases", headers=headers()).json()[0]["case_id"]
    assert (
        client.post(
            f"/v1/cases/{case_id}/outcome",
            headers=headers(),
            json={
                "finding": "confirmed_fraud",
                "observed_at": datetime.now(UTC).isoformat(),
                "reason": "x",
            },
        ).status_code
        == 403
    )
    client.post(f"/v1/cases/{case_id}/claim", headers=headers())
    assert (
        client.post(
            f"/v1/cases/{case_id}/transition",
            headers=headers(),
            json={"status": "resolved", "reason": "missing disposition"},
        ).status_code
        == 409
    )
    assert (
        client.get("/v1/cases?status=investigating", headers=headers()).json()[0]["case_id"]
        == case_id
    )


def test_replay_does_not_use_later_high_priority_arrival(workflow):
    client, sessions, timestamp = workflow
    with sessions.begin() as session:
        session.add(
            OperationalTransaction(
                transaction_id="future-high-risk",
                event_id="case-event",
                ingested_at=timestamp + timedelta(hours=1),
                step=2,
                transaction_type="TRANSFER",
                amount=100_000,
                sender_id="new-sender",
                recipient_id="new-recipient",
                fraud_probability=0.9,
                expected_loss=90_000,
                risk_band="critical",
                decision="backlog",
                model_version="demo-policy-v1",
                feature_version="behavioral-v1",
                score_kind="development_heuristic",
                policy_version="daily-backlog-v1",
                review_cost=4,
                loss_fraction=1,
            )
        )
    request = {
        "available_from": (timestamp - timedelta(seconds=1)).isoformat(),
        "available_through": (timestamp + timedelta(hours=2)).isoformat(),
        "policies": [
            {"name": "one slot", "capacity": 1},
            {"name": "two staff", "analysts": 2, "reviews_per_analyst": 1},
        ],
    }
    response = client.post("/v1/replay", json=request, headers=headers())
    assert response.status_code == 200
    scenario = response.json()["scenarios"][0]
    assert scenario["reviewed_transaction_ids"] == ["case-tx"]
    assert scenario["backlog_transaction_ids"] == ["future-high-risk"]
    assert response.json()["scenarios"][1]["effective_daily_capacity"] == 2
    assert response.json()["scenarios"][1]["reviewed_transaction_ids"] == [
        "case-tx",
        "future-high-risk",
    ]
    earlier = client.post(
        "/v1/replay",
        json=request | {"available_through": (timestamp + timedelta(minutes=30)).isoformat()},
        headers=headers(),
    )
    assert earlier.status_code == 200
    assert earlier.json()["transactions"] == 1
    assert earlier.json()["dataset_version"] != response.json()["dataset_version"]


def test_replay_ranks_entire_available_batch_before_allocation(workflow):
    client, sessions, timestamp = workflow
    with sessions.begin() as session:
        session.add(
            OperationalTransaction(
                transaction_id="same-batch-high-risk",
                event_id="case-event",
                ingested_at=timestamp,
                step=1,
                transaction_type="TRANSFER",
                amount=100_000,
                sender_id="other",
                recipient_id="other-destination",
                fraud_probability=0.9,
                expected_loss=90_000,
                risk_band="critical",
                decision="backlog",
                model_version="demo-policy-v1",
                feature_version="behavioral-v1",
                score_kind="development_heuristic",
                policy_version="daily-backlog-v1",
                review_cost=4,
                loss_fraction=1,
            )
        )
    response = client.post(
        "/v1/replay",
        json={
            "available_from": (timestamp - timedelta(seconds=1)).isoformat(),
            "available_through": (timestamp + timedelta(seconds=1)).isoformat(),
            "policies": [{"name": "one slot", "capacity": 1}],
        },
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.json()["scenarios"][0]["reviewed_transaction_ids"] == ["same-batch-high-risk"]
    assert response.json()["scenarios"][0]["backlog_transaction_ids"] == ["case-tx"]
