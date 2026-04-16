-- Migration 002: add promotion_candidates table
-- Target schema version: 3
-- Applied by: cairn-admin migrate

BEGIN;

CREATE TABLE IF NOT EXISTS promotion_candidates (
    id                  TEXT PRIMARY KEY,
    entity              TEXT NOT NULL,
    entity_type         TEXT NOT NULL,
    trigger             TEXT NOT NULL
                            CHECK (trigger IN ('corroboration', 'human', 'agent')),
    status              TEXT NOT NULL DEFAULT 'pending_review'
                            CHECK (status IN ('pending_review', 'promoted', 'dismissed')),
    confidence          REAL CHECK (confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0)),
    source_message_ids  TEXT NOT NULL DEFAULT '[]',
    narrative           TEXT NOT NULL DEFAULT '',
    reviewer_id         TEXT,
    vault_path          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    ext                 TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_pc_entity      ON promotion_candidates(entity);
CREATE INDEX IF NOT EXISTS idx_pc_entity_type ON promotion_candidates(entity_type);
CREATE INDEX IF NOT EXISTS idx_pc_trigger     ON promotion_candidates(trigger);
CREATE INDEX IF NOT EXISTS idx_pc_status      ON promotion_candidates(status);
CREATE INDEX IF NOT EXISTS idx_pc_created     ON promotion_candidates(created_at);

UPDATE _schema_meta SET value = '3' WHERE key = 'schema_version';

COMMIT;
