import asyncio
import base64
import hashlib
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riskqueue.api import security
from riskqueue.api.main import app

client = TestClient(app)


def hashed_credentials(**credentials):
    return json.dumps(
        {
            hashlib.sha256(token.encode()).hexdigest(): analyst
            for token, analyst in credentials.items()
        }
    )


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


def test_api_responses_enforce_security_and_no_store_headers():
    response = client.get("/health")
    assert response.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["cache-control"] == "no-store"
    assert "camera=()" in response.headers["permissions-policy"]
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )


def test_operator_csp_allows_only_exact_inline_assets():
    response = client.get("/operator")
    style = re.search(r"<style>(.*?)</style>", response.text, re.DOTALL)
    script = re.search(r"<script>(.*?)</script>", response.text, re.DOTALL)
    assert style and script
    style_hash = base64.b64encode(hashlib.sha256(style.group(1).encode()).digest()).decode()
    script_hash = base64.b64encode(hashlib.sha256(script.group(1).encode()).digest()).decode()
    policy = response.headers["content-security-policy"]
    assert f"style-src 'sha256-{style_hash}'" in policy
    assert f"script-src 'sha256-{script_hash}'" in policy
    assert "connect-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "'unsafe-inline'" not in policy
    assert " style=" not in response.text


def test_documentation_is_usable_locally_and_hidden_when_auth_is_hosted(monkeypatch):
    monkeypatch.setenv("API_AUTH_REQUIRED", "false")
    local = client.get("/docs")
    assert local.status_code == 200
    assert "content-security-policy" not in local.headers

    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    for path in ("/docs", "/redoc", "/openapi.json"):
        hosted = client.get(path)
        assert hosted.status_code == 404
        assert hosted.headers["content-security-policy"] == (
            "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )


def test_oversized_declared_request_is_rejected_before_json_parsing():
    response = client.post(
        "/v1/score",
        content=b"x" * (1_048_576 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_oversized_streamed_request_is_rejected_without_content_length():
    response = client.post(
        "/v1/score",
        content=iter((b"{", b"x" * 1_048_576, b"}")),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_fragmented_request_is_replayed_as_one_bounded_message():
    async def exercise():
        fragments = [
            {"type": "http.request", "body": b"x", "more_body": True} for _ in range(10_000)
        ]
        fragments.append({"type": "http.request", "body": b"", "more_body": False})
        delivered = []
        sent = []

        async def receive():
            return fragments.pop(0)

        async def send(message):
            sent.append(message)

        async def downstream(_scope, inner_receive, inner_send):
            message = await inner_receive()
            delivered.append(message)
            await inner_send({"type": "http.response.start", "status": 204, "headers": []})
            await inner_send({"type": "http.response.body", "body": b""})

        middleware = security.RequestSizeLimitMiddleware(downstream, max_bytes=20_000)
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/test",
                "headers": [],
                "client": ("127.0.0.1", 1234),
            },
            receive,
            send,
        )
        return delivered, sent

    delivered, sent = asyncio.run(exercise())
    assert delivered == [{"type": "http.request", "body": b"x" * 10_000, "more_body": False}]
    assert sent[0]["status"] == 204


def test_ambiguous_content_length_is_rejected():
    response = client.post(
        "/v1/score",
        content=b"{}",
        headers=[
            ("Content-Type", "application/json"),
            ("Content-Length", "2"),
            ("Content-Length", "3"),
        ],
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Content-Length"}


def test_compressed_request_body_is_rejected():
    response = client.post(
        "/v1/score",
        content=b"compressed-placeholder",
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert response.status_code == 415
    assert response.json() == {"detail": "Compressed request bodies are not accepted"}


def rate_limited_client(clock, *, limit=2, max_buckets=4096):
    middleware = getattr(security, "RateLimitMiddleware", None)
    assert middleware is not None, "RateLimitMiddleware has not been implemented"
    limited = FastAPI()

    @limited.get("/v1/test")
    def protected():
        return {"status": "ok"}

    @limited.get("/health")
    def health():
        return {"status": "ok"}

    limited.add_middleware(
        middleware,
        requests_per_minute=limit,
        max_buckets=max_buckets,
        clock=lambda: clock[0],
    )
    return TestClient(limited)


def test_rate_limit_returns_retry_metadata_and_resets_after_window():
    clock = [100.0]
    limited = rate_limited_client(clock)
    headers = {"Authorization": "Bearer analyst-secret"}
    first = limited.get("/v1/test", headers=headers)
    second = limited.get("/v1/test", headers=headers)
    blocked = limited.get("/v1/test", headers=headers)
    assert first.headers["ratelimit-remaining"] == "1"
    assert second.headers["ratelimit-remaining"] == "0"
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "60"
    assert blocked.json() == {"detail": "Rate limit exceeded"}

    clock[0] = 161.0
    reset = limited.get("/v1/test", headers=headers)
    assert reset.status_code == 200
    assert reset.headers["ratelimit-remaining"] == "1"


def test_rate_limit_isolates_credentials_and_skips_health_checks():
    limited = rate_limited_client([100.0], limit=1)
    assert limited.get("/v1/test", headers={"Authorization": "Bearer a"}).status_code == 200
    assert limited.get("/v1/test", headers={"Authorization": "Bearer b"}).status_code == 200
    assert limited.get("/v1/test", headers={"Authorization": "Bearer a"}).status_code == 429
    assert limited.get("/health").status_code == 200
    assert limited.get("/health").status_code == 200


def test_rate_limit_evicts_oldest_credential_bucket_at_memory_bound():
    limited = rate_limited_client([100.0], limit=1, max_buckets=2)
    for token in ("oldest-secret", "second-secret", "new-secret", "oldest-secret"):
        response = limited.get("/v1/test", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200


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
    monkeypatch.delenv("ANALYST_TOKENS", raising=False)
    monkeypatch.setenv("ANALYST_TOKEN_HASHES", hashed_credentials(**{token: "analyst"}))
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


def test_hosted_readiness_rejects_plaintext_analyst_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'ready.db'}")
    monkeypatch.setenv("ANALYST_TOKENS", '{"this-is-a-plaintext-production-token":"analyst"}')
    monkeypatch.delenv("ANALYST_TOKEN_HASHES", raising=False)
    assert client.get("/ready").status_code == 503


@pytest.mark.parametrize(
    "credentials",
    [
        '{"short":"analyst"}',
        '{"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA":"analyst"}',
        '{"not-hex-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz":"analyst"}',
        '{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":"bad\\nactor"}',
    ],
)
def test_hosted_readiness_rejects_malformed_token_fingerprints(monkeypatch, tmp_path, credentials):
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'ready.db'}")
    monkeypatch.delenv("ANALYST_TOKENS", raising=False)
    monkeypatch.setenv("ANALYST_TOKEN_HASHES", credentials)
    assert client.get("/ready").status_code == 503


def test_hashed_auth_accepts_case_insensitive_bearer_and_rejects_ambiguous_header(monkeypatch):
    token = "correct-horse-battery-staple-riskqueue-2026"
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    monkeypatch.delenv("ANALYST_TOKENS", raising=False)
    monkeypatch.setenv("ANALYST_TOKEN_HASHES", hashed_credentials(**{token: "alex"}))

    assert client.get("/v1/me", headers={"Authorization": f"bearer {token}"}).json() == {
        "analyst": "alex"
    }
    assert (
        client.get("/v1/me", headers={"Authorization": f"Bearer {token} extra"}).status_code == 401
    )


def test_auth_configuration_errors_are_not_exposed(monkeypatch):
    monkeypatch.setenv("API_AUTH_REQUIRED", "true")
    monkeypatch.delenv("ANALYST_TOKENS", raising=False)
    monkeypatch.setenv("ANALYST_TOKEN_HASHES", "not-json")
    response = client.get("/v1/me", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication service unavailable"}


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
