# Phase 4.5 — Procedural Methodology Ingestion — Tasks

## Audit Status (updated 2026-04-23)

This file has been audited against the current repository state and test run (`uv run pytest -q` => 270 passed).

Legend:
- ✅ Completed
- 🟡 Partial / implemented but not to full spec depth
- ❌ Not completed

### Task Summary

| Task | Status | Notes |
|---|---|---|
| 1.1 ProcedureMethodology model | ✅ | `cairn/models/methodology.py`, `tests/test_procedure_model.py` present and passing |
| 1.2 CLI validator | ✅ | `cairn/cli/validate_procedures.py`, script entrypoint, tests present |
| 1.3 CI lint job + example | ✅ | `gitlab-ci/procedure-validate.yml`, `methodologies/procedures/example.procedure.yml` present |
| 1.4 `sync_procedures()` + search where/filter wiring | ✅ | `cairn/sync/chroma_sync.py`, `tests/test_chroma_sync_procedures.py` present |
| 1.5 `kind` filter on methodologies endpoint | ✅ | `cairn/api/routes/methodologies.py`, `tests/test_methodologies_kind_filter.py` present |
| 1.6 Route A integration tests | ❌ | `tests/integration/test_phase45_route_a.py` missing |
| 2.1 `extract_steps()` + tests | ✅ | `cairn/nlp/step_extractor.py`, `tests/test_step_extractor.py` added |
| 2.2 `methodology_kind` on PromoteRequest | ✅ | `cairn/api/routes/promotions.py`, `tests/test_promotions_methodology_kind.py` |
| 2.3 `write_procedure()` + tests | 🟡 | Implemented; tests added, but test count is 7 (spec asked 8+) and one acceptance item references non-existent `WriteResult.success` field |
| 2.4 Promotion route wiring + integration tests | 🟡 | Branching + upsert logic implemented; integration tests exist but are payload-shape/validation focused rather than full mocked call assertions requested |
| 2.5 Route C end-to-end tests | ❌ | Additional 3 e2e scenarios not yet implemented |
| 2.6 spaCy gate | ❌ | config flag + optional dependency + env/tests not implemented |
| 3.1 Skill client kind filter | ❌ | `tests/test_skill_client_methodology.py` missing |
| 3.2 Docs + README mention | ❌ | `docs/phase4.5.md` missing; README update not verified in this audit |
| 3.3 Performance tests | ❌ | `tests/performance/test_procedure_sync_perf.py` missing |
| 3.4 Full e2e (all phases) | ❌ | `tests/integration/test_phase45_e2e.py` missing |
| 3.5 Final docs/changelog pass | ❌ | CHANGELOG/README/.env completion not done to spec |

---

## Phase 1: MVP — GitLab Route (Week 1, ~12 hours)

### Task 1.1: ProcedureMethodology Pydantic Model (FR-1)

- **Estimate:** 2h
- **Priority:** Critical
- **Status:** ✅ Completed

#### Objectives
- [x] Create `cairn/models/methodology.py` with `MethodologyKind` enum and `ProcedureMethodology` model
- [x] Create `cairn/models/__init__.py` if it does not exist
- [x] Create `tests/test_procedure_model.py` with 8+ tests

---

### Task 1.2: CLI Procedure Validator (FR-2)

- **Estimate:** 2h
- **Priority:** Critical
- **Status:** ✅ Completed

#### Objectives
- [x] Create `cairn/cli/validate_procedures.py` — walks a directory, loads YAML, validates via `ProcedureMethodology`, prints results, exits 0 or 1
- [x] Create `cairn/cli/__init__.py` if it does not exist
- [x] Add `procedure-validate = "cairn.cli.validate_procedures:main"` to `[project.scripts]` in `pyproject.toml`
- [x] Create `tests/test_validate_procedures_cli.py` with 5+ tests

---

### Task 1.3: GitLab CI Procedure Lint Job (FR-2)

- **Estimate:** 1h
- **Priority:** High
- **Status:** ✅ Completed

#### Objectives
- [x] Create `gitlab-ci/procedure-validate.yml` mirroring the Sigma validate job pattern
- [x] Create `methodologies/procedures/example.procedure.yml` template (phishing triage example from design doc)

---

### Task 1.4: `sync_procedures()` in `chroma_sync.py` (FR-3, FR-7)

- **Estimate:** 3h
- **Priority:** Critical
- **Status:** ✅ Completed

#### Objectives
- [x] Add `sync_procedures(collection, procedures_dir: Path) -> tuple[int, int]` to `cairn/sync/chroma_sync.py`
- [x] Add `_procedure_doc_id(title: str, filepath: str) -> str` helper (sha256-based)
- [x] Update `search_methodologies()` signature to accept `where: dict | None = None` and pass it to ChromaDB
- [x] Wire `sync_procedures()` into the existing APScheduler job alongside `sync_sigma_rules()`
- [x] Create `tests/test_chroma_sync_procedures.py` with 8+ tests

---

### Task 1.5: `kind` Filter on Search Endpoint (FR-6)

- **Estimate:** 2h
- **Priority:** High
- **Status:** ✅ Completed

#### Objectives
- [x] Add `kind: str` field to `MethodologySearchResult` in `cairn/api/routes/methodologies.py`
- [x] Add `kind: Optional[Literal["sigma", "procedure", "any"]] = Query(default=None)` parameter to `search_methodologies_endpoint`
- [x] Pass `where={"kind": kind}` to `search_methodologies()` when kind is not None and not `"any"`
- [x] Create `tests/test_methodologies_kind_filter.py` with 5+ tests

---

### Task 1.6: Phase 1 Integration Test

- **Estimate:** 2h
- **Priority:** High
- **Status:** ❌ Not completed

#### Objectives
- [ ] Create `tests/integration/test_phase45_route_a.py` with 3+ integration tests
- [ ] Verify end-to-end Route A: write `.procedure.yml` → `sync_procedures()` → ChromaDB query returns result with `kind=procedure`

---

## Phase 2: Blackboard Promotion Route (Week 2, ~14 hours)

### Task 2.1: `extract_steps()` in `step_extractor.py` (FR-5)

- **Estimate:** 3h
- **Priority:** High
- **Status:** ✅ Completed

#### Objectives
- [x] Create `cairn/nlp/step_extractor.py` with `extract_steps(text: str) -> list[str]`
- [x] Implement three-tier heuristic: numbered list → bulleted list → sentence split
- [x] Filter steps shorter than 10 characters
- [x] Create `tests/test_step_extractor.py` with 10+ tests

---

### Task 2.2: `methodology_kind` Field on `PromoteRequest` (FR-4)

- **Estimate:** 1h
- **Priority:** High
- **Status:** ✅ Completed

#### Objectives
- [x] Add `methodology_kind: Optional[Literal["sigma", "procedure"]] = None` to `PromoteRequest` in `cairn/api/routes/promotions.py`
- [x] Verify existing promotion tests still pass
- [x] Add 2+ new tests for the new field

---

### Task 2.3: `write_procedure()` in `vault/writer.py` (FR-4)

- **Estimate:** 4h
- **Priority:** High
- **Status:** 🟡 Partial

#### Objectives
- [x] Add `async def write_procedure(...) -> WriteResult` to `cairn/vault/writer.py`
- [x] Add `_build_procedure_note(...)` private helper
- [x] Routes procedure notes to `cairn/procedures/` subdirectory
- [x] Best-effort CouchDB dual-write (same pattern as `write_note()`)
- [ ] Create `tests/test_vault_writer_procedure.py` with 8+ tests (currently 7 tests)

Notes:
- Implementation is present and working with passing tests.
- Spec acceptance mentions `WriteResult.success`, but current `WriteResult` has fields: `vault_rel`, `couchdb_synced`, `couchdb_error`.

---

### Task 2.4: Wire Promotion Route to `write_procedure()` (FR-4)

- **Estimate:** 2h
- **Priority:** High
- **Status:** 🟡 Partial

#### Objectives
- [x] Modify `promote_candidate` handler in `cairn/api/routes/promotions.py` to branch on `methodology_kind == "procedure"`
- [x] Call `write_procedure()` instead of `write_note()` when kind is `"procedure"`
- [x] Enqueue best-effort ChromaDB upsert with `kind="procedure"` after vault write
- [x] Create `tests/integration/test_phase45_route_c.py` with 4+ tests

Notes:
- Integration tests exist (4), but do not yet fully assert mocked call-path details described in the task’s suggested implementation.

---

### Task 2.5: Phase 2 End-to-End Integration Test

- **Estimate:** 2h
- **Priority:** High
- **Status:** ❌ Not completed

#### Objectives
- [ ] Write 3+ end-to-end tests covering the full Route C pipeline in `tests/integration/test_phase45_route_c.py`

---

### Task 2.6: spaCy Gate (NFR-1, FR-5)

- **Estimate:** 2h
- **Priority:** Low
- **Status:** ❌ Not completed

#### Objectives
- [ ] Add `CAIRN_SPACY_ENABLED` setting to `cairn/config.py` (default: `False`)
- [ ] Add optional spaCy sentence boundary detection as tertiary fallback in `step_extractor.py` (when enabled and spaCy available)
- [ ] Add `spacy` as optional dependency in `pyproject.toml` under `[project.optional-dependencies]` → `nlp = ["spacy>=3.0"]`
- [ ] Update `.env.example` with `CAIRN_SPACY_ENABLED=false`

---

## Phase 3: Skill Client + Docs (Week 3, ~10 hours)

### Task 3.1: Agent Skill Client — `kind` Filter (US-2)

- **Estimate:** 3h
- **Priority:** High
- **Status:** ❌ Not completed

#### Objectives
- [ ] Modify `find_methodology()` in `cairn/skill/client.py` to accept `kind: Optional[str] = None`
- [ ] Pass `kind` as query string parameter to `GET /methodologies/search` when not None
- [ ] Add `kind: str` field to `MethodologyRef` dataclass
- [ ] Create `tests/test_skill_client_methodology.py` with 4+ tests

---

### Task 3.2: Procedure Template and Docs (US-1)

- **Estimate:** 2h
- **Priority:** Medium
- **Status:** ❌ Not completed

#### Objectives
- [ ] Confirm `methodologies/procedures/example.procedure.yml` exists (created in Task 1.3) and passes CI lint
- [ ] Create `docs/phase4.5.md` — endpoint reference, procedure authoring guide, Route A vs Route C comparison, step format tips
- [ ] Add brief mention of procedure methodology support to `README.md` under Phase 4.5

---

### Task 3.3: Performance Test (NFR-1, NFR-3)

- **Estimate:** 2h
- **Priority:** Medium
- **Status:** ❌ Not completed

#### Objectives
- [ ] Create `tests/performance/test_procedure_sync_perf.py`
- [ ] Assert `sync_procedures()` wall time <= 60s for 100 synthetic files
- [ ] Assert `GET /methodologies/search?kind=procedure` p95 response time <= 200ms over 10 runs

---

### Task 3.4: End-to-End Integration Test (all phases)

- **Estimate:** 2h
- **Priority:** High
- **Status:** ❌ Not completed

#### Objectives
- [ ] Add 2 full end-to-end scenarios to `tests/integration/` combining all three phases

---

### Task 3.5: Final Documentation Pass

- **Estimate:** 1h
- **Priority:** Low
- **Status:** ❌ Not completed

#### Objectives
- [ ] Update `README.md` to mention procedure methodology support under Phase 4.5
- [ ] Confirm `.env.example` contains `CAIRN_SPACY_ENABLED=false`
- [ ] Add CHANGELOG entry for Phase 4.5 covering all three new capabilities

---

## Current Validation Snapshot

- Latest run: `uv run pytest -q`
- Result: **270 passed**

This confirms current implemented functionality is test-green, but the unchecked items above remain to fully satisfy the Phase 4.5 task list.
