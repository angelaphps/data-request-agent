# data-request-agent — implementation plan

Staged plan. Scenario tests in `tests/test_scenarios.py` are the gates. Read
`PROJECT_CONTEXT.md`, `README.md`, and `AGENTS.md` before coding. No `TEST_MODE`
or approval-bypass flags.

Product narrative and setup: [README.md](README.md). Architecture invariants:
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

---

## Status — built vs not built

### Built (Stages 0–3 + hardening)

- Governance DB (`DATABASE_URL`), migrations `001`/`002`, seed, dual DB
  (business via `BUSINESS_DATABASE_URL`), LangGraph `PostgresSaver`, Slack
  Socket Mode
- Full spine: intake → plan (LLM SQL + rich catalog **JOIN KEYS** /
  golden queries) → Submit/Cancel → approval (incl. **Approve without
  personal data**) → re-check → execute → results check → **guard** → deliver
- File path (CSV) + **dumb** one-shot analysis (`analysis.py` + matplotlib)
  on the **guarded** frame only
- Scope LLM, trend/stats → `wants_analysis`, 48h approval expiry, public
  redirect, data-as-of stamp
- Scenario gates green (including scenario **6**); Stage 1–2 analysis mock
  removed

**Delivery order (do not misread diagrams):**

```text
execute → results check → personal-data guard
  → then ONLY: file | analysis | future conversational engine
```

Nothing reaches any output engine except through the guard. The executor never
feeds analysis (present or future) a raw frame.

### Not built / deferred

- Conversational follow-ups (“why is Web higher?”) / lasting thread analysis
  context — **stubs only:** `migrations/003_thread_context.sql` and
  `followups.py` exist; not wired into delivery / Slack routing yet (Stage 4)
- Smarter analysis beyond one-shot groupby/agg + bar/line; **PandasAI** behind
  `analysis.py`
- Pre-select / rewrite to omit personal columns (today: execute then
  guard-strip)
- Dedicated business read-only DB role (A3 still open in practice)
- Catalog admin UI; standing grants; analysis critic; shared-channel delivery

```mermaid
flowchart LR
  s0[Stage0_3_DONE]
  s4[Stage4_thread_memory]
  s5[Stage5_smarter_engine]
  s6[Stage6_pii_and_ops]
  s0 --> s4 --> s5 --> s6
```

**Next coding focus:** Stage 4 → Stage 5 (smarter / conversational analysis).
See [README — Next stages](README.md#next-stages).

---

## Needed from you (ops / demo)

### Admin list: repo file → table at runtime

- **Author** admins in-repo as YAML (`config/admins.yaml`) — Slack user IDs +
  display names. Include synthetic `U_ADMIN` for tests; add your real `U…` ID
  for the manual demo.
- **Load** via `scripts/seed.py` (upsert).
- **Runtime** role checks always read `governance.admins`, never the YAML.

Repo file = who to seed. Table = who may act. Upsert does not remove old rows;
deactivate with SQL to test non-admin flows.

### How to get Slack IDs

**Your user ID (`U…`):** Profile → ⋯ → Copy member ID.

**Channel `#data-request-approval` (`C…`):** Open channel → About → Channel ID.
Set `ADMIN_CHANNEL_ID=C…` in `.env`. Invite the bot to that channel.

### Checklist

| Item | Notes |
|------|--------|
| Postgres | Dedicated DB `data_request_agent` on `DATABASE_URL` |
| DB auth | `postgresql://test:test@localhost:5432/data_request_agent` |
| Slack tokens | Bot + app-level (Socket Mode); Messages tab on |
| `ADMIN_CHANNEL_ID` | `C…` for `#data-request-approval` |
| Admin YAML | Your `U…` in `config/admins.yaml` (then seed) |
| LLM API key | pydantic-ai (scope, SQL, analysis plan) |
| Business URL | `BUSINESS_DATABASE_URL` (e.g. Render `beam_neb0`) |

---

## Staging notes (historical)

1. Scenario catalog matches gates (A1).
2. Results check landed in Stage 2; Stage 1 delivered files without it first.
3. Stage 1 built pause/resume skeleton, then filled the spine.

```mermaid
flowchart LR
  intake[Intake]
  planner[Planner]
  preview[Plan_preview_interrupt]
  approval[Approval_interrupt]
  run[Run_recheck_execute]
  deliver[Guard_and_deliver]
  intake --> planner --> preview --> approval --> run --> deliver
```

---

## Assumptions

| ID | Assumption |
|----|------------|
| **A1** | Scenario catalog below; numbers match stage gates. |
| **A2** | **Two databases:** local Postgres (`DATABASE_URL`) for `governance`; external business DB via `BUSINESS_DATABASE_URL`. LangGraph checkpoints via `PostgresSaver.setup()` on governance. |
| **A3** | Eventually: governance writer + business read-only SELECT. MVP may still use a shared credential for business until Stage 6. |
| **A4** | Scenario tests use synthetic identities (`U_ADMIN` / `U_REQ`) and `Command(resume=...)`; no live Slack for gates. |
| **A5** | Tests inject fixed proposers; inspection, trial, approval, re-check, guard, delivery always run for real. |
| **A6** | Stage 3 analysis path is live (text + table + chart); scenario 6 is a gate. |
| **A7** | Stage 3: plain LM + restricted runner (not PandasAI). **End goal:** conversational analysis; **PandasAI is the deferred engine** behind `analysis.py` after thread context (Stage 4) + no-row / prompt proof (Stage 5). Catalog stays upstream. |
| **A8** | Admins authored in YAML; seed upserts into `governance.admins`; runtime reads the table only. |

### A1 — Scenario catalog (gates)

| # | Gate stage | Intent | Status |
|---|------------|--------|--------|
| 1 | 2 | Clarification loop (≤2 questions) then plan | Done |
| 2 | 1 | Admin DM → preview Submit → file in private thread | Done |
| 3 | 1 | Requester → Submit → admin Approve → file | Done |
| 4 | 1 | Admin Reject → notify; no final execute | Done |
| 5 | 1 | Permission re-check blocks stale/invalid permission | Done |
| 6 | 3 | Real analysis path (text + table + chart) | Done |
| 7 | 2 | Results check: mismatch → one retry → honest failure | Done |
| 8 | 2 | 48h approval expiry + notify; public redirect; data-as-of | Done |
| 9 | 1 | Audit events across successful requester lifecycle | Done |
| 10 | 1 | Personal-data guard + analysis path (no Stage 1–2 mock) | Done |
| 11 | 1 | Non-admin Approve → refused; request can keep waiting | Done |

Row-cap / timeout: unit tests + assertions inside scenarios 2/3.

---

## Dependencies

**Stages 0–3 (installed):** `slack-bolt`, `langgraph`,
`langgraph-checkpoint-postgres`, `pydantic-ai`, `sqlglot`, `psycopg`, `pandas`,
`matplotlib`, `pyyaml`, `python-dotenv`, `pytest`.

**Stage 5 (later):** PandasAI only after confirm + no-row/prompt gate — do not
install early (AGENTS.md).

---

# Stage 0 — Foundations — DONE

**Gate:** smoke test — catalog + admin lookup from seed.
`pytest -q tests/test_smoke_governance.py` green.

### 0.1 Rename control_plane → governance

1. **Build:** Rename to governance database / `governance.py` / schema
   `governance`. Drop custom `graph_checkpoints` from migration.
2. **Files:** `governance.py`; `migrations/001_governance.sql`; README.
3. **Verify:** `rg -n control_plane` clean; import `governance`.

### 0.2 pyproject + config

1. **Build:** Settings for DB URLs, Slack, row cap, timeout, approval expiry
   (48h), planner retries (3), clarify cap (2), paths.
2. **Files:** `pyproject.toml`, `config.py`, `.env.example`.

### 0.3 Governance migration

1. **Build:** `CREATE SCHEMA governance`; admins, catalog, approvals, audit.
   `PostgresSaver.setup()` separate.
2. **Files:** `migrations/001_governance.sql` (+ later `002_catalog_relations.sql`).

### 0.4 Semantic YAML → catalog tables

1. **Build:** Load `semantic_layer/` into governance (incl. relationships /
   golden queries via richer YAML).

### 0.5 Seed script + in-repo admin list

1. **Build:** `config/admins.yaml`; `scripts/seed.py`; `is_admin` = SQL.

### 0.6 Audit writer

1. **Build:** Append-only `audit(event, actor, payload)`.

**Stage 0 done when:** smoke test passes.

---

# Stage 1 — Spine (file delivery E2E) — DONE

**Gates:** scenarios **2, 3, 4, 5, 9, 10, 11**.

### 1.0–1.9 (shipped)

Pause/resume skeleton → state/graph → thin Slack adapter → intake → planner
(inspect + trial) → Submit/Cancel preview → approval + authority at click →
re-check execute → guard + file delivery → audit suite.

Later hardening (still Stage 1 spine): LLM SQL drafter, rich catalog, Submit
label, Approve without personal data, non-admin refuse keeps waiting.

**Stage 1 done when:** gates 2, 3, 4, 5, 9, 10, 11 pass.

---

# Stage 2 — Resilience and honesty — DONE

**Gates:** scenarios **1, 7, 8**.

### 2.1 Clarification loop (≤2)

### 2.2 Results check + one retry

### 2.3 Expiry (48h), “data as of”, public redirect

**Stage 2 done when:** 1, 7, 8 pass and Stage 1 gates still pass.

---

# Stage 3 — Analysis path — DONE

**Gates:** scenario **6** + `test_analysis_context_has_no_data_rows`.

### Decision: PandasAI v3 vs plain LM + restricted runner

| | PandasAI v3 | Plain LM + our runner |
|--|-------------|------------------------|
| Docs | [Agent](https://docs.pandas-ai.com/v3/agent); semantic layer [experimental](https://docs.pandas-ai.com/v3/semantic-layer/semantic-layer) | pydantic-ai + pandas |
| Fit | Second semantic layer; DataFrames into Agent — hard to prove no-row invariant | Per-request **descriptions only** from our catalog |
| Execution | Optional Docker sandbox | AST/allowlist runner we own |
| Switch cost | High if ripping out later | Seam `analysis.py`; PandasAI later is a swap |

**Shipped for Stage 3:** plain LM + restricted runner. Analysis MVP is a **dumb
one-shot** (groupby/agg + bar/line) on the **guarded** frame. Chart lib:
**matplotlib**.

**End goal (Stages 4–5):** conversational analysis (follow-ups about the data
in-thread). **PandasAI is the deferred engine for that**, swapped behind
`analysis.py` after thread memory exists and prompt/row behavior is proven —
catalog stays upstream, never buried inside PandasAI. Always:
execute → results check → **guard** → engine.

### 3.1 Replace mock behind same branch — DONE

1. **Built:** Schema slice; LM plan; restricted runner; Slack text + markdown
   table (≤ `analysis_summary_max_rows`) + chart PNG; full extracts stay on
   file path.
2. **Files:** `analysis.py`, `delivery.py`, `destinations.py`.
3. **Verified:** scenario 6; no-data-rows context test; full `pytest -q`.

**Stage 3 done when:** gates above pass (they do).

---

# Stage 4 — Thread memory for analysis follow-ups — NEXT

**Goal:** After a chart/table, “why is Web higher?” / “what about India?” stays
in-thread without a full new request every time.

1. **Build:** Persist last **guarded** analysis context per DM thread
   (`governance.thread_context` — migration `003` already authored; finish
   save on analysis deliver + load on follow-up).
2. **Route:** In `slack_app.py`, when graph is idle and message looks like a
   follow-up and context exists → answer path; clear new extracts still start
   the full spine.
3. **Answer:** From stored stats / summary table / prior answer first
   (`followups.py` exists as a stub — wire it). `needs_new_request` → fall
   through to full graph with a suggested ask.
4. **Invariant:** LM still must not see pre-guard raw executor rows; prefer
   aggregates + small summary already released by the guard.
5. **Files:** `governance.py`, `delivery.py`, `slack_app.py`, `followups.py`,
   migration `003`.
6. **Verify:** new scenario(s) — analysis deliver → follow-up reply without new
   approval when envelope unchanged; fresh “top 10…” still starts full spine.
7. **Deps:** none new required for heuristic path; OpenAI for LM follow-ups.
8. **From you:** none.

**Stage 4 done when:** follow-up scenario(s) pass and Stage 0–3 gates still pass.

---

# Stage 5 — Smarter analysis engine — AFTER STAGE 4

**Goal:** Move from dumb one-shot toward conversational exploration on the
**guarded** frame.

### 5.1 Expand allowlisted runner (immediate smarter win)

1. **Build:** More aggs / chart types / clearer narratives in `analysis.py`
   without changing Slack/approval/guard.
2. **Verify:** richer scenario 6 variants (multi-measure, time-ish cuts if
   SQL returns them).

### 5.2 PandasAI (or equivalent) swap — conversational engine

1. **Build:** Implement behind `analysis.py` seam; input = **post-guard**
   frame (+ Stage 4 thread context for follow-ups). Catalog remains our
   upstream YAML→governance brief — do **not** bury a second semantic layer
   inside PandasAI.
2. **Gate (blocking):** Prove or document whether PandasAI puts dataframe rows
   in prompts; if yes, configure/sandbox/limit so product invariant holds, or
   explicitly amend PROJECT_CONTEXT for the conversational engine only.
3. **Invariant test:** engine input is always post-guard (no raw executor
   path).
4. **Deps:** Confirm before `poetry add` (AGENTS.md).
5. **From you:** accept prompt/row evidence or invariant amendment.

**Stage 5 done when:** smarter/conversational gates pass; guard-order test
green; catalog still single source of truth.

---

# Stage 6 — PII hardening + ops

1. **Approve without personal data:** prefer not selecting personal columns
   (SQL rewrite / column projection) vs execute-then-strip only.
2. **`READONLY_DATABASE_URL`** for business query execution (close A3).
3. **Catalog/ops:** safer admin deactivate-on-seed, richer golden queries on
   thin datasets, dual YAML↔DB production decision documented.

**Stage 6 done when:** strip-or-project behavior covered by tests; read-only
execute path documented and used when URL set.

---

## Citations

- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph checkpointers: https://docs.langchain.com/oss/python/langgraph/checkpointers
- PostgresSaver: https://github.com/langchain-ai/langgraph/blob/5931a5f0/libs/checkpoint-postgres/README.md
- Slack Bolt actions: https://docs.slack.dev/tools/bolt-python/concepts/actions
- Slack file upload / thread: https://docs.slack.dev/reference/methods/files.completeUploadExternal
- pydantic-ai structured output: https://ai.pydantic.dev/output/
- PandasAI semantic layer (experimental): https://docs.pandas-ai.com/v3/semantic-layer/semantic-layer
- PandasAI Agent: https://docs.pandas-ai.com/v3/agent

---

## Open questions

| # | Question | Status |
|---|----------|--------|
| 1 | Can `PostgresSaver` use schema `governance`? | Open / low priority — works on default search_path today |
| 2 | Does PandasAI put dataframe rows in prompts? | **Stage 5 gate** — must answer before swap |
| 3 | Sync Socket Mode + `invoke` vs async | Decided for MVP: sync invoke is fine |
| 4 | Chart library for Stage 3 | **Resolved: matplotlib** |
