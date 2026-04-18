-- topic_common.sql — shared messages table for all Cairn topic databases
-- Used as the schema source for aws, azure, networking, systems, pam databases.
-- Mirrors the messages table in osint.sql exactly.
--
-- Schema version: 1
--
-- NOTE: _schema_meta INSERTs (schema_version, domain) are injected
-- programmatically by init.py for each database — do NOT add INSERT rows here.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;


-- ---------------------------------------------------------------------------
-- Schema metadata — values inserted at init time by init.py
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS _schema_meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);


-- ---------------------------------------------------------------------------
-- Messages — raw inbox (present in every topic database)
-- The ingest pipeline writes here first, then populates domain tables.
-- raw_content is the full YAML+markdown artifact exactly as posted.
-- frontmatter is the parsed envelope as JSON (redundant with raw_content,
--   kept for query convenience without re-parsing).
-- body is the markdown body stripped of frontmatter.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,           -- UUID v7
    agent_id        TEXT NOT NULL,
    thread_id       TEXT,
    message_type    TEXT NOT NULL,
                        -- finding | hypothesis | query | response |
                        -- alert | methodology_ref
    in_reply_to     TEXT REFERENCES messages(id),
    confidence      REAL CHECK (confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0)),
    tlp_level       TEXT CHECK (tlp_level IN ('white', 'green', 'amber', 'red') OR tlp_level IS NULL),
    promote         TEXT NOT NULL DEFAULT 'none'
                        CHECK (promote IN ('none', 'candidate', 'promoted', 'rejected')),
    tags            TEXT NOT NULL DEFAULT '[]', -- JSON array
    raw_content     TEXT NOT NULL,              -- full YAML+markdown as posted
    frontmatter     TEXT NOT NULL DEFAULT '{}', -- parsed frontmatter as JSON
    body            TEXT NOT NULL DEFAULT '',   -- markdown body only
    timestamp       TEXT NOT NULL,              -- ISO8601, agent-supplied
    ingested_at     TEXT NOT NULL,              -- ISO8601, server-set on receipt
    ext             TEXT NOT NULL DEFAULT '{}'  -- JSON extension point for future envelope fields
);

CREATE INDEX IF NOT EXISTS idx_messages_agent     ON messages(agent_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread    ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_type      ON messages(message_type);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_promote   ON messages(promote);
CREATE INDEX IF NOT EXISTS idx_messages_tlp       ON messages(tlp_level);
