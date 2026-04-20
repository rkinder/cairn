# Phase 4.4 — CouchDB Vault Sync Tasks

## Phase 1: CouchDB Client and LiveSync Format

### Task 1.1: CouchDBVaultClient Skeleton
**Estimate:** 2 hours
**Priority:** High

- [ ] Create `cairn/vault/couchdb_sync.py`
- [ ] Define `PutResult` frozen dataclass (`success`, `doc_id`, `revision`, `error`)
- [ ] Implement `CouchDBVaultClient.__init__` (url, username, password, database, chunk_threshold_bytes, optional http_client)
- [ ] Hold a single `httpx.AsyncClient` with Basic Auth and connection pooling
- [ ] Implement `ping()` — GET `/{db}` with auth, return True on 200
- [ ] Implement `close()` — close the shared `httpx.AsyncClient`
- [ ] All exceptions caught at method boundary; no raises escape the client

**Files to create:**
- `cairn/vault/couchdb_sync.py`

**Property tests:**
- WHEN constructed with valid URL + credentials THEN client SHALL be usable without side effects
- WHEN `ping()` hits a 200 response THEN result SHALL be True
- WHEN `ping()` hits a 401 or 5xx THEN result SHALL be False (no exception)
- WHEN `ping()` hits a connection error THEN result SHALL be False (no exception)
- WHEN `close()` is called THEN the underlying `httpx.AsyncClient` SHALL be closed

---

### Task 1.2: put_note() Create Path
**Estimate:** 2 hours
**Priority:** High

- [ ] Implement `put_note()` for the create case (no existing `_rev`)
- [ ] Build LiveSync document body: `_id`, `data`, `ctime`, `mtime`, `size`, `type: "plain"`, `children: []`
- [ ] Encode `ctime_ms` and `mtime_ms` as integers (milliseconds since epoch)
- [ ] PUT to `/{db}/{_id}` with JSON body and Basic Auth
- [ ] Return `PutResult(success=True, doc_id=..., revision=rev, error=None)` on 201
- [ ] Return `PutResult(success=False, ..., error=<detail>)` on any non-success

**Files to modify:**
- `cairn/vault/couchdb_sync.py`

**Property tests:**
- WHEN a new document is PUT THEN the body SHALL include `data`, `ctime`, `mtime`, `size`, `type`, `children`
- WHEN PUT returns 201 THEN `PutResult.success` SHALL be True AND revision SHALL be set
- WHEN PUT returns 5xx THEN `PutResult.success` SHALL be False AND error SHALL describe the status
- WHEN the HTTP call raises a connection error THEN `PutResult.success` SHALL be False (no raise)
- WHEN `_id` is passed verbatim THEN no URL encoding transformation SHALL alter it

---

### Task 1.3: put_note() Update and Conflict Path
**Estimate:** 1.5 hours
**Priority:** High

- [ ] Before PUT, GET `/{db}/{_id}` to fetch current `_rev`
- [ ] Include `_rev` in PUT body when document already exists
- [ ] On 404 from the pre-fetch, treat as create (no `_rev`)
- [ ] On 409 response to PUT, refetch `_rev` and retry once
- [ ] On second 409, return `PutResult(success=False, error="conflict")`

**Files to modify:**
- `cairn/vault/couchdb_sync.py`

**Property tests:**
- WHEN the document exists THEN the PUT body SHALL include the fetched `_rev`
- WHEN the pre-fetch returns 404 THEN the PUT SHALL proceed without `_rev`
- WHEN the PUT returns 409 once THEN the client SHALL refetch `_rev` and retry
- WHEN the PUT returns 409 twice THEN `PutResult.success` SHALL be False AND error SHALL be "conflict"

---

### Task 1.4: Chunking for Large Documents
**Estimate:** 1.5 hours
**Priority:** Medium

- [ ] When `len(content.encode("utf-8")) >= chunk_threshold_bytes`, split into chunks
- [ ] Compute chunk IDs as `h:<sha256-hex[:32]>`
- [ ] PUT each chunk document (with `data` field set to chunk content)
- [ ] PUT parent document with `data=""`, `size=<total>`, `children=[chunk_ids...]`
- [ ] Document this path is defensive — Cairn-generated notes rarely trigger it

**Files to modify:**
- `cairn/vault/couchdb_sync.py`

**Property tests:**
- WHEN content is below threshold THEN no chunk documents SHALL be created
- WHEN content is above threshold THEN N chunk documents SHALL be PUT before the parent
- WHEN chunking is used THEN parent `children` SHALL list chunk IDs in order
- WHEN a chunk PUT fails THEN the parent PUT SHALL NOT be attempted

---

## Phase 2: Vault Writer and Config Wiring

### Task 2.1: Async write_note() with WriteResult
**Estimate:** 1.5 hours
**Priority:** High

- [ ] Convert `write_note()` to `async def`
- [ ] Add frozen `WriteResult` dataclass (`vault_rel`, `couchdb_synced`, `couchdb_error`)
- [ ] Change return type from `str` to `WriteResult`
- [ ] Add `couchdb_client: CouchDBVaultClient | None = None` parameter
- [ ] After disk write, if client is provided, call `put_note()` with content + ctime/mtime
- [ ] On CouchDB failure, log warning and populate `WriteResult.couchdb_error` — do not raise
- [ ] When client is None, `couchdb_synced=False`, `couchdb_error=None` (not an error)

**Files to modify:**
- `cairn/vault/writer.py`

**Property tests:**
- WHEN `couchdb_client` is None THEN no HTTP call SHALL be made AND `couchdb_synced` SHALL be False
- WHEN CouchDB PUT succeeds THEN `couchdb_synced` SHALL be True
- WHEN CouchDB PUT fails THEN `couchdb_synced` SHALL be False AND `couchdb_error` SHALL be populated
- WHEN CouchDB raises any exception THEN the function SHALL still return a `WriteResult` (no raise)
- WHEN disk write fails THEN CouchDB SHALL NOT be called

---

### Task 2.2: Config Fields with Aliases
**Estimate:** 45 minutes
**Priority:** High

- [ ] Add `couchdb_url`, `couchdb_user`, `couchdb_password`, `couchdb_database` to `Settings`
- [ ] Each uses `validation_alias` to read its unprefixed env var name
- [ ] Add `couchdb_enabled: bool` with prefixed `CAIRN_COUCHDB_ENABLED` (default True)
- [ ] Verify all `test_skill_client.py` setup errors are resolved (134 + 13 → 147 passing)

**Files to modify:**
- `cairn/config.py`

**Property tests:**
- WHEN `.env` sets `COUCHDB_USER=foo` THEN `Settings().couchdb_user` SHALL be "foo"
- WHEN `.env` sets `CAIRN_COUCHDB_ENABLED=false` THEN `Settings().couchdb_enabled` SHALL be False
- WHEN `.env` omits all CouchDB vars THEN `Settings()` SHALL construct with defaults (no ValidationError)

---

### Task 2.3: Dependency Injection Helper
**Estimate:** 45 minutes
**Priority:** High

- [ ] Add `get_couchdb_client()` — returns a process-wide singleton or None
- [ ] Return None when `couchdb_enabled=False` or `couchdb_user` is empty
- [ ] Construct on first call; reuse thereafter
- [ ] Place in `cairn/api/deps.py` if present, otherwise co-locate with the promotions route

**Files to create or modify:**
- `cairn/api/deps.py` (or `cairn/api/routes/promotions.py`)

**Property tests:**
- WHEN `couchdb_enabled=False` THEN `get_couchdb_client()` SHALL return None
- WHEN `couchdb_user=""` THEN `get_couchdb_client()` SHALL return None
- WHEN called twice with valid settings THEN the same instance SHALL be returned

---

### Task 2.4: Promotion Route Call-Site Update
**Estimate:** 30 minutes
**Priority:** High

- [ ] In `promotions.py`, call `get_couchdb_client()` and pass to `write_note()`
- [ ] Await the async `write_note()` and unpack `WriteResult`
- [ ] On `not result.couchdb_synced and client is not None`, log a warning with `couchdb_error`
- [ ] Use `result.vault_rel` wherever the old string return was used

**Files to modify:**
- `cairn/api/routes/promotions.py`

**Property tests:**
- WHEN promotion succeeds with CouchDB reachable THEN response SHALL include the vault path AND CouchDB SHALL receive a PUT
- WHEN promotion succeeds with CouchDB unreachable THEN response SHALL still be 200 AND disk file SHALL exist
- WHEN `couchdb_enabled=False` THEN promotion SHALL succeed without any CouchDB HTTP call

---

### Task 2.5: FastAPI Lifespan Startup Probe
**Estimate:** 30 minutes
**Priority:** Medium

- [ ] In `cairn/main.py`, add a lifespan context that calls `client.ping()` on startup
- [ ] Log "CouchDB vault sync ready at <url>" on success
- [ ] Log warning on failure; do not block startup (NFR-2)
- [ ] Call `await client.close()` on shutdown

**Files to modify:**
- `cairn/main.py`

**Property tests:**
- WHEN CouchDB is reachable at startup THEN the app SHALL start AND an info log SHALL be emitted
- WHEN CouchDB is unreachable at startup THEN the app SHALL start AND a warning log SHALL be emitted
- WHEN shutdown runs THEN `client.close()` SHALL be awaited

---

## Phase 3: Testing, Fixtures, and Documentation

### Task 3.1: LiveSync Sample Fixture
**Estimate:** 30 minutes
**Priority:** High

- [ ] Export one existing document from a real LiveSync-synced CouchDB via `/_utils`
- [ ] Strip any PII from `data` if present, preserve the envelope shape
- [ ] Save as `tests/fixtures/livesync_sample.json`
- [ ] Add a test that constructs a document via `CouchDBVaultClient` and asserts field parity against the fixture

**Files to create:**
- `tests/fixtures/livesync_sample.json`

---

### Task 3.2: Unit Tests for CouchDBVaultClient
**Estimate:** 2 hours
**Priority:** High

- [ ] Use `httpx.MockTransport` — no real CouchDB required
- [ ] Cover all Phase 1 property tests (create, update, conflict retry, chunking, errors)
- [ ] Assert the exact JSON body sent (including `type: "plain"` and ms timestamps)
- [ ] Assert `_id` is passed unchanged through the PUT URL path

**Files to create:**
- `tests/test_couchdb_sync.py`

**Acceptance criteria:**
- 15+ tests passing
- No real network calls (verified by using `MockTransport`)

---

### Task 3.3: Integration Tests for Dual-Write
**Estimate:** 1.5 hours
**Priority:** High

- [ ] Build a fake `CouchDBVaultClient` (or mock transport) to drive the writer
- [ ] Test: CouchDB unavailable → disk file present, `couchdb_synced=False`, no exception
- [ ] Test: CouchDB reachable → disk file present, mock received PUT, `couchdb_synced=True`
- [ ] Test: second promotion for same entity → second PUT carries the returned `_rev`
- [ ] Test: `couchdb_enabled=False` via settings override → no HTTP call

**Files to create:**
- `tests/test_vault_writer_couchdb.py`

**Acceptance criteria:**
- All pre-Phase-4.4 vault writer tests continue to pass with the async signature
- 6+ new integration tests passing

---

### Task 3.4: Manual Verification Runbook
**Estimate:** 1 hour
**Priority:** Medium

- [ ] Add a short runbook to `docs/phase4.md` under a "Phase 4.4 verification" heading
- [ ] Steps: docker compose up → create CouchDB db → trigger promotion → inspect `/_utils` → configure Obsidian LiveSync → confirm note appears
- [ ] Capture a screenshot of the document appearing in `/_utils` for the PR

**Files to modify:**
- `docs/phase4.md`

---

### Task 3.5: CLAUDE.md Phase Checklist Update
**Estimate:** 15 minutes
**Priority:** Low

- [ ] Mark Phase 4.4 items complete in `CLAUDE.md` Development Phases section
- [ ] Update the "Phase 4 — Obsidian Vault Bridge" block to note dual-write is live

**Files to modify:**
- `CLAUDE.md`

---

## Risk Mitigation

### High-Risk Tasks
- **LiveSync format drift (Task 1.2, 3.1)** — If the LiveSync plugin ever
  changes its document shape, Cairn-written documents may stop appearing in
  clients. Mitigation: commit the reference fixture, assert field parity in
  tests, and include a manual verification step (Task 3.4) in the PR.
- **Async signature change on write_note() (Task 2.1)** — Any caller
  missed during refactor becomes a latent bug. Mitigation: grep confirmed
  a single call site in `promotions.py`; tests will catch any other path.

### Mitigation Strategies
- Disk write remains primary; CouchDB failures never break promotion
- `CAIRN_COUCHDB_ENABLED=false` is a single-flag kill switch if CouchDB
  misbehaves in production
- All HTTP exceptions are swallowed at the client boundary — nothing raises
  into the promotion flow

---

## Success Criteria

### Functional
- [ ] Promoted notes appear in Obsidian clients within 60 seconds (US-1)
- [ ] Repromotions update the existing CouchDB document (US-2)
- [ ] Analyst edits in Obsidian are not silently overwritten (US-3)

### Reliability
- [ ] Zero promotion failures caused by CouchDB unavailability
- [ ] CouchDB write success rate > 99% during normal operation

### Operational
- [ ] No new database tables or migrations required
- [ ] No changes to existing promotion UI or API surface (response bodies unchanged)
- [ ] Full test suite (existing + new) passes — target 160+ tests

---

## Estimated Total Effort
**Total:** ~15 hours (~2 person-days)
**Critical Path:** 1.1 → 1.2 → 1.3 → 2.1 → 2.4 → 3.2 → 3.3 (sequential)
**Parallel Work:** 1.4 (chunking), 2.2 (config), 3.1 (fixture), 3.4 (runbook) can run alongside the critical path
