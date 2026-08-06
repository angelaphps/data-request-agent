# AGENTS.md

Read [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) before writing any code.
Implementation stages: [`PLAN.md`](PLAN.md). Product / setup: [`README.md`](README.md).

## Project guidelines

- Propose a short numbered plan before editing code.
- Keep changes minimal; avoid overengineering.
- Never commit secrets (`.env`, credentials) or large datasets.

## Rigor — no shortcuts

- A problem found is a problem solved, not routed around. Never skip,
  weaken, or comment out a failing test or check to make things pass —
  fix the cause, or stop and report it as blocked with what you learned.
- No placeholder fixes presented as done: no swallowed exceptions, no
  hardcoded values standing in for real logic, no mocking-away of the
  thing under test. If a temporary stub is genuinely needed, mark it
  `TODO`, say so out loud, and list it in the plan.
- If a task cannot be completed properly, say so explicitly rather than
  delivering something that only looks finished.

## Rigor — no unbacked assumptions

- Do not state anything you cannot back up. Verify claims by reading
  source, running code, or citing docs before acting.
- When something is unknown or ambiguous, ask or say "I don't know."
  Never invent APIs, column names, config values, or behaviour.
- In plans and summaries, distinguish **verified** facts from
  **assumptions**, and list assumptions a change depends on.

## Project invariants (non-negotiable — see PROJECT_CONTEXT.md)

- Identity comes only from the verified Slack event; role only from the
  `governance.admins` **table**. Never from message text or YAML at runtime.
- Results are delivered only to the requester's own private thread.
- Approval cards: names, flags, and estimates only — never data rows.
  Planning / scope LMs: descriptions and estimates only — never data rows.
  **No row-level personal / PII extracts in any language-model or
  analysis-engine context** (high-cardinality personal columns are
  stripped; low-cardinality personal *dimensions* such as a few device
  types may stay for local charts). Today’s analysis LM is schema-only.
  **CSV / file** delivery may include personal columns when the recorded
  approval explicitly covers them (e.g. a contact list).
- Delivery order is fixed: **execute → results check → personal-data
  guard → then** file | analysis | future conversational engine.
  Nothing reaches an analysis engine except through the guard.
- Every query passes inspection and the permission re-check before it
  runs, including retries and requests resumed after a wait.
- The query path is read-only, enforced by the database account
  (`READONLY_DATABASE_URL` when set; else business URL until Stage 6).
- `semantic_layer/` YAML is the **only** runtime source of truth for
  meanings (datasets, columns, metrics, joins, golden queries). Do not
  read catalog from governance tables. Seed admins into the `admins`
  table; do not seed the semantic catalog into the DB. Cross-datastore
  catalog storage can wait until that need is real.
- New datastores implement `stores.TabularStore` (MVP business data:
  `PostgresStore` + dummy Postgres; post-MVP: `BigQueryStore` for the
  production BigQuery dataset). New delivery targets implement
  `destinations.Destination`. No parallel paths.
- Business logic never lives in `slack_app.py` — it is a thin adapter.
- No `TEST_MODE` or approval-bypass flags.

## Conventions

- Python: PEP8 style, Ruff linting, NumPy-style docstrings.
- Use pytest for testing; prefer pandas/NumPy for analysis ops.
- Confirm before installing new dependencies (`poetry add`).
- Never write secrets; always use environment variables. Future store
  config may store a credential *name* in catalog/env — never the secret.

## Agent instructions

- Run `poetry run pytest -q` after changes and share results — including
  failures, verbatim. Do not summarize a red run as "mostly passing."
- When committing or managing branches, follow the user's git workflow
  rules (no force-push to main, no amend unless asked, HEREDOC messages).
