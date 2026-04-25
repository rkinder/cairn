# Phase 4.6 Implementation Tasks

## Phase 1: Database & Configuration Re-architecture (Week 1)

### Task 1.1: Database Schema Migration
**Estimate:** 1 hour  
**Priority:** High

- [ ] Create `cairn/db/schema/migrations/005_rename_vault_path.sql`
- [ ] Rename `vault_path` to `kb_path` in `messages` and `promotion_candidates` tables.
- [ ] Update `cairn.models.message` and `cairn.models.promotion` to reflect `kb_path`.
- [ ] Ensure SQLite migration logic handles backwards compatibility smoothly.

**Files to modify/create:**
- `cairn/db/schema/migrations/005_rename_vault_path.sql`
- `cairn/models/message.py`
- `cairn/models/promotion.py`

### Task 1.2: Refactor Configuration
**Estimate:** 1 hour  
**Priority:** High

- [ ] Rename `cairn.vault.writer` to `cairn.kb.writer` (or alias it).
- [ ] Add `quartz_content_dir` and `quartz_sync_cmd` to `Settings` (`cairn/core/settings.py`).
- [ ] Deprecate `CAIRN_VAULT_DIR` with a startup warning.

**Files to modify:**
- `cairn/core/settings.py`
- `cairn/vault/writer.py` (Rename to `cairn/kb/writer.py`)
- `cairn/api/routes/promotions.py` (Import paths)
- `.env.example`

## Phase 2: Sequential Queue Integration (Week 1)

### Task 2.1: Async Worker & Debounce Queue
**Estimate:** 2 hours  
**Priority:** High

- [ ] Create `cairn/kb/sync_worker.py` managing the `asyncio.Queue` and debounce loop.
- [ ] Parse `CAIRN_QUARTZ_SYNC_CMD` and execute via `asyncio.create_subprocess_shell`.
- [ ] Tie the worker to the FastAPI lifespan/startup event.
- [ ] Add signal `put_nowait()` to `cairn.kb.writer` when notes are successfully created.

**Files to create/modify:**
- `cairn/kb/sync_worker.py`
- `cairn/kb/writer.py`
- `cairn/api/app.py`

### Task 2.2: Quartz Compatibility & Concurrency Tests
**Estimate:** 1.5 hours  
**Priority:** High

- [ ] Add property-based tests verifying the queue effectively debounces concurrent sync requests into a single subprocess execution.
- [ ] Validate the worker safely handles failed exit codes without crashing the event loop.

**Files to create:**
- `tests/test_kb_sync_worker.py`

## Phase 3: Documentation and Roadmap (Week 1)

### Task 3.1: Future Enhancements Documentation
**Estimate:** 1 hour  
**Priority:** Medium

- [ ] Create `ROADMAP.md` covering Phase 5 enhancements (Enterprise scale, Postgres, Multi-agent, TUI).
- [ ] Update `CLAUDE.md` to indicate the architectural pivot from Obsidian to Quartz.
- [ ] Update `README.md` to reference Quartz as the static site generator.

**Files to modify/create:**
- `ROADMAP.md`
- `CLAUDE.md`
- `README.md`

## Testing and Quality Assurance

### Task QA.1: Integration Validation
**Estimate:** 1 hour  
**Priority:** High

- [ ] Run the full existing test suite (`uv run pytest -q`) to ensure renaming `vault_path` to `kb_path` does not break Phase 4.1-4.5 workflows or ChromaDB searches.

## Deployment and Rollout

### Task D.1: Environment Variable Migration
**Estimate:** 0.5 hours  
**Priority:** Low

- [ ] Add deprecation warning for `CAIRN_VAULT_DIR`.
- [ ] Provide example values in `.env.example` for `npx quartz sync`.

## Risk Mitigation
### High-Risk Tasks
- **Task 2.1** - Asyncio Queue state must not deadlock the FastAPI event loop during shutdown.
  - *Mitigation:* Ensure `sync_worker` receives a `CancelledError` handling loop during app shutdown so it exits cleanly.
- **Task 1.1** - SQLite `ALTER TABLE RENAME COLUMN` works in modern SQLite but could fail on older versions or if the tables have complex constraints.
  - *Mitigation:* The migration script must be tested against the live test database to confirm compatibility.

## Success Criteria
### Functional
- [ ] Notes are successfully written to `CAIRN_QUARTZ_CONTENT_DIR`.
- [ ] Multiple immediate promotions result in a debounced, single sequential execution of the sync command in the background.

### Performance
- [ ] Promotion HTTP requests remain non-blocking and return instantly, regardless of the underlying GitOps queue size.

## Estimated Total Effort
**Total:** 8 hours (~1 person-day)
**Critical Path:** 1 week with 1 developer

## Phase 4: Installation Documentation (Week 1)

### Task 4.1: Quartz 4 Setup Instructions
**Estimate:** 1 hour  
**Priority:** High

- [ ] Remove all existing documentation referencing Obsidian and the SelfHosted-LiveSync plugin from the setup guides.
- [ ] Add step-by-step instructions for initializing a Quartz 4 repository (`npx quartz create`).
- [ ] Document how to configure Cairn (`CAIRN_QUARTZ_CONTENT_DIR` and `CAIRN_QUARTZ_SYNC_CMD`) to point to the newly created Quartz repository.
- [ ] Document how to run the development server (`npx quartz build --serve`) alongside Cairn.

**Files to modify:**
- `README.md`
- `docs/setup.md` (or equivalent installation guide)
