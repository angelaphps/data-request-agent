-- Per-thread analysis context for follow-up questions ("why is it like that?").
-- Apply: psql "$DATABASE_URL" -f migrations/003_thread_context.sql

BEGIN;

CREATE TABLE IF NOT EXISTS governance.thread_context (
    thread_key     TEXT PRIMARY KEY,  -- channel_id:thread_ts
    requester_slack_id TEXT NOT NULL,
    channel_id     TEXT NOT NULL,
    thread_ts      TEXT NOT NULL,
    context        JSONB NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
