-- index.db — routing registry and cross-domain message index
-- All agents hit this database first to discover topic databases
-- and to perform cross-domain queries without touching topic DBs directly.
--
-- Schema version: 2
-- Migration strategy: bump _schema_meta 'schema_version' and add
--   a corresponding migration in cairn/db/migrations/.
-- v1 → v2: added methodology_executions table (Phase 3).

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
    ('schema_version', '2'),
    ('domain',         'index');


-- ---------------------------------------------------------------------------
-- Topic database registry
-- Every topic database (osint.db, vulnerabilities.db, …) registers here.
-- Agents fetch this table on startup to discover where to route writes/reads.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS topic_databases (
    id              TEXT PRIMARY KEY,           -- UUID v7
    name            TEXT NOT NULL UNIQUE,       -- slug used in API: 'osint', 'vulnerabilities'
    display_name    TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    db_path         TEXT NOT NULL,              -- path to .db file, relative to CAIRN_DATA_DIR
    schema_version  INTEGER NOT NULL DEFAULT 1,
    domain_tags     TEXT NOT NULL DEFAULT '[]', -- JSON array: ['threat-intel', 'ioc']
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,              -- ISO8601
    updated_at      TEXT NOT NULL,              -- ISO8601
    ext             TEXT NOT NULL DEFAULT '{}'  -- JSON extension point
);


-- ---------------------------------------------------------------------------
-- Agent registry
-- Tracks known agents, their API key hashes, and what they are allowed to do.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,           -- agent_id as used in message frontmatter
    display_name    TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    api_key_hash    TEXT NOT NULL,              -- bcrypt hash of the issued API key
    capabilities    TEXT NOT NULL DEFAULT '[]', -- JSON array: ['osint', 'vuln-scan']
    allowed_dbs     TEXT NOT NULL DEFAULT '[]', -- JSON array of db names; empty = all
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    last_seen_at    TEXT,                       -- updated on each authenticated request
    ext             TEXT NOT NULL DEFAULT '{}'
);


-- ---------------------------------------------------------------------------
-- Thread registry
-- Threads can span multiple topic databases.
-- Which topic DBs participate is derived at query time from message_index —
-- no denormalized topic_dbs list is maintained here.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS threads (
    id          TEXT PRIMARY KEY,               -- thread_id as used in message frontmatter
    title       TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active', -- active | closed | archived
    tags        TEXT NOT NULL DEFAULT '[]',     -- JSON array
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    ext         TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);


-- ---------------------------------------------------------------------------
-- Cross-domain message index
-- Written by the ingest pipeline immediately after writing to the topic DB.
-- Contains only envelope fields — never body or raw_content.
-- Use this for cross-domain queries; fetch full records from topic DBs.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS message_index (
    id              TEXT PRIMARY KEY,                           -- same UUID as messages.id in topic DB
    topic_db_id     TEXT NOT NULL REFERENCES topic_databases(id),
    agent_id        TEXT NOT NULL,
    thread_id       TEXT,
    message_type    TEXT NOT NULL,
    tags            TEXT NOT NULL DEFAULT '[]',                 -- JSON array
    confidence      REAL CHECK (confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0)),
    tlp_level       TEXT CHECK (tlp_level IN ('white', 'green', 'amber', 'red') OR tlp_level IS NULL),
    promote         TEXT NOT NULL DEFAULT 'none'
                        CHECK (promote IN ('none', 'candidate', 'promoted', 'rejected')),
    timestamp       TEXT NOT NULL,                              -- ISO8601, agent-supplied
    ingested_at     TEXT NOT NULL                               -- ISO8601, server-set
    -- No ext column: this is a projection of messages, not an independent entity.
    -- Add fields here only when a corresponding field is added to messages.
);

CREATE INDEX IF NOT EXISTS idx_midx_topic_db   ON message_index(topic_db_id);
CREATE INDEX IF NOT EXISTS idx_midx_agent      ON message_index(agent_id);
CREATE INDEX IF NOT EXISTS idx_midx_thread     ON message_index(thread_id);
CREATE INDEX IF NOT EXISTS idx_midx_type       ON message_index(message_type);
CREATE INDEX IF NOT EXISTS idx_midx_timestamp  ON message_index(timestamp);
CREATE INDEX IF NOT EXISTS idx_midx_promote    ON message_index(promote);
CREATE INDEX IF NOT EXISTS idx_midx_tlp        ON message_index(tlp_level);


-- ---------------------------------------------------------------------------
-- Methodology execution records (Phase 3)
-- Tracks each time an agent executes a methodology from the GitLab repo.
--
-- Design constraint: methodology text NEVER lives here. Only the GitLab path
-- and commit SHA are stored so the exact version run can always be retrieved.
-- Execution history and outcomes live here; content lives in GitLab.
-- ---------------------------------------------------------------------------

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
