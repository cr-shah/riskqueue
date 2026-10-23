# Six One-Commit Security Pull Requests

## Intent

Add six reviewable defense layers after `codex/security-defense-in-depth`. Each pull request contains exactly one conventional commit and protects a distinct trust boundary.

## Goals

- Stop rotating invalid bearer tokens from bypassing credential rate limits.
- Reject untrusted Host headers in hosted deployments.
- Verify the configured model artifact before `joblib` deserializes it.
- Restrict ingestion events to the configured bucket and key prefix.
- Bound object downloads before CSV parsing allocates unbounded memory.
- Keep credentials and control characters out of structured logs.

## Constraints

- Preserve local development defaults.
- Add no new runtime dependency.
- Keep generated metrics and website data unchanged.
- Use tests that exercise runtime behavior rather than source-text checks.
- Stack the PRs in merge order so every PR contains exactly one commit relative to its base.

## Approaches

### Independent branches from `main`

This makes every PR mergeable in any order, but duplicates and conflicts with the middleware introduced by PR #10.

### One six-commit pull request

This is easy to merge, but does not provide the requested one-commit review units.

### Stacked one-commit pull requests

Each branch starts from the preceding security branch and each PR targets that branch. This preserves one commit per PR, avoids duplicated middleware, and makes dependency order explicit. This is the selected approach.

## Review and merge order

PR #10 must land first. The six new pull requests then merge from authentication controls through logging controls in the order documented in the implementation plan.

