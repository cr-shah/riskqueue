# Public RiskQueue workspace

The public application is hosted at https://cr-shah.github.io/riskqueue/ through the repository's existing GitHub Pages service. `website/` contains dependency-free ES modules, semantic HTML, CSS, and SVG charts. Development dependencies provide formatting, ESLint, TypeScript checks for JavaScript, Node unit tests, Playwright, and axe accessibility checks. The Python risk engine, API, Streamlit dashboard, event worker, and database adapters remain available.

## Product workflows

- Overview: exact capacity selection, interactive value-capture curves, exposure by transaction type, and the next cases to review.
- Review queue: strategy, threshold, cost, loss fraction, capacity, search, type, probability band, local status, pagination, and CSV export.
- Case evidence: ranking arithmetic, actual history-only features, optional known outcome, browser-local review status and notes, and JSON export.
- Scenario studio: freeze a baseline, change the alternative, inspect retrospective costs and capture, and share both policies through the URL.
- Model insights: measured model comparison, precision–recall, equal-count calibration bins, and threshold policies.
- Scoring lab: the deterministic development scorer mirrors `riskqueue.scoring.demo_probability` using Web Crypto for the SHA-256 term. It is explicitly separate from calibrated model predictions.
- Evidence: source CSV hash, synthetic data seed, chronological split, saved PSI drift demonstration, architecture, model card, and reproducible evidence JSON.

## Data and interpretation

`python -m scripts.build_website` reads the checked-in `artifacts/figures` results. It refuses non-demo labels, non-demo transaction IDs, invalid numeric data, inconsistent counts, and duplicate IDs. An explicit allowlist excludes sender and recipient account identifiers, raw event payloads, and operational database fields. Only `dist/site` is published. No secrets, application source tree, operational data, or model binaries are uploaded.

The source CSV SHA-256 identifies the exact scored dataset. URL parameters contain the alternative and baseline policy; a JSON export contains these assumptions, metrics, source identity, and selected transaction IDs. Exports contain no fabricated timestamps. Arithmetic is tested against every checked-in Python capacity and threshold result. Running the demo regenerates the source artifacts; backend and library versions can affect regenerated models, so the checked-in source hash is the authoritative snapshot identity.

The queue first filters by minimum score, stably ranks by the selected strategy and descending amount, and takes exactly the requested capacity (or the number eligible if smaller). Display filters apply afterward. Fraud value capture always uses all labeled fraud dollars as the denominator. Labels never influence queue rank. Expected loss is probability × amount × loss fraction. Retrospective modeled cost is review spend plus the loss fraction of missed labeled fraud dollars, assuming reviewed fraud loss is fully prevented. It is not actual recovered money. Constant review costs mean expected loss and net review value have the same ordering; negative-value cases are not automatically removed.

The public site operates entirely in the browser on a historical synthetic snapshot. It does not call cloud services or mutate PostgreSQL review state. Status and notes use local storage namespaced by the dataset hash; blocked storage falls back to the current session. No analytics or external runtime CDN is used. A Content Security Policy limits runtime requests to the same origin. The lab sends no inputs to a server. Case explanations expose real feature context and policy arithmetic; without attribution artifacts, they make no SHAP or causal contribution claims.

## Local verification

```bash
npm ci
npm run format:check
npm run lint
npm run typecheck
npm run build
npm test
npx playwright install chromium
npm run test:e2e
.venv/bin/python -m pytest --cov=riskqueue --cov-report=term-missing
.venv/bin/ruff check .
```

Use `npm run preview` for the built site on localhost:4173. Browser tests exercise desktop and mobile layouts, filters, exact capacity, local reviews, keyboard dialogs, policy URLs, exports, empty and failed loading states, and all six views with WCAG A/AA axe rules.

## Publishing

GitHub Pages must use **GitHub Actions** as its publishing source. The `Website` workflow validates PRs. After a tested PR merges into main, it builds and validates again, uploads only `dist/site`, and deploys through `actions/deploy-pages`. The workflow uses the `github-pages` environment and GitHub's short-lived deployment identity; no extra credentials are stored in the repository. Existing AWS infrastructure is not changed by a public website deployment.

For public browser verification after deployment:

```bash
RISKQUEUE_URL=https://cr-shah.github.io/riskqueue/ npm run test:e2e
```

The API remains a separate Python service. Transaction amounts are bounded to $1 trillion, event hours to 10 million, steps require integers, and batch IDs must be unique. Queue API requests optionally accept `manual_review_cost` and `loss_fraction`, with defaults preserving the previous behavior.
