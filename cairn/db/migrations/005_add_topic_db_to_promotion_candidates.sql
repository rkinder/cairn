-- Migration 005: add topic_db column to promotion_candidates
-- Target schema version: 5
-- Applied by: cairn-admin migrate
--
-- Bug 002 fix — promotion queue UI cannot fetch source message bodies
-- because the candidate record does not store which topic database the
-- source messages live in.  The UI requires a db= parameter on
-- GET /messages/{id} to locate the message, which requires this column.
--
-- Safe to run on existing populated databases:
--   - ALTER TABLE ... ADD COLUMN with no NOT NULL constraint and no DEFAULT
--     sets the new column to NULL for all pre-existing rows — no data loss.
--   - Existing candidates will have NULL topic_db; the UI handles this
--     gracefully by skipping the source message fetch when topic_db is NULL.

BEGIN;

ALTER TABLE promotion_candidates
    ADD COLUMN topic_db TEXT;  -- slug of the primary topic DB (e.g. 'osint', 'aws')

UPDATE _schema_meta SET value = '5' WHERE key = 'schema_version';

COMMIT;
