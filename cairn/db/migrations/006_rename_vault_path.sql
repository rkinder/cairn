-- Migration 006: Rename vault_path → kb_path in promotion_candidates
-- Only applies to databases that predate the Phase 4.6 schema update.
-- Fresh installs (schema_version >= 6) already have kb_path — no action needed.
-- Adds BEGIN/COMMIT and schema_version bump (fixes Bug 004).

BEGIN;

ALTER TABLE promotion_candidates RENAME COLUMN vault_path TO kb_path;

UPDATE _schema_meta SET value = '6' WHERE key = 'schema_version';

COMMIT;