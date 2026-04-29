-- Migration 007: add soft-delete metadata to message_index and topic DBs
-- Target schema version: 7
-- Applied by: cairn-admin migrate
--
-- Adds deletion tracking fields used by DELETE /messages APIs:
--   deleted_at: ISO8601 timestamp when soft-deleted (NULL = active)
--   deleted_by: authenticated actor id (agent_id) who deleted (NULL = active)
--
-- NOTE: This migration only covers index.db (message_index table).
-- The topic DB messages tables are handled by migrate_cmd in manage.py
-- because each topic DB is a separate SQLite file.

ALTER TABLE message_index
    ADD COLUMN deleted_at TEXT;

ALTER TABLE message_index
    ADD COLUMN deleted_by TEXT;

CREATE INDEX IF NOT EXISTS idx_midx_deleted_at
    ON message_index(deleted_at);

UPDATE _schema_meta
   SET value = '7'
 WHERE key = 'schema_version';
