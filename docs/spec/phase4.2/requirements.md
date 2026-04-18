# Phase 4.2 — IT Domain Expansion: Requirements

## Overview

Phase 4.2 expands Cairn's blackboard from a cybersecurity-only system into a
full-spectrum IT knowledge platform. Five new topic databases — `aws`, `azure`,
`networking`, `systems`, and `pam` — are added alongside new entity extraction
patterns, domain-aware vault routing, and an IT playbook format in the GitLab
methodology repository. All existing functionality (topic databases, entity
extraction, promotion pipeline, agent skill) is preserved without modification.

The expansion is additive: new databases share a common schema with existing
ones, new entity patterns are appended to the extractor without touching
existing patterns, and playbooks follow an existing submission pathway that
already supports non-Sigma files.

## Problem Statement

Cairn's topic databases and entity extractor are scoped to cybersecurity domains
(`osint`, `vulnerabilities`). The team operates daily across AWS, Azure,
network infrastructure, systems administration, and privileged access
management. Agents posting findings in these domains must shoehorn them into
`osint`, degrading search accuracy and cluttering the message feed with
off-domain content.

**Pain Points:**
- Infrastructure findings (IAM roles, firewall rules, CyberArk safe configs)
  routed to `osint` produce false matches in cybersecurity queries
- The entity extractor ignores AWS ARNs, Azure subscription IDs, CIDR blocks,
  and CyberArk Safe names — none of these reach the promotion pipeline
- IT operational playbooks (CyberArk PSM setup, WSUS patching, GPO baselines)
  have no versioned home alongside detection methodologies
- Agents querying `/health` see only `osint` and `vulnerabilities`, giving no
  signal that IT domain knowledge is tracked anywhere in the system

---

## User Stories (EARS Format)

### US-1: Post Finding to an IT Domain Database
**WHEN** an agent posts a message with `topic_db` set to `aws`, `azure`,
`networking`, `systems`, or `pam`  
**THEN** the system **SHALL** accept, parse, and store the message in the
corresponding domain database  
**SO THAT** infrastructure findings are scoped to their domain and do not
appear in cybersecurity queries

**Acceptance Criteria:**
- Messages with each of the five new slugs return `201 Created` with the
  correct `topic_db` in the response body
- Messages are queryable via `GET /messages?db=aws` (etc.) and appear in
  `GET /messages` cross-domain results
- Posting to `osint` or `vulnerabilities` is unaffected

---

### US-2: Discover IT Domain Databases Dynamically
**WHEN** an agent or UI client calls `GET /health`  
**THEN** the system **SHALL** include the five new domain slugs in the
`topic_dbs` array  
**SO THAT** agents enumerate valid `topic_db` values without hardcoding them

**Acceptance Criteria:**
- `GET /health` response includes `aws`, `azure`, `networking`, `systems`,
  and `pam` alongside existing slugs
- The UI database filter dropdown populates all seven databases after login

---

### US-3: Extract Domain-Specific Entities During Promotion
**WHEN** the corroboration job processes messages from IT domain databases  
**THEN** the system **SHALL** extract domain-specific entities (AWS ARNs,
Azure subscription IDs, CIDR blocks, VLAN IDs, CyberArk Safe names) using
the expanded entity extractor  
**SO THAT** infrastructure entities appear in the promotion candidate queue
with the same workflow as cybersecurity entities

**Acceptance Criteria:**
- A message body containing `arn:aws:iam::123456789012:role/AdminRole`
  produces an extracted entity of type `arn`
- A message body containing `10.100.0.0/16` produces an entity of type `cidr`
- A message body containing `Safe: AWS-Console-Access` produces an entity of
  type `cyberark_safe`
- Existing entity patterns (IPv4, CVE, T-IDs, actor) produce identical results

---

### US-4: Promote Entity to Domain-Scoped Vault Directory
**WHEN** a promotion candidate with a domain hint is approved via
`POST /promotions/{id}/promote`  
**IF** the entity's domain is one of `aws`, `azure`, `networking`, `systems`,
or `pam`  
**THEN** the system **SHALL** write the vault note to
`vault/cairn/{domain}/{entity}.md`  
**SO THAT** vault notes are organized by IT domain and navigable without
mixing infrastructure knowledge into the cybersecurity directory

**Acceptance Criteria:**
- An AWS ARN entity produces a note at `vault/cairn/aws/<entity-slug>.md`
- A CyberArk Safe entity produces a note at `vault/cairn/pam/<entity-slug>.md`
- Cybersecurity entities (CVEs, actors, IPs) continue writing to
  `vault/cairn/<entity>.md` (no subdirectory)
- Domain subdirectory is created if it does not exist

---

### US-5: Submit an IT Playbook to the Methodology Repository
**WHEN** an agent calls `POST /methodologies` with a path under
`playbooks/{domain}/`  
**THEN** the system **SHALL** accept and commit the playbook YAML to GitLab
without applying Sigma validation  
**SO THAT** IT operational procedures are versioned alongside detection
methodologies and discoverable via semantic search

**Acceptance Criteria:**
- `POST /methodologies` with `path: "playbooks/pam/cyberark-psm-rdp.yml"`
  returns `201 Created` and announces to the blackboard
- Sigma schema validation is skipped for paths not under `sigma/`
  (existing behaviour — no code change required)
- The committed file is retrievable via the GitLab API at the returned SHA

---

### US-6: Search IT Playbooks via Semantic Query
**WHEN** an agent calls `GET /methodologies/search` with a query describing
an IT operational task  
**THEN** the system **SHALL** return ranked playbook results from ChromaDB  
**SO THAT** agents discover existing IT playbooks before creating duplicate
procedures

**Acceptance Criteria:**
- A query of `"CyberArk PSM RDP session recording"` returns the
  `playbooks/pam/cyberark-psm-rdp.yml` entry with score > 0.7
- Playbook results appear alongside Sigma/methodology results in the same
  ranked list
- Results include `gitlab_path` and `commit_sha` for retrieval

---

### US-7: No Regression on Existing Cybersecurity Functionality
**WHILE** IT domain databases are active  
**WHEN** an agent posts to `osint` or `vulnerabilities`, or the corroboration
job processes cybersecurity entities  
**THEN** the system **SHALL** behave identically to Phase 4.1  
**SO THAT** existing agents and workflows require no changes

**Acceptance Criteria:**
- All existing Phase 4.1 integration tests pass without modification
- Messages to `osint` and `vulnerabilities` route, extract, and promote as before
- IPv4, IPv6, FQDN, CVE, T-ID, and actor entity patterns produce unchanged results

---

## Functional Requirements (EARS Format)

### FR-1: IT Domain Database Creation
**WHEN** `cairn-admin init-db` is executed  
**THEN** the system **SHALL** create five new SQLite databases: `aws.db`,
`azure.db`, `networking.db`, `systems.db`, and `pam.db`  
**WHERE** each database is created from a single shared schema file
(`cairn/db/schema/topic_common.sql`) with the same `messages` table
structure as `osint.db`

**Implementation:**
- `SCHEMA_FILES` dict in `cairn/db/init.py` maps each new slug to
  `topic_common.sql`
- `init_all()` iterates over the extended map
- Satisfies US-1, US-2

---

### FR-2: Shared Topic Database Schema
**WHEN** any new topic database is initialized  
**THEN** the system **SHALL** apply `cairn/db/schema/topic_common.sql`  
**WHERE** the schema is identical to the existing `messages` table in `osint.db`,
including all indexes, triggers, and the `ext` JSON column

**Implementation:**
- `topic_common.sql` is a hand-authored DDL text file whose `messages` table
  definition mirrors `osint.db` exactly; it is not a database and contains no
  data
- `osint.db` initialization is updated to reference `topic_common.sql` so the
  schema has a single source of truth going forward
- No data migration; existing `osint.db` content is untouched
- Satisfies FR-1

---

### FR-3: AWS Entity Extraction
**WHEN** the entity extractor processes message body or tags from any topic
database  
**THEN** the system **SHALL** identify and extract entities matching AWS
resource patterns  
**WHERE** patterns cover ARNs (`arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:.*`),
12-digit account IDs in AWS context, and standard region codes
(`us-east-1`, `eu-west-2`, etc.)

**Implementation:**
- Append three new `EntityPattern` entries to the pattern list in
  `cairn/nlp/entity_extractor.py`; each carries `domain="aws"`
- Account ID pattern requires the word "account" (case-insensitive) within
  20 characters of the 12-digit match to suppress false positives
- Satisfies US-3

---

### FR-4: Azure Entity Extraction
**WHEN** the entity extractor processes a message  
**THEN** the system **SHALL** extract Azure subscription UUIDs and resource
group paths  
**WHERE** subscription UUIDs are full RFC 4122 UUIDs in Azure context and
resource group paths match `/resourceGroups/[a-zA-Z0-9_-]+`

**Implementation:**
- Two new patterns with `domain="azure"`; UUID pattern uses "subscription"
  context guard to avoid matching unrelated UUIDs
- Satisfies US-3

---

### FR-5: Networking Entity Extraction
**WHEN** the entity extractor processes a message  
**THEN** the system **SHALL** extract CIDR notation blocks and VLAN
identifiers  
**WHERE** CIDRs match `\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}\b`
and VLANs match `VLAN\s*\d{1,4}` (case-insensitive)

**Implementation:**
- Two new patterns with `domain="networking"`
- CIDR pattern validates octet ranges (0–255) and prefix length (0–32)
- Satisfies US-3

---

### FR-6: Systems Entity Extraction
**WHEN** the entity extractor processes a message from the `systems` database  
**THEN** the system **SHALL** extract fully-qualified hostnames using the
existing FQDN pattern  
**WHERE** the extracted entity carries `domain="systems"` to route vault
notes to the correct subdirectory

**Implementation:**
- Reuse existing FQDN `EntityPattern`; add a domain-aware variant that
  fires when topic_db is `systems`
- No new regex required
- Satisfies US-3

---

### FR-7: PAM Entity Extraction
**WHEN** the entity extractor processes a message  
**THEN** the system **SHALL** extract CyberArk Safe names  
**WHERE** patterns match `Safe:\s*[a-zA-Z0-9_-]+` in the message body or
`safe:` prefixed tag values

**Implementation:**
- One new pattern with `domain="pam"`
- Satisfies US-3

---

### FR-8: Domain-Aware Vault Note Routing
**WHEN** the vault writer produces a note for a promoted entity  
**IF** the entity carries a `domain` field from the extractor  
**THEN** the system **SHALL** write the note to
`{CAIRN_VAULT_PATH}/cairn/{domain}/{entity-slug}.md`  
**WHERE** the domain subdirectory is created if it does not already exist,
and entities without a domain field continue to write to
`{CAIRN_VAULT_PATH}/cairn/{entity-slug}.md`

**Implementation:**
- `VaultWriter.write()` checks `entity.domain`; `pathlib.Path.mkdir(parents=True,
  exist_ok=True)` before writing
- `WikilinkResolver` cache is seeded with the domain subdirectory on startup
- Satisfies US-4

---

### FR-9: Playbook Directory ChromaDB Sync
**WHEN** a GitLab push webhook is received  
**THEN** the system **SHALL** process `.yml` files under `playbooks/`
in addition to the configured `CAIRN_GITLAB_METHODOLOGY_DIR`  
**WHERE** Sigma validation is skipped for all paths not under `sigma/`
(existing behaviour preserved)

**Implementation:**
- Set `CAIRN_GITLAB_METHODOLOGY_DIR` to the repository root in `.env`
  so the webhook handler walks `sigma/`, `methodologies/`, and `playbooks/`
  in a single pass (Option A — no code change required)
- Document this configuration in `.env.example`
- Satisfies US-5, US-6

---

### FR-10: Existing Database Preservation
**WHILE** the system is running with IT domain databases active  
**THEN** the system **SHALL** route, process, and promote messages in `osint`
and `vulnerabilities` identically to Phase 4.1  
**WHERE** no existing schema file, migration, entity pattern, or route handler
is modified

**Implementation:**
- All changes are strictly additive (new files, new list entries, new
  conditions)
- Satisfies US-7

---

### FR-11: Agent Skill Documentation
**WHEN** an agent loads the Cairn skill  
**THEN** the system **SHALL** present documentation that includes the five
new topic database slugs as valid `topic_db` values and `playbooks/{domain}/`
as a valid path prefix for `POST /methodologies`  
**WHERE** documentation notes that agents SHOULD query `GET /health` to
discover slugs dynamically rather than relying on the hardcoded list

**Implementation:**
- Update `~/.claude/skills/cairn/references/message-format.md` and
  `docs/agent-skill/references/message-format.md` (canonical copy)
- Satisfies US-1, US-5

---

## Non-Functional Requirements (EARS Format)

### NFR-1: Entity Extraction Performance
**WHEN** the entity extractor runs against a single message body  
**THEN** the system **SHALL** complete extraction in under 100 ms  
**WHERE** measured at the 95th percentile on messages up to 10 KB

### NFR-2: Backward Compatibility
**WHEN** an existing agent built against Phase 4.1 posts to `osint` or
`vulnerabilities`  
**THEN** the system **SHALL** produce identical API responses and side-effects
to Phase 4.1  
**WHERE** no breaking changes are introduced to any existing endpoint contract
or response shape

### NFR-3: Schema Maintainability
**WHEN** a developer adds a future topic database  
**THEN** the system **SHALL** require only a new entry in `SCHEMA_FILES` and
one `cairn-admin db register` call  
**WHERE** `topic_common.sql` is the single source of truth for the messages
table; no schema duplication across database files

### NFR-4: Additive-Only Entity Pattern Safety
**WHEN** new entity patterns are appended to the extractor  
**THEN** the system **SHALL** leave existing pattern definitions, their order,
and their output fields unchanged  
**WHERE** new patterns carry a `domain` field that existing patterns do not,
and callers that ignore `domain` are unaffected

### NFR-5: Vault Directory Safety
**WHEN** the vault writer attempts to create a note in a domain subdirectory
that does not yet exist  
**THEN** the system **SHALL** create the directory with `exist_ok=True` before
writing  
**WHERE** creation failure raises an exception that is caught and logged
without crashing the promotion pipeline

---

## Success Criteria

### MVP Success (Phase 1 — Databases Active)
- ✅ All five new topic databases created and registered
- ✅ `GET /health` returns all seven slugs
- ✅ Agents can post to each new database and query messages back
- ✅ UI database filter shows all seven options
- ✅ Existing Phase 4.1 tests pass unmodified

### Enhanced Success (Phase 2 — Entity Extraction and Vault Routing)
- ✅ All seven new entity pattern types extract correctly from test messages
- ✅ Promoted IT entities appear in domain subdirectories in the vault
- ✅ Domain subdirectories created automatically on first promotion
- ✅ 20+ unit tests covering new patterns and vault routing

### Full Feature Success (Phase 3 — Playbooks and Documentation)
- ✅ `playbooks/` directory synced to ChromaDB on push webhook
- ✅ IT playbook discoverable via `GET /methodologies/search`
- ✅ Agent skill docs updated with new slugs and playbook paths
- ✅ End-to-end test: post → extract → promote → vault note in correct directory
- ✅ 30+ tests total; all passing

---

## Out of Scope

### MVP (Phase 1)
- Entity extraction and vault routing (Phase 2)
- Playbook ChromaDB sync configuration (Phase 3)
- Agent skill documentation updates (Phase 3)

### All Phases
- Cross-domain entity linking (future — tag-based correlation is sufficient now)
- Domain-specific `messages` table schemas (diverging from common schema)
- Per-domain access control or RBAC (any agent may post to any topic database)
- Migration of existing messages from `osint` into domain-specific databases
- Real-time replication between topic databases
- Automatic domain inference from message content (agents specify `topic_db` explicitly)

---

## Dependencies

### Required
- **Cairn Phase 4.1** — promotion pipeline, vault writer, entity extractor, and
  corroboration job must be deployed and stable
- **`cairn-admin` CLI** — `init-db` and `db register` subcommands must support
  the new slugs (existing capability; no changes required)
- **ChromaDB** — must be reachable for playbook sync (Phase 3 only)
- **GitLab** — must be reachable for playbook submission (Phase 3 only)

### Optional
- **Obsidian vault with LiveSync** — domain subdirectories appear automatically;
  no manual vault configuration required

---

## Risks & Mitigation

### Risk 1: AWS Account ID False Positives
**Impact:** Medium  
**Probability:** High  
**Mitigation:**
- Require "account" within 20 characters of the 12-digit match
- Add a denylist of common false-positive contexts (port numbers, timestamps)
- Include suppression test cases in the entity extractor test suite

### Risk 2: Vault Domain Subdirectory Missing
**Impact:** Low  
**Probability:** Medium  
**Mitigation:**
- Use `pathlib.Path.mkdir(parents=True, exist_ok=True)` unconditionally
  before every vault write
- Add integration test that promotes an entity with a fresh (non-existent)
  domain subdirectory

### Risk 3: ChromaDB Sync Scope Expansion Causes Noise
**Impact:** Medium  
**Probability:** Low  
**Mitigation:**
- Option A (root dir config) is the recommended approach; monitor ChromaDB
  collection size after first full sync
- If noise is a problem, switch to Option B (explicit second directory config)
  at the cost of a small code change to the webhook handler

### Risk 4: Playbook YAML Format Drift
**Impact:** Low  
**Probability:** Medium  
**Mitigation:**
- Define the canonical playbook schema once in `docs/phase4.2.md` (done)
- Add a non-blocking schema hint check in `POST /methodologies` that warns
  (but does not reject) if required fields (`title`, `domain`, `steps`) are
  absent

---

## Timeline

### Phase 1: MVP — Databases Active (Days 1–2)
- Day 1: Extract `topic_common.sql`, update `init.py`, run `init-db`, register
  all five databases via `cairn-admin db register`
- Day 2: Smoke-test all seven databases end-to-end; confirm `GET /health`
  response; confirm UI filter

**Estimate:** 6 hours

### Phase 2: Enhanced — Entity Extraction and Vault Routing (Days 3–5)
- Day 3: Implement seven new `EntityPattern` entries with unit tests
- Day 4: Update `VaultWriter` for domain-aware routing; integration tests
- Day 5: End-to-end promotion test through all five new domains

**Estimate:** 8 hours

### Phase 3: Full Feature — Playbooks and Documentation (Days 6–7)
- Day 6: Configure `CAIRN_GITLAB_METHODOLOGY_DIR` for `playbooks/` sync;
  submit a sample playbook; verify ChromaDB indexing
- Day 7: Update agent skill docs; final test pass; documentation review

**Estimate:** 5 hours

**Total:** ~19 hours over 7 working days

---

