-- vulnerabilities.db — vulnerability tracking topic database
-- Stores raw messages plus structured vulnerability records, affected systems,
-- asset-vulnerability mappings, and an append-only remediation event log.
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
    ('domain',         'vulnerabilities');


-- ---------------------------------------------------------------------------
-- Messages — raw inbox (present in every topic database)
-- Identical structure across all topic databases.
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
    raw_content     TEXT NOT NULL,
    frontmatter     TEXT NOT NULL DEFAULT '{}',
    body            TEXT NOT NULL DEFAULT '',
    timestamp       TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    ext             TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_messages_agent     ON messages(agent_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread    ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_type      ON messages(message_type);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_promote   ON messages(promote);
CREATE INDEX IF NOT EXISTS idx_messages_tlp       ON messages(tlp_level);


-- ---------------------------------------------------------------------------
-- Vulnerabilities — one row per distinct vulnerability finding
--
-- cve_id and cwe_id are nullable: agents may post findings before a CVE
-- is assigned, or for internal issues that will never receive one.
--
-- severity is computed from cvss_v3_score using the NVD severity bands.
-- Stored as a generated column so queries can filter by label without
-- recalculating, and it stays consistent if the score is updated.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vulnerabilities (
    id              TEXT PRIMARY KEY,           -- UUID v7
    message_id      TEXT NOT NULL REFERENCES messages(id),
    cve_id          TEXT,                       -- CVE-YYYY-NNNNN; nullable
    cwe_id          TEXT,                       -- CWE-NNN; nullable
    title           TEXT NOT NULL,
    cvss_v3_score   REAL CHECK (cvss_v3_score IS NULL OR (cvss_v3_score BETWEEN 0.0 AND 10.0)),
    cvss_v3_vector  TEXT,                       -- e.g. CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H
    severity        TEXT GENERATED ALWAYS AS (
        CASE
            WHEN cvss_v3_score IS NULL   THEN 'unknown'
            WHEN cvss_v3_score >= 9.0    THEN 'critical'
            WHEN cvss_v3_score >= 7.0    THEN 'high'
            WHEN cvss_v3_score >= 4.0    THEN 'medium'
            WHEN cvss_v3_score >= 0.1    THEN 'low'
            ELSE                              'informational'
        END
    ) STORED,
    published_at    TEXT,                       -- ISO8601; from NVD or vendor advisory
    modified_at     TEXT,                       -- ISO8601
    ext             TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_vuln_cve      ON vulnerabilities(cve_id);
CREATE INDEX IF NOT EXISTS idx_vuln_severity ON vulnerabilities(severity);
CREATE INDEX IF NOT EXISTS idx_vuln_score    ON vulnerabilities(cvss_v3_score);
CREATE INDEX IF NOT EXISTS idx_vuln_message  ON vulnerabilities(message_id);


-- ---------------------------------------------------------------------------
-- Affected systems — which products/versions are vulnerable
--
-- cpe follows CPE 2.3 URI binding where available.
-- version_affected is a free-form range string (e.g. "< 2.4.1", "all")
-- rather than a structured semver range, to accommodate vendor advisories
-- that don't follow semver.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS affected_systems (
    id                  TEXT PRIMARY KEY,       -- UUID v7
    vulnerability_id    TEXT NOT NULL REFERENCES vulnerabilities(id),
    vendor              TEXT,
    product             TEXT,
    version_affected    TEXT,                   -- free-form range or 'all'
    cpe                 TEXT,                   -- CPE 2.3 URI
    ext                 TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_affected_vuln ON affected_systems(vulnerability_id);
CREATE INDEX IF NOT EXISTS idx_affected_cpe  ON affected_systems(cpe);


-- ---------------------------------------------------------------------------
-- Asset vulnerabilities — which known assets are affected
--
-- asset_ref is intentionally a string rather than a foreign key into
-- network.db, because SQLite cannot enforce cross-file foreign keys.
-- asset_ref_type tells the application how to interpret the value:
--   hostname   — bare hostname or FQDN
--   ip         — IPv4 or IPv6 address
--   db_ref     — UUID pointing to an asset row in network.db
--
-- status tracks the current remediation state. Full history is in
-- remediation_events (append-only). Status here is a denormalized
-- convenience for fast filtering; it must be kept in sync by the
-- ingest pipeline when processing remediation_event writes.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS asset_vulnerabilities (
    id                  TEXT PRIMARY KEY,       -- UUID v7
    vulnerability_id    TEXT NOT NULL REFERENCES vulnerabilities(id),
    asset_ref           TEXT NOT NULL,
    asset_ref_type      TEXT NOT NULL CHECK (asset_ref_type IN ('hostname', 'ip', 'db_ref')),
    status              TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN (
                                'open', 'in_progress', 'mitigated',
                                'resolved', 'accepted_risk', 'wont_fix'
                            )),
    verified_at         TEXT,                   -- ISO8601; when the finding was confirmed on this asset
    scanner_source      TEXT,                   -- tool or feed that identified this exposure
    message_id          TEXT REFERENCES messages(id),
    ext                 TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_asvuln_vuln   ON asset_vulnerabilities(vulnerability_id);
CREATE INDEX IF NOT EXISTS idx_asvuln_asset  ON asset_vulnerabilities(asset_ref);
CREATE INDEX IF NOT EXISTS idx_asvuln_status ON asset_vulnerabilities(status);


-- ---------------------------------------------------------------------------
-- Remediation events — append-only audit log of status changes and notes
--
-- Never update or delete rows here. Every state transition, assignment,
-- due-date change, and analyst note is a new row. The current state of
-- an asset_vulnerability is the latest row for that asset_vuln_id.
--
-- event_type vocabulary:
--   status_change | note | assignment | due_date_set | verification
--
-- actor is an agent_id or a human username — whatever identity token
-- the API authenticated the request with.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS remediation_events (
    id              TEXT PRIMARY KEY,           -- UUID v7
    asset_vuln_id   TEXT NOT NULL REFERENCES asset_vulnerabilities(id),
    event_type      TEXT NOT NULL,
    from_status     TEXT,                       -- null for notes/assignments
    to_status       TEXT,                       -- null for notes/assignments
    actor           TEXT NOT NULL,              -- agent_id or human username
    notes           TEXT,
    due_date        TEXT,                       -- ISO8601; set when event_type = due_date_set
    message_id      TEXT REFERENCES messages(id),
    occurred_at     TEXT NOT NULL,              -- ISO8601
    ext             TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_remevent_asset_vuln ON remediation_events(asset_vuln_id);
CREATE INDEX IF NOT EXISTS idx_remevent_actor      ON remediation_events(actor);
CREATE INDEX IF NOT EXISTS idx_remevent_type       ON remediation_events(event_type);
CREATE INDEX IF NOT EXISTS idx_remevent_occurred   ON remediation_events(occurred_at);
