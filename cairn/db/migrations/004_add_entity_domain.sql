-- Migration 003: add entity_domain column to promotion_candidates
-- Target schema version: 4
-- Applied by: cairn-admin migrate
--
-- Phase 4.2 — IT domain expansion.
-- Persists the domain hint from the entity extractor so the vault writer
-- can route notes to the correct subdirectory at promotion time, even when
-- the corroboration job runs hours before the human approves.
--
-- Safe to run on existing populated databases:
--   - ALTER TABLE ... ADD COLUMN with no NOT NULL constraint and no DEFAULT
--     sets the new column to NULL for all pre-existing rows — no data loss.
--   - The migration runner wraps each migration in a transaction.

BEGIN;

ALTER TABLE promotion_candidates
    ADD COLUMN entity_domain TEXT;  -- NULL for cybersecurity entities

UPDATE _schema_meta SET value = '4' WHERE key = 'schema_version';

COMMIT;
