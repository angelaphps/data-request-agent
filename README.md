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
   checks the result shape, and strips personal columns if approval did not
   allow them.
6. **Deliver** — CSV or analysis lands only in the requester’s private DM.

Public-channel @mentions are ignored; the bot asks the person to DM it instead.

---

## Outputs

| Kind | When | What arrives in Slack |
|------|------|------------------------|
| **File** | Default for extract-style asks | CSV + the SQL that ran + a “data as of” timestamp |
| **Analysis** | Ask mentions analyze / chart / trend / stats | Short answer, markdown table (≤20 rows), chart image |

Analysis today is a one-shot summary (groupby / aggregate + bar or line chart),
not a conversational analytics agent yet.

---

## System pieces

Two databases:

| Role | Env var | Purpose |
|------|---------|---------|
| Governance | `DATABASE_URL` | Admins, catalog, approvals, audit log, saved request state |
| Business | `BUSINESS_DATABASE_URL` | The data the SQL actually queries |

The **semantic layer** (`semantic_layer/*.yaml`) defines tables, columns,
joins, measures, and example queries. `scripts/seed.py` loads that YAML and
`config/admins.yaml` into the governance database. At runtime the bot reads
those **tables** — edit YAML, then re-seed.

Language models draft the request scope, the SQL, and (for analysis) the chart
plan. Deterministic checks always follow: SQL inspection, trial run,
permission re-check, and the personal-data guard before delivery.

The bot uses Slack **Socket Mode** (outbound connection). No public URL is
required for demos; workspace members can use it while the process is online.

---

## Getting started

**You need:** Python 3.11+, [Poetry](https://python-poetry.org/), local
Postgres (governance), a Slack app with Socket Mode, an OpenAI API key, and a
business Postgres database.

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
| `BUSINESS_DATABASE_URL` | Business / warehouse DB |
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

## Try these asks

**File**

- `top 10 users by session times`
- `how much revenue did the US bring in?`

**Analysis** (include a word like analyze / chart / trend / stats)

- `analyze average session duration by device`
- `chart total revenue by country`

**Personal data** (as a non-admin): ask for users including `device_type`, then
have an admin choose **Approve without personal data** — the CSV should omit
that column.

In the sample warehouse, devices are `Android`, `iOS`, and `Web`.

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
| **4** | Thread memory so analysis follow-ups stay in-thread |
| **5** | Smarter / conversational analysis (PandasAI behind `analysis.py`) |
| **6** | PII projection before execute; `READONLY_DATABASE_URL`; ops hardening |

Delivery order stays fixed:
`execute → results check → personal-data guard → file | analysis | future engine`.

---

## Status

Internal MVP — suitable for demos and development, not production hardening.
Never commit `.env` or other secrets.
