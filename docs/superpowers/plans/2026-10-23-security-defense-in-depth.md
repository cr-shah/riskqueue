# RiskQueue Security Defense in Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five independently testable security layers to the RiskQueue operator service and delivery pipeline in exactly five commits.

**Architecture:** Keep the current FastAPI and static-site architecture. Add dependency-free security primitives at the ASGI and authentication boundaries, then harden the existing container and GitHub Actions delivery path.

**Tech Stack:** Python 3.12, FastAPI/Starlette ASGI, Pydantic, pytest, Docker, GitHub Actions, Render

**Spec:** `docs/superpowers/specs/2026-10-23-security-defense-in-depth-design.md`

## Global Constraints

- Do not alter model scores, generated metrics, queue ranking, public synthetic data, or the database schema.
- Hosted authentication must fail closed and never retain plaintext tokens in configuration-derived lookup keys.
- Keep the existing 1,000-transaction batch usable below a 1 MiB request ceiling.
- Add no runtime dependency.
- Produce exactly five conventional commits, one per security layer.

## Review Focus

- Malformed or mixed-case bearer schemes must not bypass validation or leak configuration details.
- Chunked bodies without `Content-Length` must still hit the byte ceiling before application parsing.
- Rotating invalid bearer values must not grow limiter state without bound.
- CSP hashes must match the exact checked-in operator style and script bytes.
- Container and workflow hardening must not remove packaged migrations or Pages deployment permissions.

---

### Task 1: Hashed hosted analyst credentials

**Files:**
- Modify: `riskqueue/api/cases.py`
- Modify: `riskqueue/api/main.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_case_workflow.py`
- Modify: `render.yaml`
- Modify: `.env.example`
- Modify: `docs/DEPLOY_OPERATOR.md`
- Create: the design and plan documents named above

**Interfaces:**
- Produces: `configured_analysts() -> dict[str, str]` keyed by SHA-256 hex fingerprints in hosted mode; `actor()` accepts an RFC case-insensitive Bearer scheme.

- [ ] Add tests for valid hashed tokens, malformed fingerprints, hosted plaintext rejection, strict bearer parsing, and generic readiness failure.
- [ ] Run the focused tests and confirm they fail for the missing hashed-token behavior.
- [ ] Implement fingerprint configuration and constant-time token validation.
- [ ] Update hosted configuration and operator deployment documentation.
- [ ] Run focused and full Python tests.
- [ ] Commit as `security: hash hosted analyst credentials`.

### Task 2: Browser and API response security policy

**Files:**
- Create: `riskqueue/api/security.py`
- Modify: `riskqueue/api/main.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces: `SecurityHeadersMiddleware`; consumes the static `operator.html` path to calculate CSP hashes.

- [ ] Add tests for security headers, no-store behavior, strict operator CSP, and exact inline asset hashes.
- [ ] Run the focused tests and confirm they fail because headers are absent.
- [ ] Implement and register the response middleware.
- [ ] Run focused and full Python tests.
- [ ] Commit as `security: enforce browser response policy`.

### Task 3: Request body limits

**Files:**
- Modify: `riskqueue/api/security.py`
- Modify: `riskqueue/api/main.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces: `RequestSizeLimitMiddleware(app, max_bytes=1_048_576)`.

- [ ] Add tests for oversized declared bodies, oversized streamed bodies, conflicting lengths, compressed bodies, and a normal JSON request.
- [ ] Run the focused tests and confirm the attack cases reach parsing instead of returning the intended status.
- [ ] Implement the ASGI limiter with 413, 400, and 415 responses.
- [ ] Run focused and full Python tests.
- [ ] Commit as `security: bound API request bodies`.

### Task 4: Bounded API rate limiting

**Files:**
- Modify: `riskqueue/api/security.py`
- Modify: `riskqueue/api/main.py`
- Modify: `tests/test_api.py`
- Modify: `.env.example`
- Modify: `docs/DEPLOY_OPERATOR.md`

**Interfaces:**
- Produces: `RateLimitMiddleware(app, requests_per_minute=120, max_buckets=4096)` keyed by non-reversible credential fingerprint or client address.

- [ ] Add deterministic tests for quota consumption, isolation, retry headers, window reset, and bounded eviction.
- [ ] Run the focused tests and confirm excess calls are not rejected.
- [ ] Implement the thread-safe, bounded limiter and register it for `/v1` routes.
- [ ] Document the single-instance boundary and edge-gateway requirement for horizontal scaling.
- [ ] Run focused and full Python tests.
- [ ] Commit as `security: rate limit operator APIs`.

### Task 5: Container and CI supply-chain hardening

**Files:**
- Modify: `Dockerfile`
- Modify: `.dockerignore`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/website.yml`
- Create: `tests/test_deployment_security.py`
- Modify: `docs/DEPLOY_OPERATOR.md`

**Interfaces:**
- Consumes: the packaged `riskqueue/migrations` directory used by `python -m riskqueue.db.migrate`.

- [ ] Add policy tests that require a non-root final image, prohibit broad `COPY . .`, reject secret-prone Docker context entries, and require full-length action SHAs.
- [ ] Run the policy tests and confirm they fail on current deployment files.
- [ ] Harden the API image and Docker ignore list; resolve and pin every action reference to an immutable commit.
- [ ] Run focused tests, full Python and web quality gates, and Playwright.
- [ ] Commit as `security: harden build and deployment supply chain`.
