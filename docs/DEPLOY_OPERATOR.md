# Deploy the analyst service

The public [RiskQueue website](https://cr-shah.github.io/riskqueue/) remains a static historical synthetic evaluation. The separate FastAPI service serves `/operator`, persistent cases, and authenticated API routes. `render.yaml` defines a Render web service and private-network PostgreSQL database. It does **not** create resources until a Render workspace imports and applies the Blueprint. The selected web and database plans are paid; review current Render pricing in the dashboard before applying it.

## First deployment

1. In Render, connect the `cr-shah/riskqueue` repository and create a Blueprint from `render.yaml` on `main`. Review the service and database plans and region before applying.
2. At the `ANALYST_TOKEN_HASHES` prompt, enter a JSON object mapping a SHA-256 token fingerprint to a nonempty analyst ID. Generate a token locally with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`, save it in a password manager, then fingerprint it without putting the token in shell history: `python -c 'import getpass,hashlib; print(hashlib.sha256(getpass.getpass("Token: ").encode()).hexdigest())'`. A setting looks like `{"<64-character-lowercase-hash>":"alex"}`. Never commit the original token, paste it in an issue, or put it in a URL.
3. Wait for the pre-deploy migration and `/ready` health check to pass. Render supplies `DATABASE_URL` from the private database connection. The application converts Render's `postgresql://` URL to the installed psycopg v3 driver. The database rejects external connections via `ipAllowList: []`.
4. Open `https://<assigned-service>.onrender.com/operator`, enter the analyst token, and verify `/v1/me` succeeds. The token stays in tab memory; a reload requires re-entry. Check `/ready` returns `status: ready`. Do not publish the service URL as an operational system until you verify a real case can be claimed, noted, resolved, and audited.

The Blueprint sets `API_AUTH_REQUIRED=true`, so scoring previews and model metrics require the same bearer token. All case and replay routes already require it. Hosted mode rejects the legacy plaintext `ANALYST_TOKENS` variable and accepts only `ANALYST_TOKEN_HASHES`; plaintext tokens remain available solely for local development while authentication is not marked as hosted. Render provides TLS at its public service URL. Rotate an analyst token by changing its fingerprint in `ANALYST_TOKEN_HASHES` and redeploying. Set different tokens for different analysts.

Every `/v1` credential is limited to 120 requests per fixed service minute by default, and every network client shares a second 240-request quota so rotating invalid bearer values cannot bypass throttling. Successful responses expose the tightest remaining quota; exhausted clients or credentials receive `429` and `Retry-After`. `API_RATE_LIMIT_PER_MINUTE` and `API_CLIENT_RATE_LIMIT_PER_MINUTE` accept integers from 1 through 10,000. The limiter stores only SHA-256 credential fingerprints and client addresses, caps both in-memory bucket maps, and deliberately excludes health checks. This protects the single-instance Blueprint. Before scaling to multiple API instances, move enforcement to a shared edge gateway or distributed rate-limit store so the quota is consistent across instances.

## What the first deployment provides

The service starts in `SCORING_MODE=demo`. This is a deterministic **development heuristic**, not a trained or calibrated fraud probability. The database begins empty: a running service alone does not create review cases. To populate it, configure and run the existing SQS worker against an authorized data source, or use a separately controlled synthetic test ingestion. The worker requires its own SQS URL, object store, and IAM configuration. Do not connect real financial or personal data to the demo scorer.

For trained scoring, build a versioned model artifact with the repository's training workflow, deliver it to the API and worker through controlled artifact storage, set `SCORING_MODE=trained` and `MODEL_ARTIFACT_DIR` for both, and verify score parity and the model/feature version in stored cases. The Blueprint deliberately contains no model binary or secret. This change is required before treating the operator as a real fraud decisioning system.

## Verification and rollback

After deployment, verify `GET /ready` returns 200, `GET /v1/me` returns 401 without a token and the configured analyst with a token, and `POST /v1/score` returns 401 without a token. Use only synthetic cases for the first end-to-end investigation. The case's notes, status, outcome, and audit entries should survive a service redeploy. Monitor Render deployment and database logs for migration or readiness failures. A failing pre-deploy migration prevents the new version from serving; keep a database backup before schema upgrades and restore it if a migration has already changed data. Do not drop or recreate the database as rollback.

If `/ready` fails, check that `ANALYST_TOKEN_HASHES` is a nonempty JSON mapping of lowercase SHA-256 fingerprints, the database connection is available, and the selected scorer can load. Error responses omit secret and configuration details. The existing `/health` endpoint checks the scorer but does not prove database or analyst readiness; Render uses `/ready`.

GitHub Pages never receives the database URL or analyst tokens. The website remains separate and does not silently switch from synthetic data to the operational API.

## Build and supply-chain controls

The API image builds the Python package from an isolated temporary directory, removes build sources, and runs as the unprivileged `riskqueue` user with UID 10001. The Docker context excludes environment files, Git metadata, tests, documentation, datasets, generated artifacts, infrastructure state, and frontend dependencies. Packaged Alembic migrations and the operator document remain inside the installed wheel.

CI installs Python dependencies from the committed `uv.lock` with `uv sync --frozen`, installs npm dependencies from `package-lock.json` with lifecycle scripts disabled, and invokes the already installed Playwright executable. Every third-party GitHub Action is pinned to a full immutable commit SHA, each workflow has read-only default repository permissions, and every job has a timeout. Dependabot or a deliberate maintenance change should update both the human-readable version comment and its reviewed SHA.
