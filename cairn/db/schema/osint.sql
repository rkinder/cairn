-- osint.db — open-source intelligence topic database
-- Stores raw messages plus structured OSINT entities, relationships, and sources.
--
-- Schema version: 1

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;


-- ---------------------------------------------------------------------------
-- Schema metadata
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS _schema_meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

INSERT OR IGNORE INTO _schema_meta (key, value) VALUES
    ('schema_version', '1'),
    ('domain',         'osint');


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

CREATE INDEX IF NOT EXISTS idx_messages_agent    ON messages(agent_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread   ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_type     ON messages(message_type);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_promote  ON messages(promote);
CREATE INDEX IF NOT EXISTS idx_messages_tlp      ON messages(tlp_level);


-- ---------------------------------------------------------------------------
-- Entities — observable indicators and named objects extracted from messages
--
-- entity_type vocabulary (open — new types go in ext, earn a column when common):
--   ip, domain, url, hash_md5, hash_sha256, hash_sha1,
--   email, username, org, asn, certificate, file_path, registry_key,
--   named_pipe, mutex, service_name, user_agent
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,           -- UUID v7
    message_id      TEXT NOT NULL REFERENCES messages(id),
    entity_type     TEXT NOT NULL,
    value           TEXT NOT NULL,
    confidence      REAL CHECK (confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0)),
    tlp_level       TEXT CHECK (tlp_level IN ('white', 'green', 'amber', 'red') OR tlp_level IS NULL),
    first_seen      TEXT NOT NULL,              -- ISO8601
    last_seen       TEXT NOT NULL,              -- ISO8601; updated on corroboration
    ext             TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_entities_type_value  ON entities(entity_type, value);
CREATE INDEX IF NOT EXISTS idx_entities_message     ON entities(message_id);
-- Prevents the same entity value being extracted twice from the same message.
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_dedup ON entities(entity_type, value, message_id);


-- ---------------------------------------------------------------------------
-- Relationships — directed edges between entities
--
-- relationship_type vocabulary (open):
--   resolves_to, communicates_with, owns, attributed_to,
--   hosts, drops, related_to, parent_of, sibling_of
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS relationships (
    id                  TEXT PRIMARY KEY,       -- UUID v7
    source_entity_id    TEXT NOT NULL REFERENCES entities(id),
    target_entity_id    TEXT NOT NULL REFERENCES entities(id),
    relationship_type   TEXT NOT NULL,
    confidence          REAL CHECK (confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0)),
    message_id          TEXT NOT NULL REFERENCES messages(id),
    observed_at         TEXT NOT NULL,          -- ISO8601
    ext                 TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_rel_source   ON relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_target   ON relationships(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_type     ON relationships(relationship_type);
CREATE INDEX IF NOT EXISTS idx_rel_message  ON relationships(message_id);


-- ---------------------------------------------------------------------------
-- Sources — where intelligence came from
--
-- source_type vocabulary: feed | analyst | tool | report | vendor | partner
-- reliability follows the NATO admiralty scale (modified):
--   confirmed | usually_reliable | fairly_reliable |
--   not_usually_reliable | unreliable | unknown
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,           -- UUID v7
    name            TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    url             TEXT,
    reliability     TEXT NOT NULL DEFAULT 'unknown',
    tlp_level       TEXT CHECK (tlp_level IN ('white', 'green', 'amber', 'red') OR tlp_level IS NULL),
    ext             TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_name ON sources(name);


-- ---------------------------------------------------------------------------
-- Entity ↔ Source junction
-- An entity can be corroborated by multiple sources.
-- This table is also the corroboration signal used by the promotion pipeline:
-- when source_count for an entity reaches the configured threshold,
-- the entity (and its originating message) becomes a promotion candidate.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS entity_sources (
    entity_id   TEXT NOT NULL REFERENCES entities(id),
    source_id   TEXT NOT NULL REFERENCES sources(id),
    observed_at TEXT NOT NULL,                  -- ISO8601
    PRIMARY KEY (entity_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_sources_source ON entity_sources(source_id);
