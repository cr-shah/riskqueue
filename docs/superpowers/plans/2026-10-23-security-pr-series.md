# Six One-Commit Security Pull Requests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship six distinct security controls as six stacked pull requests containing one commit each.

**Architecture:** Extend the security middleware, model loader, cloud ingestion boundary, object store, and structured logger at their existing trust boundaries. Each task starts from the prior task's branch and produces one tested commit and one pull request.

**Tech Stack:** Python 3.12, FastAPI/Starlette ASGI, Pydantic, boto3 compatible clients, pytest, Ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-23-security-pr-series-design.md`

## Global Constraints

- Exactly one commit per new pull request relative to its base branch.
- No new runtime dependencies and no generated metric changes.
- Local development remains usable without hosted-only configuration.
- Every behavior change follows a witnessed RED to GREEN test cycle.

## Review Focus

- Rotating malformed or invalid bearer values must share a client quota while valid credentials retain individual quotas.
- Hosted configuration must fail closed when no trusted hosts are configured without breaking local TestClient requests.
- No model bytes may reach `joblib.load` before the expected SHA-256 digest is checked.
- Directly forged queue messages must not bypass Lambda ingestion origin restrictions.
- S3 and local objects one byte above the configured limit must be rejected before full reads.
- Nested sensitive log fields and newline characters must be redacted or escaped.

---

### Task 1: Credential stuffing resistance

**Files:** Modify `riskqueue/api/security.py`, `tests/test_api.py`, and security configuration docs.

- [ ] Add a failing test that rotates invalid bearer tokens from one client and reaches a shared client quota.
- [ ] Add a bounded client quota alongside the existing per-credential quota.
- [ ] Run API tests, Ruff, commit, push, and open a PR against `codex/security-defense-in-depth`.

### Task 2: Trusted hosts

**Files:** Modify `riskqueue/api/security.py`, `riskqueue/api/main.py`, `tests/test_api.py`, and deployment configuration.

- [ ] Add failing tests for rejected untrusted hosts and fail-closed hosted configuration.
- [ ] Add strict `TRUSTED_HOSTS` parsing and ASGI host validation.
- [ ] Run API tests, Ruff, commit, push, and open a PR against Task 1's branch.

### Task 3: Model artifact integrity

**Files:** Modify `riskqueue/modeling/artifacts.py`, `riskqueue/scoring.py`, modeling tests, and deployment docs.

- [ ] Add a failing test proving tampered model bytes are rejected before deserialization.
- [ ] Verify a configured SHA-256 digest before calling `joblib.load` and expose the saved digest from training.
- [ ] Run modeling/scoring tests, Ruff, commit, push, and open a PR against Task 2's branch.

### Task 4: Ingestion provenance

**Files:** Modify cloud message validation, Lambda/worker boundaries, tests, and cloud configuration docs.

- [ ] Add failing tests for a bucket or key outside the configured ingestion scope.
- [ ] Centralize bucket and prefix validation and enforce it in both Lambda and worker processing.
- [ ] Run cloud/worker tests, Ruff, commit, push, and open a PR against Task 3's branch.

### Task 5: Bounded object downloads

**Files:** Modify `riskqueue/cloud/storage.py`, worker setup, storage tests, and cloud configuration docs.

- [ ] Add failing local and S3 tests for an object exceeding the byte limit.
- [ ] Reject oversized objects using metadata before reading and cap the actual stream read.
- [ ] Run storage/worker tests, Ruff, commit, push, and open a PR against Task 4's branch.

### Task 6: Structured log redaction

**Files:** Modify `riskqueue/observability.py`, logging tests, and operations documentation.

- [ ] Add failing tests for nested secret fields and log control characters.
- [ ] Recursively redact sensitive keys, bound values, and preserve one JSON record per event.
- [ ] Run the full test and lint suites, request a fresh review, commit, push, and open a PR against Task 5's branch.

