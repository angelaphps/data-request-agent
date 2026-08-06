-- Governance schema for data-request-agent
-- Apply with: psql "$DATABASE_URL" -f migrations/001_governance.sql
--
-- Runtime semantic meanings live in semantic_layer/ YAML, not in the
-- datasets/columns/metrics tables below (those are unused legacy leftovers).
--
-- LangGraph checkpoints are NOT created here. Bootstrap them separately via
-- PostgresSaver.setup() (langgraph-checkpoint-postgres).

BEGIN;

CREATE SCHEMA IF NOT EXISTS governance;
CREATE SCHEMA IF NOT EXISTS business;

CREATE TABLE IF NOT EXISTS governance.admins (
    slack_user_id   TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'approver',
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS governance.catalog_versions (
    id              BIGSERIAL PRIMARY KEY,
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_path     TEXT NOT NULL,
    checksum        TEXT NOT NULL,
    payload         JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS governance.datasets (
    name            TEXT PRIMARY KEY,
    description     TEXT NOT NULL,
    owner           TEXT,
    sensitivity     TEXT NOT NULL DEFAULT 'none'
                    CHECK (sensitivity IN ('none', 'internal', 'personal', 'restricted')),
    table_schema    TEXT NOT NULL DEFAULT 'business',
    table_name      TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS governance.columns (
    dataset_name    TEXT NOT NULL REFERENCES governance.datasets (name) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL,
    sensitivity     TEXT NOT NULL DEFAULT 'none'
                    CHECK (sensitivity IN ('none', 'internal', 'personal', 'restricted')),
    PRIMARY KEY (dataset_name, name)
);

CREATE TABLE IF NOT EXISTS governance.metrics (
    name            TEXT PRIMARY KEY,
    definition      TEXT NOT NULL,
    expression      TEXT NOT NULL,
    dataset_name    TEXT NOT NULL REFERENCES governance.datasets (name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS governance.approvals (
    id              TEXT PRIMARY KEY,
    request_id      TEXT NOT NULL,
    requester_slack_id TEXT NOT NULL,
    channel_id      TEXT NOT NULL,
    thread_ts       TEXT NOT NULL,
    plan            JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    touches_personal_data BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    decided_at      TIMESTAMPTZ,
    decided_by      TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS governance.approval_events (
    id              BIGSERIAL PRIMARY KEY,
    approval_id     TEXT NOT NULL REFERENCES governance.approvals (id),
    actor_slack_id  TEXT NOT NULL,
    action          TEXT NOT NULL,
    detail          JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS governance.audit_log (
    id              BIGSERIAL PRIMARY KEY,
    event           TEXT NOT NULL,
    actor_slack_id  TEXT,
    payload         JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS approvals_status_idx
    ON governance.approvals (status);
CREATE INDEX IF NOT EXISTS audit_log_created_at_idx
    ON governance.audit_log (created_at DESC);

COMMIT;
