# PROJECT_CONTEXT.md — Slack Data Request Agent

This file gives overall context for development. It captures what the agent is, why it exists, and the shape of the system — not how to build it. Companion artifacts: the one-page architecture diagram, the detailed workflow graph, four component diagrams, and the problem-and-solution write-up.

## Project Overview

An internal Slack agent that fulfils data requests in natural language. A person messages the bot privately ("what's the churn year on year?", "give me customers and contact details in the South Island"); the agent works out what to query, gets the request approved by the right person where required, runs it safely against the company's tabular data, and returns either a downloadable file or an analysis reply (answer, small table, chart) in the same private thread.

It exists because data requests today are handled by hand: they queue behind other work, tax the few people with database access, and are governed by ad-hoc judgement with no consistent record of who approved what data going to whom. The agent makes access self-serve while making governance *stronger*, not weaker: authorization decided by a named administrator, sensitive data released only by recorded decision, and every step logged.

The guiding trade-off, stated once: a secure agent nobody wants to use is a failed project, and so is a popular one that leaks. Every design decision serves both.

## System Scope

**In scope (the MVP):**
- One Slack bot; private message threads for requests and delivery; one private administrators channel for approval cards. No other conversation surfaces.
- Natural-language requests over tabular business data. **MVP:** dummy
  Postgres warehouse via `BUSINESS_DATABASE_URL` and `PostgresStore`.
  **Post-MVP:** a BigQuery dataset behind the same `TabularStore` adapter
  (`BigQueryStore` stub exists; not wired). Governance state lives in a
  separate local Postgres (`DATABASE_URL`).
- Two access levels: administrators (run after preview) and requesters (run after an administrator approves).
- Two outputs: a downloadable file, or an analysis reply with tables and chart images — always stamped with when the data was read and showing what was run.
- Clarifying questions instead of guessing; a plain-language plan preview before anything runs; honest, useful decline messages.
- Release of flagged personal data on **CSV / file** delivery when — and only
  when — the recorded approval for that specific request covers it.
  **Analysis** strips **row-level** personal extracts (high-cardinality);
  low-cardinality personal *dimensions* (e.g. a few device types) may remain
  so charts keep a group axis. Planning LMs stay description-only.
- A semantic catalog authored as YAML under `semantic_layer/` and read
  directly at runtime (single location for meanings until a future
  cross-datastore need appears). **Next repo:** rewrite that YAML for the
  real warehouse; do not seed meanings into Postgres.
- A complete audit trail tied to each request.

**Out of scope (deferred, each behind a seam that exists in the MVP):**
- Other intake channels (forms, email, tickets) — permanently out by design; Slack is the product.
- Additional delivery destinations (e.g. shared drives), additional output formats (e.g. composed report documents), and delivery to shared channels for non-sensitive results.
- Additional datastores beyond the planned BigQuery swap (document stores,
  unstructured data) and the guard policies unstructured data would require.
  BigQuery itself is deferred post-MVP but the `TabularStore` seam is in place.
- Approval tiers, per-dataset approvers, delegation, standing grants.
- Management interfaces for administrators or catalog (admins YAML→table; catalog YAML files for MVP).
- A language-model "critic" that reviews whether an analysis answered the question.

## Architecture Summary

One orchestrated flow with five subsystems, plus a small governance database the agent owns:

- **Intake** — confirms the message genuinely came from Slack, acknowledges instantly, identifies the person (administrator or requester) from the agent's own records, and turns their words into a structured request — asking at most two clarifying questions rather than guessing.
- **Query Planner** — gathers the meanings of business terms and measures from the semantic catalog, drafts a single read-only query, and proves it safe before anyone sees it: an inspection confirms it touches only known tables and columns, and a trial run returns the result's column headings and a size estimate without reading data. Failures feed back into another attempt, three at most, then the agent bows out politely. The validated plan is shown to the person in plain words for confirmation.
- **Approval** — requesters' confirmed plans become an approval card in the administrators channel: their words verbatim, the data touched with sensitive items flagged, and the expected result shape — never data rows. The request saves itself and waits, minutes or hours; a decision records a permission scoped to this one request, with an expiry. Administrators skip this step for their own requests.
- **Run** — re-checks the query against the recorded permission (so neither retries nor long waits can widen access), then executes through a read-only account with time and size limits, via a `TabularStore` adapter that hides which datastore sits underneath (Postgres dummy today; BigQuery later).
- **Deliver** — **after** results check and the personal-data guard only.
  Branches to file preparation or analysis. No output engine (file composer,
  today’s runner, or a future conversational/PandasAI engine) may see the
  executor’s raw frame — only a **guarded** release. **CSV** may retain
  personal columns when the approval covers them (e.g. a contact list).
  **Analysis** strips row-level personal extracts (high-cardinality) before
  the engine; low-cardinality personal *dimensions* may remain for charts.
  Today’s analysis LM still gets column descriptions only (no data rows)
  and writes steps that run on our side on that guarded frame; a future
  conversational engine may use non-personal rows from that same frame,
  still never high-cardinality personal extracts. The reply lands in the
  requester's private thread.

The **governance database** is the agent's own small database: who the administrators are, recorded approvals, the audit log, and saved state for waiting requests. The **semantic layer** — dataset, table, column, and measure meanings — is authored under `semantic_layer/` and read directly at runtime (one location until a future cross-datastore need). Intake, the Planner, Approval, and Delivery all read that YAML catalog; the analysis tool receives a small per-request slice generated from it, never its own copy.

Three subsystems contain a language-model step (understanding the request, drafting the query, writing the analysis steps); each is immediately followed by a deterministic check. Everything else — routing, execution, guarding, delivery, audit — is plain code.

## Key Inputs and Outputs

**Inputs:**
- A person's natural-language request, in a private Slack message (identity from the verified Slack event, never from message text).
- Their follow-up answers to clarifying questions, and their Run/Cancel choice on the plan preview.
- An administrator's approve/decline on the approval card.
- The semantic layer: descriptions and measure definitions authored in this repository.
- Control data: administrators list (YAML → `governance.admins` table);
  sensitivity and meanings in `semantic_layer/` YAML (read at runtime, not
  seeded into Postgres); synthetic business rows in the business store.

**Outputs:**
- To the requester, in their private thread: a plain-language plan preview; status updates while waiting; and the result — a downloadable file, or an answer with a small table and chart image — showing what was run and when the data was read. When the agent cannot help: a plain reason and what it *can* answer.
- To administrators, in their channel: the approval card (metadata only, no data rows) and the outcome of their decision.
- To the record: an append-only audit trail of every request's journey — received, clarified, previewed, sent for approval, decided, run, checked, delivered, or failed.

## Design Rationale

**Authorization is data, enforced by structure.** Identity comes from the verified Slack platform; role from a table; permissions from recorded approvals; and the inability to write or over-read from the database account itself. Nothing security-critical is an instruction to a language model — a model can be talked out of an instruction, not out of a missing grant.

**The language model proposes; code disposes.** Only three steps are probabilistic, and each is fenced: the parsed request is validated against a strict shape, the drafted query must pass inspection and a trial run, and analysis planning/execution never sees **row-level** personal extracts (today’s LM also sees no data rows — descriptions only; a future conversational engine may see non-personal post-guard rows). Retries regenerate the proposal but re-face the same checks, so persistence cannot widen access.

**Humans decide what only humans can.** The requester confirms that the plan matches their intent (before) and can judge the result against what was run (after). The administrator judges whether *this person* should receive *this data* on a **file** — reviewing the request and the data it touches, not machine-generated query text. The flags on the approval card make the sensitive-data question explicit. The guard then carries that decision for CSV (release what was approved, hide what was not) and strips **row-level** personal extracts before analysis (low-cardinality dimensions may remain for charts).

**The delivery audience always equals the authorized person.** Requests and results live in the requester's private thread; a bot mention in a public channel processes nothing there. Approval authorizes a person, so delivery reaches exactly that person.

**Waiting is a designed state, not an edge case.** Requests save themselves at every human pause and resume on a button press — after minutes, hours, or a restart — bounded by a 48-hour approval expiry (small-team leave/illness buffer), with the permission re-checked on resume and results stamped with the data's read time.

**Analysis replies stay compact in Slack.** An analysis request returns, in the requester's private thread: a direct text answer, an inline markdown summary table (≤ `analysis_summary_max_rows`, default 20 — a preview, not a dump), and a chart PNG. Full row extracts remain the file-delivery path.

**Analysis engine (MVP vs end goal).** Today’s analysis is intentionally basic (“dumb”): a schema-only LM proposes groupby/aggregation/chart choices; our restricted pandas runner + matplotlib produce a one-shot answer, table, and PNG on a **guarded** frame (row-level personal extracts stripped; low-cardinality dimensions may remain for charts). **Same-thread follow-ups** (Stage 4) reuse stored aggregates/summary only — never high-cardinality personal extracts. **End goal:** richer conversational analysis. **PandasAI is the deferred engine for that** (swap behind `analysis.py`), still **downstream of the guard** (execute → results check → guard → analysis frame → engine), still using the upstream YAML catalog — not a second buried semantic layer, and never fed raw executor output or high-cardinality personal extracts. Delivery, approval, and Slack shaping stay put.

**Honesty over confidence.** The agent asks rather than guesses, previews rather than surprises, names an alternative when it declines, and flags a result that looks wrong rather than delivering it with a straight face. Trust in the answers is the product; the checks exist to protect it.

**Every deferred capability has a seam in the MVP.** New datastores are new
`TabularStore` implementations (BigQuery is the planned production warehouse);
new delivery destinations are new implementations of the delivery interface;
approval tiers are configurations of the one gate; report formats are new
compositions of the same guarded results. Growth is implementation, not
re-architecture.