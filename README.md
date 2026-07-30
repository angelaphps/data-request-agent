# data-request-agent — MVP

Slack-native agent that turns natural-language data requests into **approved, audited, read-only queries**, then returns either a **CSV** or a (currently basic) **analysis reply** in the requester’s private DM thread.

It exists so data access can be self-serve **without** weakening governance: identity from Slack, role from a table, permission from a recorded approval, sensitive columns released only when approved, and every step in an audit log.

> Guiding trade-off: a secure agent nobody uses is a failed project; a popular one that leaks is worse. Every MVP decision serves both.

Companion context: [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) (product/architecture), [`PLAN.md`](PLAN.md) (staged build), [`AGENTS.md`](AGENTS.md) (dev invariants).

---

## What the MVP does

| Path | Who | What you get |
|------|-----|----------------|
| **File** | Anyone (admin skips approval) | CSV in the DM + “what ran” + **Data as of** stamp |
| **Analysis** | Asks that look like analyze / chart / trend / stats | Short text + markdown summary table (≤20 rows) + chart PNG — **not** a full dump |
| **Approval** | Non-admins after **Submit** | Card in `#data-request-approval` (ask, plan, columns, personal flag — **no data rows**). **Approve** · **Approve without personal data** · **Reject**. Approvals last **48 hours**. |

**Analysis is intentionally “dumb” right now** (one-shot groupby/agg + bar/line). It is **not** PandasAI.

**End goal:** conversational analysis — after a chart/table, the user asks follow-ups about *that* result (“why is Web higher?”, “what about India?”) in the same thread without starting from scratch every time.

**PandasAI** is the **deferred engine** for that conversational analysis layer (swap behind `analysis.py`), once thread context + governance still hold and the no-data-rows invariant can be proven. It still sits **after** the personal-data guard — same as today’s runner. See [Next stages](#next-stages-of-the-mvp).

---

## System write-up (how it works)

```text
DM ask → Intake → Planner → Submit/Cancel → [Approval if non-admin]
      → Permission re-check → Execute (business DB)
      → Results check → Personal-data guard
      → then ONLY: file delivery  |  analysis / conversational engine
```

**Fixed order — do not misread diagrams the other way.**  
Nothing reaches any output engine (dumb analysis **or** future PandasAI conversational analysis) except **through the PII guard**. The executor never feeds an analysis engine raw rows. The conversational bot interrogates the **guarded** frame (and thread memory of that guarded summary) — never the pre-guard result set.

**Two databases**

| DB | Env | Contents |
|----|-----|----------|
| **Governance** (local) | `DATABASE_URL` | Admins, semantic catalog, approvals, audit, LangGraph checkpoints |
| **Business** (e.g. Render `beam_neb0`) | `BUSINESS_DATABASE_URL` | Real tabular data the SQL runs against |

**Semantic layer (central, upstream — not buried in an analysis tool)**

- **Authored** as YAML under `semantic_layer/` (datasets with fields, join keys, measures, golden queries + `metrics.yaml`).
- **Seeded** into `governance.*` (`datasets`, `columns`, `relationships`, `golden_queries`, `metrics`).
- **Runtime** reads the **tables** for LLM briefs (JOIN KEYS + golden SQL first), inspection, and sensitivity — not the YAML files on every message.

**LM proposes; code disposes**

1. Scope / parse → structured ask  
2. SQL draft → sqlglot inspect + trial  
3. Analysis plan → allowlisted pandas ops on an **already guarded** frame  

No security-critical rule is “please don’t” in a prompt: role and permission live in data and checks.

**Privacy / PII**

- Columns marked `sensitivity: personal` in the catalog (e.g. `users.device_type`).
- Approval card shows **Personal data: yes/no**.
- **Approve without personal data** keeps the query but strips personal columns at the **guard (after execute, before deliver)** — drop columns, don’t scramble.
- Analysis LM context: **column descriptions only**, never result rows.

**Slack surfaces**

- Requester: private DM only. Public @mentions get a redirect (“message me directly”).
- Admins: one channel (`ADMIN_CHANNEL_ID`) for approval cards.

---

## Challenges faced (honest notes)

### Auth, permissions, and data privacy

Easy to rabbit-hole. Conceptually we still had to **crack enough** that a company would even consider company data:

- Identity only from verified Slack events (never from message text).
- Role only from `governance.admins` (YAML is seed source; runtime is the table).
- Per-request approval with expiry + re-check before run.
- Personal columns flagged on the card and enforced by the guard.
- Audit trail for the journey.

Without that story, nobody lets you point a bot at the warehouse. The MVP proves the **shape** of management, not a full enterprise IAM product.

### Semantic layer placement

Lots of disagreement with models and sketches. Consensus that held:

- **Don’t bury the catalog inside PandasAI (or any analysis engine).** It must sit **central and upstream** — Intake, Planner, Approval, and Delivery all share one meaning of “revenue”, “device”, join keys, sensitivity.
- In this bot it’s even more important: the LLM **never sees real data rows**, only schemas and catalog metadata. If the catalog is thin or wrong, the bot looks stupid or refuses safe asks.
- **YAML vs DB for production:** still an open product call. This MVP uses **both**: YAML as the authored source of truth in-repo; Postgres governance tables as the runtime store after seed. That keeps git review of meanings and fast runtime reads. Pure-DB or pure-file catalogs are possible later; the dual layout is deliberate for now.

### YAML format quality

Prose-only catalogs were not enough (“join users somehow…”). Relation-aware YAML mattered:

- `fields` with `role`, types, `related_table` / `related_field`
- dataset `measures`
- `golden_queries` (example SQL with real joins)

Those land in `governance.relationships` / `golden_queries` and are injected into the LLM brief as **JOIN KEYS** and **GOLDEN QUERY EXAMPLES**. Without that, asks like “top users by session” or “duration by device” routinely failed even when the SQL was trivial.

---

## Setup

### Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- Local Postgres for governance
- Slack app (Socket Mode) + OpenAI API key
- Business Postgres URL (dummy warehouse is fine)

### 1. Clone and install

```bash
cd data-request-agent
poetry install
cp .env.example .env
# edit .env — see below
```

### 2. Configure `.env`

| Variable | Purpose |
|----------|---------|
| `SLACK_BOT_TOKEN` | Bot token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | App-level token for Socket Mode (`xapp-…`) |
| `SLACK_SIGNING_SECRET` | Signing secret (optional if Socket Mode only) |
| `ADMIN_CHANNEL_ID` | Channel ID `C…` for approval cards (not `#name`) |
| `DATABASE_URL` | Local governance, e.g. `postgresql://test:test@localhost:5432/data_request_agent` |
| `BUSINESS_DATABASE_URL` | Warehouse the agent queries |
| `OPENAI_API_KEY` | Scope, SQL draft, analysis plan |

Optional: `APPROVAL_EXPIRY_HOURS=48`, `ANALYSIS_SUMMARY_MAX_ROWS=20`, `READONLY_DATABASE_URL=…`

### 3. Create governance DB and migrate

```bash
# create role/db if needed, then:
psql "$DATABASE_URL" -f migrations/001_governance.sql
psql "$DATABASE_URL" -f migrations/002_catalog_relations.sql
```

(`003_thread_context.sql` and `followups.py` are stubs for Stage 4 thread
memory — optional; not required for the current Stages 0–3 MVP.)

### 4. Admins

Edit `config/admins.yaml` with real Slack member IDs (`U…`). Include synthetic `U_ADMIN` for tests; **do not** put requester test IDs in the admin list.

Runtime checks **`governance.admins` only**. Changing YAML without seeding does nothing. Upsert does not remove old rows — deactivate with SQL if you need to act as a non-admin:

```sql
UPDATE governance.admins SET active = FALSE WHERE slack_user_id = 'U…';
-- flip back to TRUE when done
```

### 5. Seed

```bash
poetry run python scripts/seed.py
```

Loads admins + `semantic_layer/**` into governance and probes the business DB.  
**Seed does not start Slack.** After YAML catalog edits: seed again (restart only needed for code changes).

### 6. Run the bot

```bash
poetry run data-request-agent
```

Leave it running (Socket Mode). Ctrl+C stops it.

### 7. Tests

```bash
poetry run pytest -q
```

Scenario gates live in `tests/test_scenarios.py` (see `PLAN.md`).

---

## Manual smoke tests (useful asks)

**File path**

- `top 10 users by session times` → CSV  
- `how much revenue did the US bring in?` → CSV  

**Analysis path** (must sound like analyze / chart / trend / stats)

- `analyze average session duration by device` → text + table + chart  
- `chart total revenue by country`  

**Approval / PII** (as non-admin, then admin in approval channel)

- Ask for users with `device_type` → card shows personal flag → try **Approve without personal data** → CSV without that column  

**Device note:** warehouse values are `Android` / `iOS` / `Web` (Web ≈ desktop).

---

## Repo map

```text
data_request_agent/     # app: intake, planner, approval, execution, delivery, analysis, Slack
semantic_layer/         # authored catalog (YAML)
config/admins.yaml      # authored admins (seed → DB)
migrations/             # governance schema (001–002 required; 003 Stage 4 stub)
scripts/seed.py         # seed admins + catalog
scripts/expire_approvals.py
tests/                  # scenario + unit gates
PROJECT_CONTEXT.md      # product / architecture narrative
PLAN.md                 # staged implementation (0–3 done; 4–6 next)
AGENTS.md               # invariants for agents/humans
```

---

## Next stages of the MVP

Ordered by the product end goal (**conversational analysis**), not ceremony:

1. **Thread memory for analysis**  
   Wire `003_thread_context.sql` + `followups.py` (stubs today): keep last ask /
   summary stats / small table in the DM thread so follow-ups land on *that* result.

2. **Conversational analysis engine (PandasAI deferred here)**  
   Swap the dumb one-shot runner in `analysis.py` for PandasAI (or equivalent) so follow-ups can explore the **already guarded** frame conversationally. Catalog stays upstream. **Never** wire the executor straight into PandasAI — order stays execute → results check → **guard** → conversational engine.

3. **Harder PII path**  
   Don’t select personal columns when approving without personal data (today: execute then strip). Read-only DB role for query execution (`READONLY_DATABASE_URL`).

4. **Catalog ops**  
   Settle YAML-only vs DB-only vs dual for production; safer admin seed/deactivate; richer golden queries on thin datasets.

5. **Further product polish**  
   Standing grants / delegation, shared-channel delivery for non-sensitive results, analysis “critic”, multi-store adapters — deferred seams in `PROJECT_CONTEXT.md`.

---

## Design invariants (non-negotiable)

From `AGENTS.md` / `PROJECT_CONTEXT.md`:

- Identity from Slack event; role from admins **table**.
- Results only to the requester’s private thread.
- No data rows in LM context — descriptions / aggregates we choose to expose only.
- No data rows on approval cards.
- Inspect + permission re-check before every run (including after waits).
- Query path read-only at the database account.
- `semantic_layer/` is the authored source of truth for meanings; analysis gets a per-request slice generated from it.
- New stores implement `stores.TabularStore`; new delivery targets implement `destinations.Destination`.
- Business logic does not live in `slack_app.py` (thin adapter).

---

## License / status

Internal MVP / demo codebase. Not production-hardened. Treat secrets in `.env` as local-only; never commit them.
