# data-request-agent

Slack bot for governed, self-serve data access.

DM the bot in plain English. It plans a read-only SQL query, shows a preview,
gets admin approval when needed, runs the query, and replies in the same
private thread with a **CSV** or a short **analysis** (text, small table,
chart).

Every request is audited. Sensitive columns are released only when an admin
approves them.

| Doc | Role |
|-----|------|
| [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) | Architecture and invariants |
| [`PLAN.md`](PLAN.md) | Build stages and scenario gates |
| [`AGENTS.md`](AGENTS.md) | Coding rules for agents |

---

## What a request looks like

1. **Ask** — user DMs the bot (e.g. “top 10 users by session time”).
2. **Clarify** — bot may ask up to two short questions if the ask is ambiguous.
3. **Preview** — bot shows a plain-language plan and SQL; user clicks
   **Submit** or **Cancel**.
4. **Approve** (non-admins only) — an admin sees a card in the approval
   channel and chooses **Approve**, **Approve without personal data**, or
   **Reject**. Admins skip this for their own requests. Approvals last 48 hours.
5. **Run** — bot re-checks permission, executes against the business database,
   checks the result shape, then applies the personal-data guard.
6. **Deliver** — CSV or analysis lands only in the requester’s private DM.
   CSV may keep personal columns when approval covers them; analysis never
   receives personal columns (even if that request’s CSV approval would).

Public-channel @mentions are ignored; the bot asks the person to DM it instead.

---

## Outputs

| Kind | When | What arrives in Slack |
|------|------|------------------------|
| **File** | Default for extract-style asks | CSV + the SQL that ran + a “data as of” timestamp. Personal columns only if explicitly approved (e.g. a contact list). |
| **Analysis** | Ask mentions analyze / chart / trend / stats | Short answer, markdown table (≤20 rows), chart image — always on a **PII-stripped** frame (no personal columns to the analysis engine or LLM). |

Analysis today is a one-shot summary (groupby / aggregate + bar or line chart),
not a conversational analytics agent yet (Stages 4–5).

---

## System pieces

Two databases today:

| Role | Env var | Purpose |
|------|---------|---------|
| Governance | `DATABASE_URL` | Admins, catalog, approvals, audit log, saved request state (local Postgres) |
| Business | `BUSINESS_DATABASE_URL` | Dummy Postgres warehouse the SQL queries for the MVP |

Business data is reached through `stores.TabularStore`. **MVP:**
`PostgresStore` + the dummy Postgres URL. **Post-MVP:** the same spine
reads a **BigQuery** dataset via `BigQueryStore` (stub in `stores.py` —
not wired yet). Planner, approval, guard, and delivery stay put; only the
store adapter and catalog/SQL dialect work change.

The **semantic layer** (`semantic_layer/*.yaml`) defines tables, columns,
joins, measures, and example queries. `scripts/seed.py` loads that YAML and
`config/admins.yaml` into the governance database. At runtime the bot reads
those **tables** — edit YAML, then re-seed.

Language models draft the request scope, the SQL, and (for analysis) the chart
plan. Deterministic checks always follow: SQL inspection, trial run,
permission re-check, and the personal-data guard before delivery. **Nothing
with personal / PII columns reaches an analysis engine or LLM** — including
the upcoming conversational / PandasAI path. Explicit admin approval remains
the path for releasing personal columns in **CSV** exports.

The bot uses Slack **Socket Mode** (outbound connection). No public URL is
required for demos; workspace members can use it while the process is online.

---

## Getting started

**You need:** Python 3.11+, [Poetry](https://python-poetry.org/), local
Postgres (governance), a Slack app with Socket Mode, an OpenAI API key, and a
**dummy business Postgres** URL for the MVP (`BUSINESS_DATABASE_URL`).
Production BigQuery comes later behind the same store seam.

```bash
git clone https://github.com/angelaphps/data-request-agent.git
cd data-request-agent
poetry install
cp .env.example .env
```

Fill in `.env`:

| Variable | What it is |
|----------|------------|
| `SLACK_BOT_TOKEN` | Bot token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | Socket Mode app token (`xapp-…`) |
| `ADMIN_CHANNEL_ID` | Approval channel ID (`C…`, not `#name`) |
| `DATABASE_URL` | Local governance DB |
| `BUSINESS_DATABASE_URL` | Dummy business Postgres (MVP warehouse) |
| `OPENAI_API_KEY` | OpenAI key |

`SLACK_SIGNING_SECRET` is optional for Socket Mode. See `.env.example` for
tunables (`APPROVAL_EXPIRY_HOURS`, `ANALYSIS_SUMMARY_MAX_ROWS`, and so on).

Create the governance schema, then seed:

```bash
psql "$DATABASE_URL" -f migrations/001_governance.sql
psql "$DATABASE_URL" -f migrations/002_catalog_relations.sql

# Add your Slack user ID (U…) to config/admins.yaml, then:
poetry run python scripts/seed.py
```

Keep `U_ADMIN` in the admin file for tests. Role checks use the
`governance.admins` table only — YAML changes take effect after seed.

```bash
# Start the bot
poetry run data-request-agent

# Run tests
poetry run pytest -q
```

### Slack IDs for demo

- **Your user ID (`U…`):** Profile → ⋯ → Copy member ID. Add it to
  `config/admins.yaml`, then re-seed.
- **Approval channel (`C…`):** Open the channel → About → Channel ID. Set
  `ADMIN_CHANNEL_ID` in `.env` and invite the bot.

---

## UAT test plan

Short manual checks for Slack. Use both an **admin** and a **requester** account
when you can. Admins watch the approval channel for cards.

### Before you start

- Open a **DM** with the bot (not a public channel).
- Know whether you are an **admin** or a **requester**.
- Every happy path ends in the **same private DM thread** with the SQL that
  ran and a “Data as of: …” stamp.

### Common path

DM ask → (maybe 1–2 clarifying questions) → plan preview → **Submit** or
**Cancel**. Admins run after Submit. Requesters wait for admin **Approve** /
**Approve without personal data** / **Reject**.

### Example questions by workflow

| What you’re testing | Who | Example ask | What to expect |
|---------------------|-----|-------------|----------------|
| **File / CSV (admin, no approval)** | Admin | `top 10 users by session times` | Preview → Submit → CSV in DM |
| **File / CSV (aggregate)** | Either | `how much revenue did the US bring in?` | CSV (or short result) + SQL + timestamp |
| **Requester approval** | Non-admin | `top 10 users by session times` | After Submit, card in approval channel; result only after admin acts |
| **Reject** | Non-admin + admin | Same as above, admin clicks **Reject** | Requester notified; no CSV |
| **Cancel** | Either | Any ask, click **Cancel** on preview | Stops; no run |
| **Analysis** | Either | `analyze average session duration by country` | Text + small table + chart (use words like analyze / chart / trend / stats) |
| **Analysis (chart)** | Either | `chart total revenue by country` | Same analysis-style reply |
| **Personal data — strip** | Non-admin + admin | `users sample with device_type` (or “users including device type”) | Card shows personal flag → **Approve without personal data** → CSV **without** `device_type` |
| **Personal data — keep on CSV** | Non-admin + admin | Same ask → **Approve** | CSV **may include** `device_type`; analysis path would still never keep PII |
| **Clarify** | Either | Something vague, e.g. `session stuff` or `give me the numbers` | Bot asks ≤2 clarifying questions before a plan |
| **Public redirect** | Anyone | @mention the bot in a **public** channel | Bot does **not** run there; asks you to DM it |

**Sample warehouse tip:** devices are `Android`, `iOS`, `Web`. Prefer
**country** for analysis demos; `device_type` is flagged personal (good for
approval / CSV tests).

### Quick pass/fail checklist

- [ ] Results only appear in the requester’s private DM
- [ ] Preview has plan + SQL (not a sample of real rows)
- [ ] Non-admin needs approval; admin does not
- [ ] Analysis asks produce chart/table path, not only a silent CSV
- [ ] “Approve without personal data” removes personal columns from the CSV
- [ ] Public @mention does not leak data into the channel

---

## Layout

```text
data_request_agent/   Python app (Slack adapter, graph, SQL, delivery)
semantic_layer/       Catalog YAML (source of truth for meanings)
config/admins.yaml    Admin list to seed
migrations/           Governance SQL
scripts/              seed.py, expire_approvals.py
tests/                Scenario and unit tests
```

---

## Next stages

Stages 0–3 (spine, resilience, one-shot analysis) are done — that is the MVP.
Detail and gates live in [`PLAN.md`](PLAN.md).

| Stage | Focus |
|-------|--------|
| **4** | Thread memory so analysis follow-ups stay in-thread (PII-stripped context only) |
| **5** | Smarter / conversational analysis (PandasAI behind `analysis.py`; never personal columns to the LLM) |
| **6** | PII projection before execute; `READONLY_DATABASE_URL`; ops hardening |
| **Later** | Swap business store to **BigQuery** (`BigQueryStore` behind `TabularStore`) |

Delivery order stays fixed:
`execute → results check → personal-data guard → file | analysis | future engine`.

Split after the guard: **CSV** may include personal columns when explicitly
approved; **analysis / LLM** always runs on a PII-stripped frame.

Business data today is a **dummy Postgres**; post-MVP the query path targets a
**BigQuery** dataset through the same adapter — not a rewrite of Slack,
approval, or delivery.

---

## Status

Internal MVP — suitable for demos and development, not production hardening.
Never commit `.env` or other secrets.
