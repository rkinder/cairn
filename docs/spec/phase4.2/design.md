# Phase 4.2 — IT Domain Expansion: Design

## Architecture Overview

```
Phase 4.2 IT Domain Expansion
├── cairn/db/schema/
│   └── topic_common.sql              # FR-2: Shared messages table DDL
├── cairn/db/
│   └── init.py                       # FR-1: Extend SCHEMA_FILES + _TOPIC_METADATA
├── cairn/nlp/
│   └── entity_extractor.py           # FR-3–7: Domain field on Entity + new patterns
├── cairn/vault/
│   └── writer.py                     # FR-8: Domain-aware subdirectory routing
├── .env.example                       # FR-9: Document CAIRN_GITLAB_METHODOLOGY_DIR
└── docs/agent-skill/references/
    └── message-format.md             # FR-11: New slugs + playbook paths
```

All changes are strictly additive. No existing files are deleted; no existing
function signatures change in a breaking way. Existing tests run unmodified.

---

## Core Components

### topic_common.sql (FR-2)

**Purpose:** Single DDL source of truth for the `messages` table shared by all
topic databases (new and, going forward, existing).

**Schema:**

```sql
-- topic_common.sql
-- Shared messages table for all Cairn topic databases.
-- Mirrors the messages table in osint.sql exactly.
-- Schema version: 1

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Schema metadata — values are parameterised at init time via SCHEMA_FILES.
CREATE TABLE IF NOT EXISTS _schema_meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

-- Messages table — identical across all topic databases.
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    thread_id       TEXT,
    message_type    TEXT NOT NULL,
    in_reply_to     TEXT REFERENCES messages(id),
    confidence      REAL CHECK (confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0)),
    tlp_level       TEXT CHECK (tlp_level IN ('white','green','amber','red') OR tlp_level IS NULL),
    promote         TEXT NOT NULL DEFAULT 'none'
                        CHECK (promote IN ('none','candidate','promoted','rejected')),
    tags            TEXT NOT NULL DEFAULT '[]',
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
```

**Notes:**
- The `_schema_meta` `INSERT` rows are omitted here; `init.py` inserts
  `schema_version` and `domain` programmatically for each new database so
  this file needs no per-domain edits.
- `osint.sql` and `vulnerabilities.sql` are left unchanged (no migration
  needed — those databases already exist in production).

---

### init.py Extension (FR-1)

**Purpose:** Register the five new topic databases so `init_all()` creates
their `.db` files and registers them in `index.db` automatically.

**Changes — extend the two module-level dicts:**

```python
# cairn/db/init.py  (additions only — existing entries unchanged)

SCHEMA_FILES: dict[str, str] = {
    "index":          "index.sql",
    "osint":          "osint.sql",
    "vulnerabilities":"vulnerabilities.sql",
    # Phase 4.2 — IT domain expansion
    "aws":            "topic_common.sql",
    "azure":          "topic_common.sql",
    "networking":     "topic_common.sql",
    "systems":        "topic_common.sql",
    "pam":            "topic_common.sql",
}

_TOPIC_METADATA: dict[str, dict] = {
    "osint": { ... },           # unchanged
    "vulnerabilities": { ... }, # unchanged
    # Phase 4.2
    "aws": {
        "display_name": "AWS",
        "description":  "AWS infrastructure, IAM, security, and architecture findings.",
        "domain_tags":  '["aws", "cloud", "iam"]',
    },
    "azure": {
        "display_name": "Azure",
        "description":  "Azure infrastructure, Entra ID, networking, and PIM findings.",
        "domain_tags":  '["azure", "cloud", "entra"]',
    },
    "networking": {
        "display_name": "Networking",
        "description":  "Network infrastructure, firewalls, segmentation, and topology.",
        "domain_tags":  '["network", "firewall", "vlan"]',
    },
    "systems": {
        "display_name": "Systems",
        "description":  "Windows/Linux administration, GPO, patching, and server config.",
        "domain_tags":  '["windows", "linux", "gpo", "patching"]',
    },
    "pam": {
        "display_name": "Privileged Access",
        "description":  "CyberArk, privileged sessions, vault management, and EPM.",
        "domain_tags":  '["cyberark", "pam", "privileged-access"]',
    },
}
```

**`_register_topic_dbs` also needs a per-slug `_schema_meta` insert:**

```python
# After creating each new DB file, insert domain-specific metadata:
async with aiosqlite.connect(db_path) as db:
    await db.execute(
        "INSERT OR IGNORE INTO _schema_meta (key, value) VALUES (?, ?)",
        ("schema_version", "1"),
    )
    await db.execute(
        "INSERT OR IGNORE INTO _schema_meta (key, value) VALUES (?, ?)",
        ("domain", domain),
    )
    await db.commit()
```

**Edge Cases:**
- `init_all()` is idempotent (`CREATE TABLE IF NOT EXISTS`, `INSERT OR IGNORE`)
- Existing `osint.db` and `vulnerabilities.db` are opened for version-check
  only; their data is untouched

---

### entity_extractor.py Extension (FR-3–7)

**Purpose:** Add domain-aware IT entity patterns while preserving all
existing cybersecurity patterns unchanged.

**Entity dataclass — add optional `domain` field (backward-compatible):**

```python
@dataclasses.dataclass(frozen=True)
class Entity:
    type:   str
    value:  str
    span:   tuple[int, int]
    domain: str | None = None   # NEW — None for cybersecurity entities
```

**New compiled patterns:**

```python
# --- AWS (FR-3) ---

# ARN: arn:aws:<service>:<region>:<account-id>:<resource>
_RE_AWS_ARN = re.compile(
    r"\barn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:[^\s\"'<>]+"
)

# Region: e.g. us-east-1, eu-west-2, ap-southeast-1
_RE_AWS_REGION = re.compile(
    r"\b(?:us|eu|ap|sa|ca|me|af)-(?:east|west|north|south|central|northeast|southeast)-\d\b"
)

# Account ID: exactly 12 digits, requires "account" within 20 chars (context guard)
_RE_AWS_ACCOUNT_RAW = re.compile(r"\b(\d{12})\b")
_RE_AWS_ACCOUNT_CTX = re.compile(r"account", re.IGNORECASE)


# --- Azure (FR-4) ---

# Subscription UUID — full RFC 4122, requires "subscription" within 30 chars
_RE_AZURE_UUID_RAW = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)
_RE_AZURE_UUID_CTX = re.compile(r"subscription", re.IGNORECASE)

# Resource group path
_RE_AZURE_RG = re.compile(r"/resourceGroups/([a-zA-Z0-9_-]+)", re.IGNORECASE)


# --- Networking (FR-5) ---

# CIDR: validated octets + prefix length 0–32
_RE_CIDR = re.compile(
    r"\b"
    r"(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)"
    r"/(?:3[0-2]|[12]?\d)"
    r"\b"
)

# VLAN: VLAN 100, VLAN100, vlan 4094
_RE_VLAN = re.compile(r"\bVLAN\s*(\d{1,4})\b", re.IGNORECASE)


# --- PAM / CyberArk (FR-7) ---

# Safe name in message body: "Safe: AWS-Console-Access"
_RE_CYBERARK_SAFE_BODY = re.compile(r"\bSafe:\s*([a-zA-Z0-9_-]+)", re.IGNORECASE)

# Safe name in tags: "safe:AWS-Console-Access"
_RE_CYBERARK_SAFE_TAG  = re.compile(r"^safe:(.+)$", re.IGNORECASE)
```

**Context-guard helpers:**

```python
def _has_context(text: str, match_start: int, match_end: int,
                 pattern: re.Pattern, window: int = 20) -> bool:
    """Return True if *pattern* appears within *window* chars of the match."""
    lo = max(0, match_start - window)
    hi = min(len(text), match_end + window)
    return bool(pattern.search(text[lo:hi]))
```

**New extraction block inside `extract()` (appended after existing blocks):**

```python
    # --- AWS ARNs (FR-3) ---
    for m in _RE_AWS_ARN.finditer(text):
        _add("arn", m.group(), (m.start(), m.end()), domain="aws")

    # --- AWS Account IDs (FR-3, context-guarded) ---
    for m in _RE_AWS_ACCOUNT_RAW.finditer(text):
        if _has_context(text, m.start(), m.end(), _RE_AWS_ACCOUNT_CTX, window=20):
            _add("aws_account_id", m.group(), (m.start(), m.end()), domain="aws")

    # --- AWS Regions (FR-3) ---
    for m in _RE_AWS_REGION.finditer(text):
        _add("aws_region", m.group(), (m.start(), m.end()), domain="aws")

    # --- Azure Subscription IDs (FR-4, context-guarded) ---
    for m in _RE_AZURE_UUID_RAW.finditer(text):
        if _has_context(text, m.start(), m.end(), _RE_AZURE_UUID_CTX, window=30):
            _add("azure_subscription_id", m.group(), (m.start(), m.end()), domain="azure")

    # --- Azure Resource Groups (FR-4) ---
    for m in _RE_AZURE_RG.finditer(text):
        _add("azure_resource_group", m.group(1), (m.start(), m.end()), domain="azure")

    # --- CIDRs (FR-5) ---
    for m in _RE_CIDR.finditer(text):
        _add("cidr", m.group(), (m.start(), m.end()), domain="networking")

    # --- VLANs (FR-5) ---
    for m in _RE_VLAN.finditer(text):
        _add("vlan", f"VLAN {m.group(1)}", (m.start(), m.end()), domain="networking")

    # --- CyberArk Safes — body (FR-7) ---
    for m in _RE_CYBERARK_SAFE_BODY.finditer(text):
        _add("cyberark_safe", m.group(1), (m.start(), m.end()), domain="pam")

    # --- CyberArk Safes — tags (FR-7) ---
    for tag in (tags or []):
        tm = _RE_CYBERARK_SAFE_TAG.match(tag.strip())
        if tm:
            safe_name = tm.group(1).strip()
            if safe_name:
                _add("cyberark_safe", safe_name, (0, 0), domain="pam")
```

**Updated `_add` inner function to accept `domain`:**

```python
    def _add(entity_type: str, value: str, span: tuple[int, int],
             domain: str | None = None) -> None:
        key = (entity_type, value.lower())
        if key not in seen:
            seen.add(key)
            entities.append(Entity(type=entity_type, value=value,
                                   span=span, domain=domain))
```

**Systems FQDN domain tagging (FR-6):**

The `extract()` function gains an optional `topic_db` parameter. When
`topic_db="systems"`, FQDNs extracted from the body receive `domain="systems"`.
All other callers pass nothing and get the existing behaviour.

```python
def extract(
    text: str,
    *,
    tags: Sequence[str] | None = None,
    topic_db: str | None = None,      # NEW — optional domain hint
) -> list[Entity]:
    ...
    # Inside the FQDN block, after _add("fqdn", value, span):
    # Re-tag with domain if the message originated from the systems DB.
    # (The entity was already added; update it in-place via reconstruction.)
    fqdn_domain = "systems" if topic_db == "systems" else None
    _add("fqdn", value, span, domain=fqdn_domain)
```

**Edge Cases:**
- ARN regex is greedy on the resource segment; capped by whitespace and quote
  chars to prevent run-on matches
- CIDR octet validation reuses the same `25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d`
  class from `_RE_IPV4` — no chance of matching `999.0.0.0/8`
- Azure UUID context guard (30 chars) is wider than AWS account (20 chars)
  because UUIDs appear innocuously in many non-Azure contexts (log correlation
  IDs, etc.)

---

### vault/writer.py Extension (FR-8)

**Purpose:** Route vault notes into a domain subdirectory when the promoted
entity carries a `domain` hint.

**Updated `write_note()` signature (one new optional parameter):**

```python
def write_note(
    vault_root: Path,
    *,
    entity: str,
    entity_type: str,
    narrative: str,
    source_message_ids: list[str],
    confidence: float | None,
    promoted_at: str,
    tags: list[str] | None = None,
    related_links: list[str] | None = None,
    domain: str | None = None,         # NEW — e.g. "aws", "pam"
) -> str:
```

**Domain-aware path resolution (replaces the fixed `cairn_dir` calculation):**

```python
    # Determine target directory within the vault.
    if domain:
        target_dir = vault_root / _CAIRN_SUBDIR / domain
        vault_rel_prefix = f"{_CAIRN_SUBDIR}/{domain}"
    else:
        target_dir = vault_root / _CAIRN_SUBDIR
        vault_rel_prefix = _CAIRN_SUBDIR

    target_dir.mkdir(parents=True, exist_ok=True)   # creates domain subdir if needed
    safe_name = _safe_filename(entity)
    note_file  = target_dir / f"{safe_name}.md"
    vault_rel  = f"{vault_rel_prefix}/{safe_name}.md"
```

**`_entity_type_to_tag` extension:**

```python
def _entity_type_to_tag(entity_type: str) -> str:
    mapping = {
        # Existing cybersecurity types (unchanged)
        "ipv4":                "ip-address",
        "ipv6":                "ip-address",
        "fqdn":                "hostname",
        "cve":                 "vulnerability",
        "technique":           "mitre-attack",
        "actor":               "threat-actor",
        # Phase 4.2 IT domain types
        "arn":                 "aws-resource",
        "aws_account_id":      "aws-account",
        "aws_region":          "aws-region",
        "azure_subscription_id": "azure-subscription",
        "azure_resource_group":  "azure-resource-group",
        "cidr":                "network-range",
        "vlan":                "vlan",
        "cyberark_safe":       "pam-safe",
    }
    return mapping.get(entity_type, entity_type)
```

**Caller update in `cairn/api/routes/promotions.py`:**

The promotion route already calls `write_note()`. It is updated to pass
`domain=candidate.entity_domain` (a new column added to `promotion_candidates`
— see below).

**Edge Cases:**
- `mkdir(parents=True, exist_ok=True)` is unconditional; no branch on whether
  the directory already exists
- If `domain` is an unexpected value (e.g. a future slug not yet in the tag
  map), the directory is still created correctly; only the Obsidian tag
  falls back to the raw entity_type string

---

### promotion_candidates schema extension

**Purpose:** Persist the `domain` hint so the vault writer can use it at
promotion time, even if the corroboration job runs hours before the human
approves.

```sql
-- Migration: add entity_domain column to promotion_candidates in index.db
ALTER TABLE promotion_candidates
    ADD COLUMN entity_domain TEXT;  -- NULL for cybersecurity entities
```

This is a nullable `ALTER TABLE ADD COLUMN` — safe to apply to an existing
`index.db` without data loss.

---

### Configuration: Playbook ChromaDB Sync (FR-9)

**No code change required.** The existing GitLab webhook handler already walks
all `.yml` files under `CAIRN_GITLAB_METHODOLOGY_DIR`. Setting this variable
to the repository root (`.` or an empty string mapped to root) causes
`sigma/`, `methodologies/`, and `playbooks/` to all be processed in one pass.

**`.env.example` addition:**

```bash
# Set to '.' (repo root) to sync sigma/, methodologies/, AND playbooks/.
# Default 'methodologies' covers cybersecurity playbooks only.
CAIRN_GITLAB_METHODOLOGY_DIR=.
```

**Sigma validation is unaffected:** The existing condition
`if path.startswith("sigma/")` gates validation; `playbooks/` files pass
through without Sigma schema checks.

---

### Agent Skill Documentation (FR-11)

**Files to update:**
- `docs/agent-skill/references/message-format.md` (canonical)
- `~/.claude/skills/cairn/references/message-format.md` (local install)

**Changes:**
1. Extend the `topic_db` valid values table to include the five new slugs with
   domain descriptions
2. Add a `playbooks/{domain}/` row to the `POST /methodologies` path examples
3. Reinforce the "query `/health` dynamically" note with an explicit code example

---

## Integration Points

### With cairn/jobs/corroboration.py
```python
# Pass topic_db when calling extract() so systems FQDNs are tagged correctly.
entities = extract(message.body, tags=message.tags, topic_db=message.topic_db)

# Persist domain on the candidate row when creating a new candidate.
await db.execute(
    "INSERT INTO promotion_candidates (..., entity_domain) VALUES (..., :domain)",
    {..., "domain": entity.domain},
)
```

### With cairn/api/routes/promotions.py
```python
# Pass domain through to write_note().
vault_path = write_note(
    vault_root,
    entity=candidate.entity,
    entity_type=candidate.entity_type,
    domain=candidate.entity_domain,   # new
    ...
)
```

---

## Error Handling Strategy

### Error Categories
1. **`init_db` failure on new slug**: `KeyError` if slug missing from
   `SCHEMA_FILES` — will not occur after FR-1 changes; existing guard unchanged
2. **Entity pattern false positive**: Logged at DEBUG; promotion candidate
   created; human reviewer dismisses in UI — no crash path
3. **Vault subdirectory creation failure**: `OSError` from `mkdir()` is
   propagated to the promotion route, which returns `500` and logs the error;
   the `promotion_candidates` row remains `pending_review` so the human can
   retry
4. **Migration `ALTER TABLE` on already-migrated DB**: `ALTER TABLE ADD COLUMN`
   on an existing column raises `OperationalError: duplicate column name` —
   wrap in a try/except or use the migration runner's idempotency guard

---

## Testing Strategy

### Unit Tests (25+ tests)

```python
# tests/test_entity_extractor_it.py
describe("AWS entity extraction", () => {
    test("extracts ARN from message body")
    test("extracts AWS region code")
    test("extracts account ID with 'account' context present")
    test("suppresses 12-digit number without 'account' context")
    test("suppresses 12-digit number 25 chars from 'account'")
})

describe("Azure entity extraction", () => {
    test("extracts subscription UUID with context")
    test("suppresses UUID without 'subscription' context")
    test("extracts resource group path")
})

describe("Networking entity extraction", () => {
    test("extracts valid CIDR /16")
    test("extracts valid CIDR /32")
    test("suppresses invalid octet CIDR (999.0.0.0/8)")
    test("extracts VLAN with space (VLAN 100)")
    test("extracts VLAN without space (VLAN100)")
    test("extracts VLAN case-insensitive (vlan 200)")
})

describe("PAM entity extraction", () => {
    test("extracts CyberArk safe from body")
    test("extracts CyberArk safe from tags")
})

describe("Systems FQDN tagging", () => {
    test("tags FQDN with domain=systems when topic_db=systems")
    test("does not tag FQDN with domain when topic_db=osint")
})

describe("Backward compatibility", () => {
    test("existing IPv4 extraction unchanged")
    test("existing CVE extraction unchanged")
    test("existing actor extraction unchanged")
    test("Entity without domain field defaults to None")
    test("callers that ignore domain field are unaffected")
})

# tests/test_vault_writer_it.py
describe("Domain-aware vault routing", () => {
    test("writes AWS entity to cairn/aws/<entity>.md")
    test("writes PAM entity to cairn/pam/<entity>.md")
    test("creates domain subdirectory if it does not exist")
    test("cybersecurity entity still writes to cairn/<entity>.md")
    test("updates existing note in domain subdirectory")
})

# tests/test_db_init_it.py
describe("IT domain database init", () => {
    test("init_all creates aws.db with correct schema")
    test("init_all creates all five new databases")
    test("GET /health returns all seven slugs after init")
    test("init_all is idempotent on second call")
})
```

### Integration Tests (10+ tests)

```python
describe("Phase 4.2 end-to-end", () => {
    test("post to aws → query messages → message returned")
    test("post to pam → corroboration job → candidate created with domain=pam")
    test("promote PAM candidate → vault note at cairn/pam/<safe>.md")
    test("promote AWS candidate → vault note at cairn/aws/<arn>.md")
    test("post to osint → Phase 4.1 behaviour unchanged")
    test("corroboration on cybersecurity entity → note at cairn/<entity>.md")
    test("GET /health includes all seven topic DB slugs")
    test("migration adds entity_domain column without data loss")
    test("playbook YAML accepted by POST /methodologies (no Sigma error)")
    test("VLAN entity in networking DB produces promotion candidate")
})
```

### Manual Tests
1. `docker compose exec cairn-api cairn-admin init-db` → five new `.db` files
   appear in the data volume
2. Post a message with `topic_db: aws` → appears in UI with AWS database badge
3. Post a message containing `arn:aws:iam::123456789012:role/AdminRole` → after
   corroboration job runs, candidate appears in Promotion Queue
4. Promote the candidate → vault note at `vault/cairn/aws/arn_aws_iam__...md`

---

## Migration Path

### Phase 1: MVP — Databases Active (Days 1–2)
**Goal:** All five databases live; agents can post and query immediately.

1. Create `cairn/db/schema/topic_common.sql` (2h)
2. Extend `SCHEMA_FILES` and `_TOPIC_METADATA` in `init.py` (1h)
3. Add `_schema_meta` insert logic for parameterised domain/version (1h)
4. Run `cairn-admin init-db` in dev; smoke-test all seven slugs (1h)
5. Unit tests for init (1h)

**Deliverables:** Seven topic databases active; `GET /health` returns all slugs

### Phase 2: Enhanced — Extraction and Vault Routing (Days 3–5)
**Goal:** IT entities extracted, promoted to correct vault directories.

1. Add `domain` field to `Entity` dataclass (0.5h)
2. Implement seven new pattern groups + `_has_context` helper (2h)
3. Add `topic_db` parameter to `extract()`; tag systems FQDNs (1h)
4. Add `entity_domain` column migration for `promotion_candidates` (0.5h)
5. Update `write_note()` with domain-aware routing (1h)
6. Wire `domain` through corroboration job and promotions route (1h)
7. Unit and integration tests (2h)

**Deliverables:** IT entities in promotion queue; vault notes in domain subdirs

### Phase 3: Full Feature — Playbooks and Documentation (Days 6–7)
**Goal:** Playbooks indexed in ChromaDB; skill docs updated; fully tested.

1. Update `.env.example` with `CAIRN_GITLAB_METHODOLOGY_DIR=.` (0.5h)
2. Submit sample playbook via `POST /methodologies`; verify ChromaDB entry (1h)
3. Update `docs/agent-skill/references/message-format.md` (0.5h)
4. Copy updated skill doc to `~/.claude/skills/cairn/references/` (0.25h)
5. End-to-end integration test pass (1h)
6. Documentation review (0.75h)

**Deliverables:** Full feature complete; 35+ tests passing; docs updated

---

## Success Metrics

### MVP (Phase 1)
- ✅ Five new `.db` files created by `cairn-admin init-db`
- ✅ `GET /health` returns seven slugs
- ✅ Agents can `POST /messages?db=aws` and retrieve messages back
- ✅ 5+ unit tests covering init logic

### Enhanced (Phase 2)
- ✅ AWS ARN, account ID, region extracted from test messages
- ✅ Azure subscription ID and resource group extracted
- ✅ CIDR, VLAN extracted; invalid CIDRs suppressed
- ✅ CyberArk Safe extracted from body and tags
- ✅ Vault notes appear in correct domain subdirectory
- ✅ 25+ unit tests passing

### Full Feature (Phase 3)
- ✅ Playbook YAML indexed in ChromaDB after webhook push
- ✅ `GET /methodologies/search` returns playbook results
- ✅ Agent skill docs reflect seven topic DB slugs
- ✅ End-to-end: post → extract → promote → vault note in correct directory
- ✅ 35+ tests passing; all Phase 4.1 tests still green
