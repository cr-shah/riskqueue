# RiskQueue Security Defense in Depth Design

## Intent

Strengthen the public RiskQueue operator service with five independently useful controls while preserving the synthetic GitHub Pages workspace and existing analyst workflow. The changes protect credentials, browser sessions, API compute, deployment images, and CI provenance.

## Goals

- Store only SHA-256 token fingerprints in hosted configuration and compare credentials in constant time.
- Apply strict browser and API response headers, including a hash-based CSP for the inline operator application.
- Reject oversized or compressed request bodies before JSON parsing.
- Bound repeated API use with a memory-safe per-credential rate limiter.
- Run the API container as an unprivileged user and pin third-party CI actions to immutable commits.

## Non-goals and anti-goals

- No change to model scores, generated metrics, queue ranking, public synthetic data, or database schema.
- No claim that in-process rate limiting replaces an edge gateway for a horizontally scaled deployment.
- No cookies, browser persistence, external identity provider, or new production dependency.
- Security controls must fail closed in hosted mode and must not expose tokens in logs, errors, or limiter keys.

## Constraints

- Keep FastAPI, Starlette, the static operator, Render, and GitHub Pages architecture.
- Retain plaintext `ANALYST_TOKENS` only for unauthenticated local development compatibility; hosted mode requires `ANALYST_TOKEN_HASHES`.
- The 1 MiB request ceiling must allow the existing 1,000-transaction API batch.
- The limiter must have bounded memory and emit standard retry metadata.
- Exactly five conventional commits implement the five layers.

## Approaches

1. **Defense in depth in the application and delivery pipeline (selected).** Adds focused controls at each exposed boundary, uses no new runtime service, and fits the current single-instance Render design.
2. **Put all protections at a managed API gateway.** Strong for distributed enforcement, but introduces infrastructure, cost, and credentials that the current public demo does not have.
3. **Replace bearer tokens with a full OIDC provider.** Best long-term identity model, but much larger than five focused commits and requires an external tenant and user lifecycle.

The selected approach gives immediate protection without pretending the demo is an enterprise identity platform. A future multi-instance deployment should move rate limiting and identity to shared infrastructure.

## Design

1. `ANALYST_TOKEN_HASHES` maps lowercase SHA-256 hex fingerprints to analyst IDs. Hosted readiness rejects plaintext token configuration. Bearer parsing is strict and comparison remains constant time.
2. A response middleware adds HSTS, nosniff, deny framing, no-referrer, restrictive permissions, cross-origin isolation headers, and `no-store`. `/operator` receives a CSP whose script and style hashes are derived from the checked-in inline assets.
3. An ASGI body limiter validates `Content-Length`, rejects non-identity content encodings, and counts streamed bytes before Pydantic parses input.
4. A fixed-window limiter protects `/v1` by credential fingerprint or client address, uses monotonic time, caps tracked buckets, and returns `429` with `Retry-After` and rate metadata.
5. The API image installs only packaged runtime code, runs as UID 10001, and the Docker context excludes local secrets and development material. GitHub Actions references use immutable commit SHAs with version comments.

## Verification

Each layer is introduced with a failing focused test, then the full Python suite runs. Final verification also runs Ruff, Prettier, ESLint, TypeScript, the website build, Node unit tests, Playwright, Dockerfile/deployment policy tests, and GitHub workflow validation.
