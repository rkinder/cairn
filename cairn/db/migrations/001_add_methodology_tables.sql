-- Migration 001: Add methodology execution tracking
-- Target:  index.db
-- Applies: schema_version 1 → 2
--
-- Run via: cairn-admin migrate
--
-- This migration adds the methodology_executions table that Phase 3 requires
-- for tracking which methodology versions agents have run and their review status.
--
-- Constraint preserved: methodology TEXT is never stored here.
-- Only gitlab_path + commit_sha are stored so content can always be retrieved
-- from GitLab at the exact version that was executed.

BEGIN;

CREATE TABLE IF NOT EXISTS methodology_executions (
    id                  TEXT PRIMARY KEY,       -- UUID v7
    methodology_id      TEXT NOT NULL,          -- logical ID (Sigma 'name' field or path-based)
    gitlab_path         TEXT NOT NULL,          -- full path in repo (e.g. methodologies/apt29/named-pipe.yml)
    commit_sha          TEXT NOT NULL,          -- exact commit SHA that was executed
    status              TEXT NOT NULL DEFAULT 'proposed'
                            CHECK (status IN ('proposed', 'peer_reviewed', 'validated', 'deprecated')),
    parent_version      TEXT,                   -- commit SHA of the parent methodology (lineage)
    agent_id            TEXT NOT NULL,          -- agent that ran this methodology
    result_message_ids  TEXT NOT NULL DEFAULT '[]', -- JSON array of blackboard message IDs from this run
    reviewer_id         TEXT,                   -- set when status transitions to 'validated'
    notes               TEXT NOT NULL DEFAULT '',   -- optional reviewer/transition notes
    created_at          TEXT NOT NULL,          -- ISO8601
    updated_at          TEXT NOT NULL,          -- ISO8601
    ext                 TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mex_methodology ON methodology_executions(methodology_id);
CREATE INDEX IF NOT EXISTS idx_mex_gitlab_path ON methodology_executions(gitlab_path);
CREATE INDEX IF NOT EXISTS idx_mex_agent       ON methodology_executions(agent_id);
CREATE INDEX IF NOT EXISTS idx_mex_status      ON methodology_executions(status);
CREATE INDEX IF NOT EXISTS idx_mex_created     ON methodology_executions(created_at);

-- Bump schema version.
UPDATE _schema_meta SET value = '2' WHERE key = 'schema_version';

COMMIT;
