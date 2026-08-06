-- Legacy catalog relationship / golden-query tables (UNUSED at runtime).
-- Join keys and golden examples are read from semantic_layer/ YAML instead.
-- Kept so older DBs that applied this migration stay compatible; do not seed.
-- Apply: psql "$DATABASE_URL" -f migrations/002_catalog_relations.sql

BEGIN;

CREATE TABLE IF NOT EXISTS governance.relationships (
    from_dataset   TEXT NOT NULL REFERENCES governance.datasets (name) ON DELETE CASCADE,
    from_column    TEXT NOT NULL,
    to_dataset     TEXT NOT NULL,
    to_column      TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (from_dataset, from_column, to_dataset, to_column)
);

CREATE TABLE IF NOT EXISTS governance.golden_queries (
    name           TEXT PRIMARY KEY,
    dataset_name   TEXT NOT NULL REFERENCES governance.datasets (name) ON DELETE CASCADE,
    description    TEXT NOT NULL,
    sql            TEXT NOT NULL
);

ALTER TABLE governance.columns
    ADD COLUMN IF NOT EXISTS data_type TEXT,
    ADD COLUMN IF NOT EXISTS role TEXT;

COMMIT;
