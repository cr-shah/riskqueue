import pytest
from fastapi.testclient import TestClient

from riskqueue.api.main import app

client = TestClient(app)


def transaction(**overrides):
    payload = {
        "transaction_id": "tx-1",
        "step": 2,
        "type": "TRANSFER",
        "amount": 2500.0,
        "nameOrig": "C1",
        "nameDest": "M1",
    }
    return payload | overrides


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_requires_database_and_analyst_auth(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ANALYST_TOKENS", raising=False)
    assert client.get("/ready").status_code == 503

    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'ready.db'}")
    assert client.get("/ready").status_code == 503

    monkeypatch.setenv("ANALYST_TOKENS", '{"test-token":"analyst"}')
    assert client.get("/ready").json()["status"] == "ready"


def test_readiness_rejects_unreachable_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'missing' / 'db.sqlite'}")
    monkeypatch.setenv("ANALYST_TOKENS", '{"test-token":"analyst"}')
    assert client.get("/ready").status_code == 503


def test_public_preview_routes_require_analyst_token_when_enabled(monkeypatch):
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    token = "T" * 32
    monkeypatch.setenv("ANALYST_TOKENS", f'{{"{token}":"analyst"}}')
    assert client.post("/v1/score", json=transaction()).status_code == 401
    assert client.post("/v1/score/batch", json={"transactions": [transaction()]}).status_code == 401
    assert (
        client.post("/v1/review-queue", json={"transactions": [transaction()]}).status_code == 401
    )
    assert client.get("/v1/model/metrics").status_code == 401
    response = client.post(
        "/v1/score", json=transaction(), headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


def test_hosted_readiness_rejects_short_analyst_token(monkeypatch, tmp_path):
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'ready.db'}")
    monkeypatch.setenv("ANALYST_TOKENS", '{"short":"analyst"}')
    assert client.get("/ready").status_code == 503


def test_readiness_rejects_invalid_preview_auth_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("API_AUTH_REQUIRED", "tru")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'ready.db'}")
    monkeypatch.setenv("ANALYST_TOKENS", '{"test-token":"analyst"}')
    assert client.get("/ready").status_code == 503


def test_single_score_contract():
    response = client.post("/v1/score", json=transaction())
    assert response.status_code == 200
    assert 0 <= response.json()["fraud_probability"] <= 1
    assert response.json()["expected_loss"] >= 0


def test_negative_amount_rejected():
    assert client.post("/v1/score", json=transaction(amount=-1)).status_code == 422


def test_infinite_amount_rejected():
    assert client.post("/v1/score", json=transaction(amount="Infinity")).status_code == 422


def test_invalid_transaction_type_rejected():
    assert client.post("/v1/score", json=transaction(type="WIRE")).status_code == 422


def test_batch_limit_is_enforced():
    payload = {"transactions": [transaction(transaction_id=f"t-{i}") for i in range(1001)]}
    assert client.post("/v1/score/batch", json=payload).status_code == 422


def test_review_queue_is_ranked_and_capacity_limited():
    payload = {
        "transactions": [
            transaction(transaction_id="small", amount=10),
            transaction(transaction_id="large", amount=100_000),
        ],
        "capacity": 1,
        "strategy": "expected_loss",
    }
    result = client.post("/v1/review-queue", json=payload)
    assert result.status_code == 200
    assert result.json()[0]["transaction_id"] == "large"
    assert result.json()[0]["rank"] == 1


@pytest.mark.parametrize(
    "overrides",
    [{"step": 1.5}, {"step": True}, {"step": 10_000_001}, {"amount": 1e13}, {"amount": "2"}],
)
def test_transaction_numeric_bounds(overrides):
    assert client.post("/v1/score", json=transaction(**overrides)).status_code == 422


@pytest.mark.parametrize("endpoint", ["/v1/score/batch", "/v1/review-queue"])
def test_duplicate_batch_transaction_ids_rejected(endpoint):
    assert (
        client.post(endpoint, json={"transactions": [transaction(), transaction()]}).status_code
        == 422
    )


def test_queue_exposes_validated_policy_assumptions():
    payload = {
        "transactions": [transaction()],
        "strategy": "expected_review_value",
        "loss_fraction": 0.5,
        "manual_review_cost": 8,
    }
    result = client.post("/v1/review-queue", json=payload)
    original = client.post("/v1/score", json=transaction()).json()
    assert result.status_code == 200
    row = result.json()[0]
    assert row["expected_loss"] == pytest.approx(original["expected_loss"] * 0.5, abs=0.01)
    assert row["priority_score"] == round(row["expected_loss"] - 8, 2)


@pytest.mark.parametrize("field,value", [("loss_fraction", 1.1), ("manual_review_cost", -1)])
def test_queue_rejects_invalid_assumptions(field, value):
    assert (
        client.post(
            "/v1/review-queue", json={"transactions": [transaction()], field: value}
        ).status_code
        == 422
    )
