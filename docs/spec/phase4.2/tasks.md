# Phase 4.2 — IT Domain Expansion: Tasks

## Overview

Implementation tasks for Phase 4.2 IT Domain Expansion, organized into three
phases. All work is strictly additive — no existing files are deleted and no
existing function signatures change in a breaking way.

**Total Estimate:** ~19 hours over 7 working days

---

## Phase 1: MVP — Databases Active (Days 1–2) — 6 hours

### Task 1.1: Create `topic_common.sql`
**Estimate:** 2 hours  
**Priority:** Critical  
**Implements:** FR-1, FR-2

**Objectives:**
- [ ] Create `cairn/db/schema/topic_common.sql`
- [ ] Mirror the `messages` table definition from `osint.sql` exactly
- [ ] Include `_schema_meta` table (values inserted programmatically, not here)
- [ ] Include all six indexes from `osint.sql`
- [ ] Verify the file is valid SQLite DDL (parseable by `sqlite3` CLI)

**Files to create:**
- `cairn/db/schema/topic_common.sql`

**Implementation Steps:**
1. Open `cairn/db/schema/osint.sql` and copy the `messages` table DDL
2. Add `PRAGMA journal_mode = WAL` and `PRAGMA foreign_keys = ON` header
3. Add `CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)`
4. Add the `messages` table with all columns: `id`, `agent_id`, `thread_id`,
   `message_type`, `in_reply_to`, `confidence`, `tlp_level`, `promote`, `tags`,
   `raw_content`, `frontmatter`, `body`, `timestamp`, `ingested_at`, `ext`
5. Add all six indexes: `idx_messages_agent`, `idx_messages_thread`,
   `idx_messages_type`, `idx_messages_timestamp`, `idx_messages_promote`,
   `idx_messages_tlp`
6. Do **not** include domain-specific `INSERT INTO _schema_meta` rows
   (those are injected by `init.py` at runtime)
7. Validate: `sqlite3 /tmp/test.db < cairn/db/schema/topic_common.sql` exits 0

**Test Cases:**
```python
# tests/test_db_init_it.py

def test_topic_common_sql_is_valid_ddl(tmp_path):
    """topic_common.sql can be applied to a blank SQLite database."""

def test_messages_table_columns_match_osint(tmp_path):
    """Columns created by topic_common.sql match osint.sql exactly."""

def test_schema_meta_table_created(tmp_path):
    """_schema_meta table present after applying topic_common.sql."""
```

**Acceptance Criteria:**
- `cairn/db/schema/topic_common.sql` exists and is valid SQLite DDL
- Column list matches `osint.sql` `messages` table exactly (checked by test)
- No hard-coded `INSERT INTO _schema_meta` rows in the file
- 3+ tests passing for this task

---

### Task 1.2: Extend `init.py` — SCHEMA_FILES, _TOPIC_METADATA, _schema_meta inserts
**Estimate:** 2 hours  
**Priority:** Critical  
**Implements:** FR-1, FR-2

**Objectives:**
- [ ] Add five new entries to `SCHEMA_FILES` dict (each pointing to `topic_common.sql`)
- [ ] Add five new entries to `_TOPIC_METADATA` dict with display names and domain tags
- [ ] Add `_schema_meta` insert logic so each new DB gets `schema_version` and `domain` rows
- [ ] Confirm `init_all()` creates all five new `.db` files via `cairn-admin init-db`
- [ ] Confirm the operation is idempotent (second run does not crash or duplicate rows)

**Files to modify:**
- `cairn/db/init.py`

**Implementation Steps:**
1. Locate `SCHEMA_FILES` dict and append:
   ```python
   "aws":        "topic_common.sql",
   "azure":      "topic_common.sql",
   "networking": "topic_common.sql",
   "systems":    "topic_common.sql",
   "pam":        "topic_common.sql",
   ```
2. Locate `_TOPIC_METADATA` dict and append entries for `aws`, `azure`,
   `networking`, `systems`, `pam` with `display_name`, `description`,
   and `domain_tags` (see design.md for exact values)
3. In `init_db()` (or `_register_topic_dbs()`), after applying the SQL file,
   add two `INSERT OR IGNORE INTO _schema_meta` statements for
   `("schema_version", "1")` and `("domain", domain_slug)`
4. Run `cairn-admin init-db` locally and verify five new `.db` files appear
5. Run again — confirm no error (idempotency via `CREATE TABLE IF NOT EXISTS`
   and `INSERT OR IGNORE`)

**Test Cases:**
```python
def test_init_all_creates_five_new_databases(tmp_path):
    """init_all() creates aws.db, azure.db, networking.db, systems.db, pam.db."""

def test_new_databases_contain_messages_table(tmp_path):
    """Each new .db file has a messages table with the correct schema."""

def test_schema_meta_domain_row_inserted(tmp_path):
    """_schema_meta contains ('domain', <slug>) for each new database."""

def test_init_all_is_idempotent(tmp_path):
    """Calling init_all() twice raises no errors and creates no duplicate rows."""

def test_existing_osint_db_untouched(tmp_path):
    """osint.db is opened for schema check only; data rows are preserved."""
```

**Acceptance Criteria:**
- `cairn-admin init-db` creates exactly five new `.db` files alongside
  `osint.db` and `vulnerabilities.db`
- Each new database has `messages` table, `_schema_meta` table with correct
  `domain` value
- Second `init-db` run exits cleanly
- 5+ tests passing for this task

---

### Task 1.3: Smoke Test — Seven Databases End-to-End
**Estimate:** 1 hour  
**Priority:** High  
**Implements:** FR-1, US-1, US-2

**Objectives:**
- [ ] Confirm `GET /health` returns all seven slugs after `init-db`
- [ ] POST a test message to each new database; confirm `201 Created`
- [ ] GET messages back from each new database via `?db=<slug>`
- [ ] Confirm UI database filter dropdown lists all seven databases

**Implementation Steps:**
1. Start the dev stack: `docker compose up -d`
2. Run `cairn-admin init-db` if not already done in Task 1.2
3. Call `GET /health` and assert `aws`, `azure`, `networking`, `systems`, `pam`
   appear in the `topic_dbs` array
4. For each new slug, POST a minimal test message and assert `201`
5. For each new slug, `GET /messages?db=<slug>` and assert the posted message
   appears
6. Open the UI, log in, and visually confirm the database filter shows all
   seven options

**Acceptance Criteria:**
- `GET /health` lists all seven slugs
- POST + GET round-trip works for every new database slug
- No regression on `osint` and `vulnerabilities` (existing messages still
  visible, existing tests still pass)

---

## Phase 2: Entity Extraction and Vault Routing (Days 3–5) — 8 hours

### Task 2.1: Add `domain` Field to `Entity` Dataclass
**Estimate:** 0.5 hours  
**Priority:** Critical  
**Implements:** FR-3–FR-8, NFR-4

**Objectives:**
- [ ] Add `domain: str | None = None` as a trailing optional field on `Entity`
- [ ] Confirm the frozen dataclass is still hashable after the change
- [ ] Confirm existing callers that construct `Entity(type=..., value=..., span=...)` are unaffected

**Files to modify:**
- `cairn/nlp/entity_extractor.py`

**Implementation Steps:**
1. Locate the `Entity` dataclass definition
2. Append `domain: str | None = None` as the last field (with default `None`
   so all existing callers remain valid without changes)
3. Run the existing entity extractor test suite — all tests must pass unchanged

**Test Cases:**
```python
def test_entity_domain_defaults_to_none():
    """Entity constructed without domain field has domain=None."""

def test_entity_with_domain_is_hashable():
    """Entity(type='arn', value='...', span=(0,3), domain='aws') is hashable."""

def test_existing_entity_construction_unchanged():
    """Entity(type='ipv4', value='1.2.3.4', span=(0,7)) still works."""
```

**Acceptance Criteria:**
- All existing entity extractor tests pass without modification
- `Entity` with and without `domain` both work
- 3+ tests passing for this task

---

### Task 2.2: Implement AWS, Azure, Networking, and PAM Patterns
**Estimate:** 2.5 hours  
**Priority:** Critical  
**Implements:** FR-3, FR-4, FR-5, FR-7

**Objectives:**
- [ ] Add `_has_context()` helper function
- [ ] Add three AWS patterns: `_RE_AWS_ARN`, `_RE_AWS_REGION`, `_RE_AWS_ACCOUNT_RAW` + `_RE_AWS_ACCOUNT_CTX`
- [ ] Add two Azure patterns: `_RE_AZURE_UUID_RAW` + `_RE_AZURE_UUID_CTX`, `_RE_AZURE_RG`
- [ ] Add two networking patterns: `_RE_CIDR`, `_RE_VLAN`
- [ ] Add two PAM patterns: `_RE_CYBERARK_SAFE_BODY`, `_RE_CYBERARK_SAFE_TAG`
- [ ] Update inner `_add()` function to accept optional `domain` parameter
- [ ] Append new extraction blocks to `extract()` (after existing blocks)
- [ ] Verify: new blocks do not alter iteration order of existing blocks

**Files to modify:**
- `cairn/nlp/entity_extractor.py`

**Implementation Steps:**
1. After existing compiled patterns, add `_has_context()` as a module-level
   function (see design.md for signature and body)
2. Add AWS, Azure, networking, PAM compiled patterns in that order
3. Update `_add()` inner function: add `domain: str | None = None` parameter
   and include `domain=domain` in the `Entity(...)` constructor call
4. Append new extraction blocks to `extract()`:
   - AWS ARN block → `_add("arn", ..., domain="aws")`
   - AWS account ID block with `_has_context(..., window=20)` guard
   - AWS region block
   - Azure UUID block with `_has_context(..., window=30)` guard
   - Azure resource group block
   - CIDR block
   - VLAN block
   - CyberArk safe (body) block
   - CyberArk safe (tags) block — iterates `tags` list
5. Verify all existing extraction blocks are unchanged (no edits to IPv4,
   IPv6, CVE, T-ID, FQDN, actor blocks)

**Test Cases:**
```python
# tests/test_entity_extractor_it.py

# AWS
def test_extracts_arn_from_body():
    """arn:aws:iam::123456789012:role/AdminRole → Entity(type='arn', domain='aws')"""

def test_extracts_aws_region():
    """'us-east-1' → Entity(type='aws_region', domain='aws')"""

def test_extracts_account_id_with_context():
    """'account 123456789012' → Entity(type='aws_account_id')"""

def test_suppresses_account_id_without_context():
    """Bare '123456789012' with no 'account' nearby → not extracted"""

def test_suppresses_account_id_outside_window():
    """'account' 25 chars from the 12-digit number → not extracted"""

# Azure
def test_extracts_azure_subscription_uuid_with_context():
    """'subscription 550e8400-...' → Entity(type='azure_subscription_id', domain='azure')"""

def test_suppresses_uuid_without_subscription_context():
    """UUID with no 'subscription' nearby → not extracted"""

def test_extracts_azure_resource_group():
    """/resourceGroups/prod-rg-001 → Entity(type='azure_resource_group')"""

# Networking
def test_extracts_cidr_valid():
    """'10.100.0.0/16' → Entity(type='cidr', domain='networking')"""

def test_suppresses_invalid_cidr_octet():
    """'999.0.0.0/8' → not extracted"""

def test_extracts_vlan_with_space():
    """'VLAN 100' → Entity(type='vlan', value='VLAN 100', domain='networking')"""

def test_extracts_vlan_without_space():
    """'VLAN100' → Entity(type='vlan', domain='networking')"""

def test_extracts_vlan_case_insensitive():
    """'vlan 200' → Entity(type='vlan')"""

# PAM
def test_extracts_cyberark_safe_from_body():
    """'Safe: AWS-Console-Access' → Entity(type='cyberark_safe', domain='pam')"""

def test_extracts_cyberark_safe_from_tags():
    """tags=['safe:AWS-Console-Access'] → Entity(type='cyberark_safe', domain='pam')"""

# Backward compatibility
def test_existing_ipv4_extraction_unchanged():
    """IPv4 extraction still works and domain is None."""

def test_existing_cve_extraction_unchanged():
    """CVE extraction still works and domain is None."""

def test_existing_actor_extraction_unchanged():
    """Actor extraction still works and domain is None."""
```

**Acceptance Criteria:**
- All 15+ new pattern tests pass
- All pre-existing entity extractor tests still pass
- False-positive guard tests pass (account ID without context, UUID without context)
- 15+ tests passing for this task

---

### Task 2.3: Add Systems FQDN Domain Tagging
**Estimate:** 0.5 hours  
**Priority:** Medium  
**Implements:** FR-6

**Objectives:**
- [ ] Add `topic_db: str | None = None` parameter to `extract()`
- [ ] When `topic_db == "systems"`, pass `domain="systems"` to `_add()` for FQDNs
- [ ] All other callers that omit `topic_db` continue to receive `domain=None` on FQDNs

**Files to modify:**
- `cairn/nlp/entity_extractor.py`

**Implementation Steps:**
1. Add `topic_db: str | None = None` as a keyword-only parameter to `extract()`
   (after existing `tags` parameter)
2. In the FQDN extraction block, derive `fqdn_domain = "systems" if topic_db == "systems" else None`
3. Pass `domain=fqdn_domain` to `_add()` for FQDNs
4. All other entity blocks are unaffected (they already pass an explicit domain
   or `None`)

**Test Cases:**
```python
def test_fqdn_tagged_systems_when_topic_db_is_systems():
    """extract('host.corp.local', topic_db='systems') → fqdn with domain='systems'"""

def test_fqdn_not_tagged_systems_for_other_dbs():
    """extract('host.corp.local', topic_db='osint') → fqdn with domain=None"""

def test_fqdn_not_tagged_systems_without_topic_db():
    """extract('host.corp.local') → fqdn with domain=None"""
```

**Acceptance Criteria:**
- 3 tests passing for this task
- No change to FQDN extraction when `topic_db` is omitted

---

### Task 2.4: Migrate `promotion_candidates` — Add `entity_domain` Column
**Estimate:** 0.5 hours  
**Priority:** High  
**Implements:** FR-8

**Objectives:**
- [ ] Write the `ALTER TABLE promotion_candidates ADD COLUMN entity_domain TEXT` migration
- [ ] Wrap in an idempotency guard (`try/except OperationalError`)
- [ ] Confirm existing `promotion_candidates` rows are preserved with `entity_domain = NULL`
- [ ] Confirm the migration can be applied to a fresh `index.db` created by `init_all()`

**Files to modify or create:**
- `cairn/db/migrations/004_add_entity_domain.sql` (or equivalent migration runner entry)

**Implementation Steps:**
1. Determine how existing migrations are run (check `cairn/db/` for a
   `migrations/` directory or migration runner)
2. Create `004_add_entity_domain.sql` with:
   ```sql
   ALTER TABLE promotion_candidates ADD COLUMN entity_domain TEXT;
   ```
3. If the project uses a Python migration runner, add an idempotency wrapper:
   ```python
   try:
       await db.execute("ALTER TABLE promotion_candidates ADD COLUMN entity_domain TEXT")
   except aiosqlite.OperationalError:
       pass  # column already exists
   ```
4. Run the migration against a dev `index.db` that has existing rows
5. Confirm rows still present, `entity_domain` is `NULL` for all old rows

**Test Cases:**
```python
def test_migration_adds_entity_domain_column(tmp_path):
    """Migration adds entity_domain TEXT column to promotion_candidates."""

def test_migration_is_idempotent(tmp_path):
    """Running migration twice raises no error."""

def test_existing_rows_preserved_after_migration(tmp_path):
    """Pre-existing promotion_candidates rows survive with entity_domain=NULL."""
```

**Acceptance Criteria:**
- Migration runs cleanly on fresh and populated `index.db`
- `entity_domain` column present and nullable
- 3+ tests passing for this task

---

### Task 2.5: Update `vault/writer.py` for Domain-Aware Routing
**Estimate:** 1 hour  
**Priority:** High  
**Implements:** FR-8, NFR-5

**Objectives:**
- [ ] Add `domain: str | None = None` parameter to `write_note()`
- [ ] When `domain` is set, resolve target path as `vault_root/cairn/{domain}/`
- [ ] When `domain` is `None`, preserve existing path `vault_root/cairn/`
- [ ] Create domain subdirectory with `mkdir(parents=True, exist_ok=True)` before writing
- [ ] Extend `_entity_type_to_tag()` with eight new IT domain entity types

**Files to modify:**
- `cairn/vault/writer.py`

**Implementation Steps:**
1. Add `domain: str | None = None` as the last parameter to `write_note()`
2. Replace the fixed `cairn_dir = vault_root / _CAIRN_SUBDIR` block with:
   ```python
   if domain:
       target_dir = vault_root / _CAIRN_SUBDIR / domain
       vault_rel_prefix = f"{_CAIRN_SUBDIR}/{domain}"
   else:
       target_dir = vault_root / _CAIRN_SUBDIR
       vault_rel_prefix = _CAIRN_SUBDIR
   target_dir.mkdir(parents=True, exist_ok=True)
   ```
3. Replace all remaining references to `cairn_dir` with `target_dir`
4. Update `_entity_type_to_tag()` to add mappings for: `arn`, `aws_account_id`,
   `aws_region`, `azure_subscription_id`, `azure_resource_group`, `cidr`,
   `vlan`, `cyberark_safe` (see design.md for exact tag strings)

**Test Cases:**
```python
# tests/test_vault_writer_it.py

def test_aws_entity_writes_to_aws_subdirectory(tmp_path):
    """write_note(..., domain='aws') creates note at cairn/aws/<entity>.md"""

def test_pam_entity_writes_to_pam_subdirectory(tmp_path):
    """write_note(..., domain='pam') creates note at cairn/pam/<safe>.md"""

def test_domain_subdirectory_created_if_missing(tmp_path):
    """cairn/azure/ is created automatically on first write."""

def test_cybersecurity_entity_writes_to_cairn_root(tmp_path):
    """write_note(..., domain=None) writes to cairn/<entity>.md (unchanged)."""

def test_existing_note_updated_in_domain_subdirectory(tmp_path):
    """Second write_note to same domain entity appends rather than duplicates."""

def test_arn_entity_type_maps_to_aws_resource_tag():
    """_entity_type_to_tag('arn') → 'aws-resource'"""

def test_cyberark_safe_maps_to_pam_safe_tag():
    """_entity_type_to_tag('cyberark_safe') → 'pam-safe'"""
```

**Acceptance Criteria:**
- Domain subdirectory path is correct for all five new domain slugs
- Cybersecurity entities (domain=None) write to `cairn/` root as before
- `mkdir` called unconditionally — no crash if directory already exists
- 7+ tests passing for this task

---

### Task 2.6: Wire `domain` Through Corroboration Job and Promotions Route
**Estimate:** 1 hour  
**Priority:** High  
**Implements:** FR-8

**Objectives:**
- [ ] Update `cairn/jobs/corroboration.py` to pass `topic_db` to `extract()`
- [ ] Update `cairn/jobs/corroboration.py` to persist `entity.domain` in the
  `entity_domain` column when inserting a `promotion_candidates` row
- [ ] Update `cairn/api/routes/promotions.py` to read `candidate.entity_domain`
  and pass it as `domain=` to `write_note()`

**Files to modify:**
- `cairn/jobs/corroboration.py`
- `cairn/api/routes/promotions.py`

**Implementation Steps:**
1. In `corroboration.py`, locate the `extract()` call and add
   `topic_db=message.topic_db`
2. In the `INSERT INTO promotion_candidates` statement, add `entity_domain=entity.domain`
   to the VALUES clause (new nullable column from Task 2.4)
3. In `promotions.py`, locate the `write_note()` call and add
   `domain=candidate.entity_domain`
4. Confirm that for `osint` and `vulnerabilities` messages, `entity.domain` is
   `None` and `write_note()` still writes to `cairn/` root

**Test Cases (integration, see Task 2.7):**
- These are covered by integration test cases in Task 2.7

**Acceptance Criteria:**
- `entity_domain` column populated for IT domain candidates
- `entity_domain` is `None` for cybersecurity candidates
- Promotion of an IT entity writes to the correct domain subdirectory

---

### Task 2.7: Unit and Integration Tests — Phase 2
**Estimate:** 1.5 hours  
**Priority:** High

**Objectives:**
- [ ] Write integration tests covering the full extract → corroborate → promote → vault path
- [ ] Verify Phase 4.1 regression (osint and vulnerabilities unaffected)
- [ ] Reach 20+ passing tests across Phase 2

**Files to create:**
- `tests/test_entity_extractor_it.py` (unit — started in Tasks 2.2–2.3)
- `tests/test_vault_writer_it.py` (unit — started in Task 2.5)
- `tests/test_phase42_integration.py`

**Integration Test Cases:**
```python
# tests/test_phase42_integration.py

def test_post_to_aws_returns_201():
    """POST /messages with topic_db=aws returns 201."""

def test_pam_corroboration_creates_candidate_with_domain():
    """Corroboration job on a PAM message creates a candidate with entity_domain='pam'."""

def test_promote_pam_candidate_writes_vault_note_in_pam_dir(tmp_path):
    """POST /promotions/{id}/promote writes vault note to cairn/pam/."""

def test_promote_aws_candidate_writes_vault_note_in_aws_dir(tmp_path):
    """POST /promotions/{id}/promote writes vault note to cairn/aws/."""

def test_osint_behaviour_unchanged():
    """Post to osint → corroborate → promote produces cairn/<entity>.md (no subdir)."""

def test_cybersecurity_entity_note_at_cairn_root_after_phase42():
    """CVE entity promoted after Phase 4.2 changes writes to cairn/ root unchanged."""

def test_get_health_includes_all_seven_slugs():
    """GET /health lists aws, azure, networking, systems, pam alongside existing two."""

def test_migration_adds_column_without_data_loss():
    """Running migration on populated index.db preserves existing candidates."""
```

**Acceptance Criteria:**
- 20+ total tests passing across Phase 2 tasks
- All Phase 4.1 tests still green
- End-to-end: post → extract → promote → vault note in correct domain directory

---

## Phase 3: Playbooks and Documentation (Days 6–7) — 5 hours

### Task 3.1: Configure `CAIRN_GITLAB_METHODOLOGY_DIR` for Playbook Sync
**Estimate:** 1 hour  
**Priority:** High  
**Implements:** FR-9, US-5

**Objectives:**
- [ ] Update `.env.example` to document `CAIRN_GITLAB_METHODOLOGY_DIR=.`
- [ ] Set `CAIRN_GITLAB_METHODOLOGY_DIR=.` in the dev `.env` file
- [ ] Submit a sample playbook via `POST /methodologies` and verify the ChromaDB
  entry is created
- [ ] Confirm `playbooks/` YAML is accepted without Sigma validation errors
- [ ] Confirm `sigma/` files still go through Sigma validation (no regression)

**Files to modify:**
- `.env.example`
- `.env` (dev only — not committed)

**Sample playbook for testing:**
```yaml
# playbooks/pam/cyberark-psm-rdp.yaml
title: "CyberArk PSM RDP Session Recording Setup"
domain: pam
version: "1.0"
status: proposed
description: >
  Procedure for enabling and validating RDP session recording
  via CyberArk Privileged Session Manager.
steps:
  - Confirm PSM server target platform assignment
  - Verify session recording policy in PVWA
  - Test connection through PSM; confirm recording starts
  - Retrieve session recording from vault
```

**Implementation Steps:**
1. Add to `.env.example` (below the existing methodology dir entry):
   ```bash
   # Set to '.' (repo root) to sync sigma/, methodologies/, AND playbooks/.
   # Default 'methodologies' covers cybersecurity playbooks only.
   CAIRN_GITLAB_METHODOLOGY_DIR=.
   ```
2. Set the same in dev `.env`
3. Commit the sample playbook YAML to the GitLab methodology repo under
   `playbooks/pam/cyberark-psm-rdp.yaml`
4. Trigger the GitLab webhook (or call it manually in dev)
5. Confirm ChromaDB has an entry for the playbook path
6. Call `GET /methodologies/search?q=CyberArk+PSM+RDP` and verify the entry
   is returned

**Acceptance Criteria:**
- `.env.example` documents `CAIRN_GITLAB_METHODOLOGY_DIR=.`
- Playbook YAML accepted by the methodology endpoint without Sigma error
- ChromaDB entry created for the playbook file
- `GET /methodologies/search` returns the playbook result

---

### Task 3.2: Update Agent Skill Documentation
**Estimate:** 1 hour  
**Priority:** Medium  
**Implements:** FR-11, US-1, US-5

**Objectives:**
- [ ] Add five new slugs to the `topic_db` valid-values table
- [ ] Add `playbooks/{domain}/` as an example path in the methodology path table
- [ ] Add a note reinforcing that agents SHOULD query `GET /health` to discover
  slugs dynamically rather than relying on the hardcoded list
- [ ] Copy the updated file to the local skill install path

**Files to modify:**
- `docs/agent-skill/references/message-format.md` (canonical copy)
- `~/.claude/skills/cairn/references/message-format.md` (local install)

**Implementation Steps:**
1. Open `docs/agent-skill/references/message-format.md`
2. Locate the `topic_db` values table; add rows for `aws`, `azure`,
   `networking`, `systems`, `pam` with descriptions matching `_TOPIC_METADATA`
3. Add `playbooks/pam/`, `playbooks/aws/`, etc. as example paths under
   `POST /methodologies`
4. Add a note: *"Agents SHOULD call `GET /health` at startup to discover valid
   `topic_db` values dynamically rather than hardcoding this list."*
5. Copy the updated file to `~/.claude/skills/cairn/references/message-format.md`

**Acceptance Criteria:**
- Both copies of `message-format.md` updated identically
- All five new slugs appear in the valid-values table
- `playbooks/{domain}/` path example present
- Dynamic discovery note present

---

### Task 3.3: End-to-End Integration Test Pass
**Estimate:** 1.5 hours  
**Priority:** High

**Objectives:**
- [ ] Run the full test suite against the dev stack
- [ ] Confirm 30+ tests pass with zero failures
- [ ] Run all Phase 4.1 integration tests — confirm no regression
- [ ] Execute manual test scenarios from the design doc

**Manual Test Scenarios:**
1. `cairn-admin init-db` → five new `.db` files appear in data volume
2. Post a message with `topic_db: aws` → appears in UI with AWS database badge
3. Post a message containing `arn:aws:iam::123456789012:role/AdminRole` → after
   corroboration job runs, candidate appears in Promotion Queue with domain
4. Promote the candidate → vault note at `vault/cairn/aws/arn_aws_iam__...md`
5. Post a message with `Safe: AWS-Console-Access` to `topic_db: pam` →
   promotion candidate created with `entity_domain=pam`
6. Query `GET /methodologies/search?q=CyberArk+PSM` → playbook result returned
7. Post to `osint` → cybersecurity workflow unchanged end-to-end

**Acceptance Criteria:**
- 30+ tests total passing
- Zero test failures
- All Phase 4.1 tests green
- All 7 manual scenarios pass

---

### Task 3.4: Documentation Review
**Estimate:** 0.5 hours  
**Priority:** Low

**Objectives:**
- [ ] Review `docs/spec/phase4.2/requirements.md`, `design.md`, `tasks.md` for
  accuracy against the implemented code
- [ ] Update any spec inaccuracies discovered during implementation
- [ ] Add a brief completion note to `CLAUDE.md` Phase 4 checklist

**Files to modify:**
- `CLAUDE.md` (mark Phase 4.2 items as complete in the Phase 4 checklist)
- `docs/spec/phase4.2/` (correction edits only if spec diverged from reality)

**Implementation Steps:**
1. Read the Phase 4 section of `CLAUDE.md`; check off items completed by
   Phase 4.2
2. If any spec document has an inaccuracy vs. actual implementation, make the
   minimal edit to correct it
3. No new prose needed — just accuracy cleanup

**Acceptance Criteria:**
- `CLAUDE.md` Phase 4 checklist reflects Phase 4.2 completions
- Spec documents accurately describe the implemented behavior

---

## Testing Summary

### Unit Tests (25+ tests)
- `test_entity_extractor_it.py`: 15+ tests (AWS, Azure, networking, PAM, systems, backward compat)
- `test_vault_writer_it.py`: 7+ tests (domain routing, tag mapping, edge cases)
- `test_db_init_it.py`: 5+ tests (schema creation, metadata, idempotency)
- `test_migration_entity_domain.py`: 3+ tests (migration correctness, idempotency)

### Integration Tests (10+ tests)
- End-to-end POST → query per new database slug
- Corroboration → candidate with domain field
- Promotion → vault note in domain subdirectory
- Phase 4.1 regression (osint and vulnerabilities unchanged)
- Playbook ChromaDB sync
- `GET /health` seven-slug response
- Migration on populated database

### Manual Tests
1. `cairn-admin init-db` → five new `.db` files in data volume
2. UI database filter dropdown shows all seven databases
3. AWS ARN in message body → promotion candidate in queue
4. Promote AWS candidate → vault note in `cairn/aws/`
5. Promote CVE candidate → vault note in `cairn/` root (no regression)
6. `GET /methodologies/search` returns IT playbook result

---

## Success Criteria

### Phase 1 (MVP — Databases Active)
- ✅ Five new `.db` files created by `cairn-admin init-db`
- ✅ `GET /health` returns all seven slugs
- ✅ Agents can `POST /messages?db=aws` and retrieve messages back
- ✅ UI database filter lists all seven options
- ✅ 5+ unit tests for init logic
- ✅ All Phase 4.1 tests still passing

### Phase 2 (Entity Extraction and Vault Routing)
- ✅ AWS ARN, account ID (with context guard), and region extracted
- ✅ Azure subscription UUID (with context guard) and resource group extracted
- ✅ CIDR and VLAN extracted; invalid CIDRs suppressed
- ✅ CyberArk Safe extracted from body and from tags
- ✅ Systems FQDNs tagged with `domain="systems"`
- ✅ Vault notes appear in `cairn/{domain}/` subdirectory for IT entities
- ✅ Cybersecurity entities still write to `cairn/` root
- ✅ 20+ tests passing

### Phase 3 (Playbooks and Documentation)
- ✅ Playbook YAML accepted and committed to GitLab without Sigma error
- ✅ `GET /methodologies/search` returns playbook results from ChromaDB
- ✅ Agent skill docs list all seven `topic_db` slugs
- ✅ End-to-end: post → extract → promote → vault note in correct directory
- ✅ 30+ tests total; all passing
- ✅ `CLAUDE.md` Phase 4 checklist updated

---

## Timeline

**Days 1–2 (Phase 1):** 6 hours
- Day 1: Tasks 1.1–1.2 (topic_common.sql + init.py extension) — 4h
- Day 2: Task 1.3 (smoke test, all seven databases) — 1h; buffer — 1h

**Days 3–5 (Phase 2):** 8 hours
- Day 3: Tasks 2.1–2.2 (Entity dataclass + new patterns, 3h)
- Day 4: Tasks 2.3–2.5 (systems tagging, migration, vault routing, 2.5h)
- Day 5: Tasks 2.6–2.7 (wire domain through pipeline, integration tests, 2.5h)

**Days 6–7 (Phase 3):** 5 hours
- Day 6: Tasks 3.1–3.2 (playbook config + skill docs, 2h)
- Day 7: Tasks 3.3–3.4 (end-to-end test pass + doc review, 2h); buffer — 1h

**Total:** ~19 hours over 7 working days
