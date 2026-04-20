# Phase 4.4 — CouchDB Vault Sync Design

## Architecture Overview

One new async HTTP client module (`cairn/vault/couchdb_sync.py`) that speaks
the PouchDB document conventions used by Obsidian LiveSync. The existing vault
writer is extended to perform a best-effort dual-write: disk first, CouchDB
second. Disk remains the source of truth. No new database tables, no new API
endpoints.

```
Promotion flow                        Vault writer                 CouchDB
      │                                    │                           │
      │ promote_candidate(id)              │                           │
      │───────────────────────────────────►│                           │
      │                                    │  1. render markdown       │
      │                                    │  2. write to disk         │
      │                                    │     (primary)             │
      │                                    │                           │
      │                                    │  3. PUT /db/{path}        │
      │                                    │     (best effort)         │
      │                                    │──────────────────────────►│
      │                                    │◄──────────────────────────│
      │                                    │     (409 → fetch _rev,    │
      │                                    │      retry once)          │
      │                                    │                           │
      │   vault_rel, couchdb_synced        │                           │
      │◄───────────────────────────────────│                           │
      │                                    │                           │
      │                                    │      LiveSync plugin      │
      │                                    │      pulls on tick        │
      │                                    │                           ▼
      │                                    │              Obsidian clients
```

On CouchDB failure, the disk write still succeeds and the promotion completes.
The failure is logged with the target path and error detail for manual retry.

---

## Core Components

### CouchDBVaultClient (FR-1, FR-3, FR-4)

**Purpose:** Async HTTP client wrapping the standard CouchDB REST API using
the LiveSync document format.

**Module:** `cairn/vault/couchdb_sync.py`

**Interface:**

```python
class CouchDBVaultClient:
    """Async client for writing LiveSync-format documents to CouchDB."""

    def __init__(
        self,
        *,
        url: str,
        username: str,
        password: str,
        database: str,
        chunk_threshold_bytes: int = 250_000,
        http_client: httpx.AsyncClient | None = None,
    ): ...

    async def put_note(
        self,
        *,
        vault_rel_path: str,
        content: str,
        ctime_ms: int,
        mtime_ms: int,
    ) -> PutResult:
        """Create or update a vault note document.

        Fetches current _rev if the document exists, PUTs with revision,
        retries once on 409. Returns (success: bool, doc_id: str, error: str | None).
        """

    async def ping(self) -> bool:
        """Return True if CouchDB is reachable and auth succeeds."""

    async def close(self) -> None: ...


@dataclass(frozen=True)
class PutResult:
    success: bool
    doc_id: str
    revision: str | None
    error: str | None
```

**Error handling:**
- Connection errors, 5xx, auth failures → return `PutResult(success=False, ...)` and log
- 409 on update → fetch latest `_rev`, retry once; on second 409, log as conflict
- All exceptions caught at this boundary — never propagates into the promotion flow

---

### LiveSync Document Format (FR-2)

**Purpose:** Emit documents that Obsidian LiveSync recognizes without modification.

Small documents (content < `chunk_threshold_bytes`) are stored inline:

```json
{
  "_id": "cairn/aws/arn_aws_iam_123456789012_role_AdminRole.md",
  "data": "---\ntitle: arn:aws:iam::123456789012:role/AdminRole\n...",
  "ctime": 1713628320000,
  "mtime": 1713628320000,
  "size": 1234,
  "type": "plain",
  "children": []
}
```

Large documents (content ≥ `chunk_threshold_bytes`) are split. Each chunk is
stored as a separate document with `_id` of the form `h:<sha256-prefix>` and
the parent document's `children` array holds the chunk IDs in order:

```json
{
  "_id": "cairn/large_note.md",
  "data": "",
  "ctime": 1713628320000,
  "mtime": 1713628320000,
  "size": 512000,
  "type": "plain",
  "children": ["h:a1b2c3d4...", "h:e5f6g7h8...", ...]
}
```

**Chunking strategy:**
- Only activates when `len(content.encode("utf-8")) >= chunk_threshold_bytes`
- Cairn-generated notes are expected to remain under threshold; chunking is
  implemented defensively and will rarely fire

**Document ID conventions:**
- `_id` for parent documents: the vault-relative POSIX path (forward slashes)
- `_id` for chunk documents: `h:<hex-digest>` where digest is the first 32 chars
  of SHA-256 over the chunk bytes

The exact format is reverse-engineered from an existing LiveSync database
via `/_utils`. A sample document from a known-working vault is checked in
as `tests/fixtures/livesync_sample.json` for reference during validation.

---

### Vault Writer Integration (FR-1)

**Purpose:** Extend `write_note()` to perform CouchDB sync after the disk write.

**Module:** `cairn/vault/writer.py` (modified)

**Changes:**

```python
async def write_note(
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
    domain: str | None = None,
    couchdb_client: CouchDBVaultClient | None = None,   # NEW
) -> WriteResult:                                       # CHANGED return type
    # ... existing disk write (unchanged) ...
    disk_written_at = time.time()
    file_bytes = note_file.read_bytes()

    couchdb_synced = False
    couchdb_error: str | None = None
    if couchdb_client is not None:
        ctime_ms = int(note_file.stat().st_ctime * 1000)
        mtime_ms = int(disk_written_at * 1000)
        result = await couchdb_client.put_note(
            vault_rel_path=vault_rel,
            content=file_bytes.decode("utf-8"),
            ctime_ms=ctime_ms,
            mtime_ms=mtime_ms,
        )
        couchdb_synced = result.success
        couchdb_error = result.error
        if not result.success:
            logger.warning(
                "vault/writer: CouchDB sync failed for %s: %s",
                vault_rel, couchdb_error,
            )

    return WriteResult(
        vault_rel=vault_rel,
        couchdb_synced=couchdb_synced,
        couchdb_error=couchdb_error,
    )


@dataclass(frozen=True)
class WriteResult:
    vault_rel: str
    couchdb_synced: bool
    couchdb_error: str | None
```

**Breaking-change note:** `write_note()` becomes `async` and returns a
`WriteResult` instead of a bare string. This is a deliberate, localized
breakage — only `cairn/api/routes/promotions.py` calls it. The route handler
is already async; updating the call site is a one-line change.

**Backward compatibility for callers passing no client:** When
`couchdb_client` is `None`, the function behaves exactly as it did in Phase
4.2 (disk-only, `couchdb_synced=False`). Existing tests continue to exercise
this path without modification.

---

### Configuration (FR-4)

**Module:** `cairn/config.py` (modified)

The CouchDB env vars do **not** use the `CAIRN_` prefix — they're shared with
`docker-compose.yml` for the CouchDB service itself. Use Pydantic field
aliases to read them without changing the global `env_prefix`:

```python
# cairn/config.py — added to Settings class

couchdb_url: str = Field(
    default="http://couchdb:5984",
    validation_alias="COUCHDB_URL",
)
couchdb_user: str = Field(
    default="",
    validation_alias="COUCHDB_USER",
)
couchdb_password: str = Field(
    default="",
    validation_alias="COUCHDB_PASSWORD",
)
couchdb_database: str = Field(
    default="obsidian-livesync",
    validation_alias="COUCHDB_DATABASE",
)
couchdb_enabled: bool = Field(
    default=True,
    validation_alias="CAIRN_COUCHDB_ENABLED",
    description="Set false to disable the CouchDB dual-write entirely.",
)
```

`validation_alias` bypasses `env_prefix` so these four fields read their
unprefixed names directly. The extra `CAIRN_COUCHDB_ENABLED` toggle uses the
standard prefix and defaults to true — giving operators a single-flag kill
switch without tearing out credentials.

---

### Dependency Injection (FR-1)

**Module:** `cairn/api/deps.py` (new shared-dependency helpers — if already
exists, add to it; otherwise co-locate with promotions route)

```python
_couchdb_client: CouchDBVaultClient | None = None


def get_couchdb_client() -> CouchDBVaultClient | None:
    """Return the shared CouchDB client, or None if disabled/unconfigured."""
    global _couchdb_client
    settings = get_settings()
    if not settings.couchdb_enabled or not settings.couchdb_user:
        return None
    if _couchdb_client is None:
        _couchdb_client = CouchDBVaultClient(
            url=settings.couchdb_url,
            username=settings.couchdb_user,
            password=settings.couchdb_password,
            database=settings.couchdb_database,
        )
    return _couchdb_client
```

Used in `promotions.py`:

```python
client = get_couchdb_client()
result = await write_note(..., couchdb_client=client)
if not result.couchdb_synced and client is not None:
    logger.warning("promote_candidate: CouchDB sync failed: %s", result.couchdb_error)
```

**Startup probe:** `cairn/main.py` (or the FastAPI lifespan hook) calls
`await client.ping()` once during startup if a client is constructed. A
failed ping logs a warning but does not block startup (NFR-2).

---

## Integration Points

### In `cairn/api/routes/promotions.py`
Single call site update — pass the injected client into `write_note()` and
await the result. The existing try/except around the disk write expands to
cover the CouchDB failure mode (already handled inside the writer).

### In `cairn/main.py` (lifespan)
Add startup ping and shutdown close:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    client = get_couchdb_client()
    if client is not None:
        if await client.ping():
            logger.info("CouchDB vault sync ready at %s", settings.couchdb_url)
        else:
            logger.warning("CouchDB vault sync unreachable — promotions will disk-write only")
    yield
    if client is not None:
        await client.close()
```

### In `docker-compose.yml`
No changes required. The existing CouchDB service and credential env wiring
already match the field aliases. Verify only.

---

## Files to Create

| File | Purpose |
|---|---|
| `cairn/vault/couchdb_sync.py` | `CouchDBVaultClient` + `PutResult` dataclass |
| `tests/test_couchdb_sync.py` | Unit tests for the client (httpx mock transport) |
| `tests/test_vault_writer_couchdb.py` | Integration tests for dual-write behavior |
| `tests/fixtures/livesync_sample.json` | Reference LiveSync document from a real vault |

## Files to Modify

| File | Change |
|---|---|
| `cairn/vault/writer.py` | `async` signature, `WriteResult` return, CouchDB call |
| `cairn/config.py` | Four `COUCHDB_*` fields + `CAIRN_COUCHDB_ENABLED` toggle |
| `cairn/api/routes/promotions.py` | Inject client, unpack `WriteResult`, log sync status |
| `cairn/main.py` | Lifespan startup ping and shutdown close |

---

## Testing Strategy

### Unit Tests (CouchDBVaultClient)
- `put_note()` creates a new document → expect 201 and correct document body
- `put_note()` updates existing document → fetches `_rev`, issues PUT with rev
- `put_note()` handles 409 conflict → refetches `_rev` and retries once
- `put_note()` returns `success=False` on connection error, 5xx, auth failure
- `put_note()` chunks content above threshold → verify `children` array and
  child document PUTs
- `put_note()` ctime/mtime encoded as integer milliseconds
- `_id` equals `vault_rel_path` verbatim (no URL encoding)
- `ping()` returns True on 200, False otherwise

Uses `httpx.MockTransport` for all network mocking — no real CouchDB needed
for unit tests.

### Integration Tests (Vault Writer Dual-Write)
- Disk write succeeds + CouchDB unavailable → `couchdb_synced=False`,
  file on disk, no exception raised
- Disk write succeeds + CouchDB succeeds → `couchdb_synced=True`,
  file on disk, document in mock CouchDB
- Second promotion for same entity → existing CouchDB doc updated (revision
  advances), disk file appended
- `couchdb_enabled=False` → `couchdb_synced=False`, no HTTP calls made
- `couchdb_user=""` → client not constructed, no HTTP calls made

### Manual Verification
- Start full stack via `docker compose up`; run a promotion; confirm the
  document appears in `http://localhost:5984/_utils` under
  `obsidian-livesync` database
- Open Obsidian with LiveSync configured to the same database; confirm the
  note appears in the vault within 60 seconds (US-1 success criterion)
- Edit the note in Obsidian; run a second promotion on the same entity;
  confirm the edit survives as a CouchDB conflict revision (US-3)

---

## Performance & Reliability

**Latency budget (NFR-1):**
- Disk write: < 50ms (unchanged)
- CouchDB PUT: target < 500ms p95, 2s hard ceiling
- Total promotion latency impact: < 2s additional

**Fault tolerance (NFR-2):**
- CouchDB unreachable at startup → logged warning, startup continues
- CouchDB unreachable per-write → disk write succeeds, `WriteResult` reflects
  failure, promotion endpoint returns success
- Connection reuse: single `httpx.AsyncClient` instance held by
  `CouchDBVaultClient` for the process lifetime; no per-request connection
  cost after warmup

**No LiveSync plugin dependency (NFR-3):**
- All writes use vanilla CouchDB REST (`PUT /{db}/{doc}`, `GET /{db}/{doc}`)
- Basic Auth via `httpx`; no LiveSync-specific headers

---

## Open Considerations

- **LiveSync conflict handling (US-3):** Initial implementation relies on
  CouchDB's native `_conflicts` — Cairn's write becomes a conflict revision
  rather than overwriting. Full merge semantics are out of scope for Phase
  4.4 and may warrant a Phase 4.5 follow-up.
- **Deletion:** Out of scope per requirements — Cairn never deletes
  promoted notes today.
- **Backfill:** Existing on-disk notes written before Phase 4.4 are not
  automatically pushed to CouchDB. A one-shot admin command
  (`cairn-admin vault-sync-backfill`) is deferred to a follow-up task if
  demand arises.
