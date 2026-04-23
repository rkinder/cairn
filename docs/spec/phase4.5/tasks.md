# Phase 4.5 — Procedural Methodology Ingestion — Tasks

## Overview

3-phase implementation, 36 hours total. Each task has an estimate, priority, objectives (checkboxes), files to create/modify, implementation steps, test cases, and acceptance criteria.

**Implementation order** (matches design doc §Implementation Order):
1. `cairn/models/methodology.py` — model first; everything depends on it
2. `cairn/nlp/step_extractor.py` — pure function, no dependencies
3. `cairn/cli/validate_procedures.py` + `gitlab-ci/procedure-validate.yml` — FR-2 complete
4. `cairn/sync/chroma_sync.py` — add `sync_procedures()`, `_procedure_doc_id()`, update `search_methodologies()`
5. `cairn/vault/writer.py` — add `write_procedure()` and `_build_procedure_note()`
6. `cairn/api/routes/promotions.py` — add `methodology_kind` to `PromoteRequest`; branch in `promote_candidate`
7. `cairn/api/routes/methodologies.py` — add `kind` to response model and query param
8. Unit tests, then integration tests

---

## Phase 1: MVP — GitLab Route (Week 1, ~12 hours)

### Task 1.1: ProcedureMethodology Pydantic Model (FR-1)

- **Estimate:** 2h
- **Priority:** Critical

#### Objectives
- [ ] Create `cairn/models/methodology.py` with `MethodologyKind` enum and `ProcedureMethodology` model
- [ ] Create `cairn/models/__init__.py` if it does not exist
- [ ] Create `tests/test_procedure_model.py` with 8+ tests

#### Files to Create
- `cairn/models/__init__.py` (if absent)
- `cairn/models/methodology.py`
- `tests/test_procedure_model.py`

#### Implementation Steps
1. Define `MethodologyKind(str, Enum)` with members `sigma = "sigma"` and `procedure = "procedure"`.
2. Define `ProcedureMethodology(BaseModel)` with fields matching the design doc:
   - `title: str = Field(...)`
   - `tags: list[str] = Field(...)`
   - `steps: list[str] = Field(..., min_length=2)`
   - `description: str | None = Field(default=None)`
   - `references: list[str] = Field(default_factory=list)`
   - `author: str | None = Field(default=None)`
   - `severity: Literal["low", "medium", "high", "critical"] | None = Field(default=None)`
3. Define `SigmaMethodology(BaseModel)` stub (title, tags, description, status fields) as specified in the design doc.
4. Write `tests/test_procedure_model.py` covering the test cases below.

#### Test Cases (`tests/test_procedure_model.py`)
| Test | Assertion |
|---|---|
| `test_valid_model` | Full valid dict parses without error |
| `test_missing_title_fails` | `ValidationError` raised when `title` absent |
| `test_empty_steps_fails` | `ValidationError` raised when `steps=[]` (min_length=2) |
| `test_single_step_fails` | `ValidationError` raised when `steps` has one element |
| `test_optional_fields_default` | `description`, `author`, `severity` are `None` when absent |
| `test_references_default_empty` | `references` defaults to `[]` |
| `test_severity_enum_invalid` | `ValidationError` for severity value not in Literal |
| `test_severity_enum_valid` | All four severity values parse correctly |

#### Acceptance Criteria
- `ProcedureMethodology.model_validate(valid_dict)` succeeds for a well-formed dict.
- `ValidationError` raised for missing `title`, `steps` with fewer than 2 elements, and invalid `severity`.
- 8+ tests passing.

---

### Task 1.2: CLI Procedure Validator (FR-2)

- **Estimate:** 2h
- **Priority:** Critical

#### Objectives
- [ ] Create `cairn/cli/validate_procedures.py` — walks a directory, loads YAML, validates via `ProcedureMethodology`, prints results, exits 0 or 1
- [ ] Create `cairn/cli/__init__.py` if it does not exist
- [ ] Add `procedure-validate = "cairn.cli.validate_procedures:main"` to `[project.scripts]` in `pyproject.toml`
- [ ] Create `tests/test_validate_procedures_cli.py` with 5+ tests

#### Files to Modify/Create
- `cairn/cli/__init__.py` (if absent)
- `cairn/cli/validate_procedures.py`
- `pyproject.toml` (`[project.scripts]` section)
- `tests/test_validate_procedures_cli.py`

#### Implementation Steps
1. Implement `validate_directory(procedures_dir: Path) -> int` — globs `*.procedure.yml`, calls `ProcedureMethodology.model_validate()` on each, prints `OK <name>` or `FAIL <name>: <error>`, returns failure count.
2. Implement `main()` — reads `sys.argv[1]`, calls `validate_directory()`, exits with code `1 if failed else 0`; exits 1 with usage message if no arg provided.
3. Add entry point in `pyproject.toml`.
4. Write tests using `subprocess.run` or `importlib` to invoke `main()` with a temp directory.

#### Test Cases (`tests/test_validate_procedures_cli.py`)
| Test | Assertion |
|---|---|
| `test_valid_dir_exits_0` | Temp dir with one valid `.procedure.yml` exits 0 |
| `test_invalid_file_exits_1` | Temp dir with one invalid file (missing `title`) exits 1 |
| `test_error_names_field` | stderr output from invalid file names the failing field |
| `test_empty_dir_exits_0` | Empty temp dir exits 0 |
| `test_missing_dir_exits_1` | Non-existent path exits 1 |

#### Acceptance Criteria
- `python -m cairn.cli.validate_procedures <dir>` runs without import errors.
- Exit code 0 on all-valid or empty directory; exit code 1 on any failure or missing directory.
- 5+ tests passing.

---

### Task 1.3: GitLab CI Procedure Lint Job (FR-2)

- **Estimate:** 1h
- **Priority:** High

#### Objectives
- [ ] Create `gitlab-ci/procedure-validate.yml` mirroring the Sigma validate job pattern
- [ ] Create `methodologies/procedures/example.procedure.yml` template (phishing triage example from design doc)

#### Files to Create
- `gitlab-ci/procedure-validate.yml`
- `methodologies/procedures/example.procedure.yml`

#### Implementation Steps
1. Write `procedure-validate.yml` with stage `validate`, image `python:3.12-slim`, script installing `pyyaml pydantic` and `-e .`, then running `python -m cairn.cli.validate_procedures methodologies/procedures/`.
2. Add `rules:` block triggering only on changes to `methodologies/procedures/**/*.procedure.yml`.
3. Write `example.procedure.yml` using the phishing triage example from the design doc (title, description, tags, author, severity, 6 steps, references).

#### Test Cases
No unit tests. Manual verification:
- Push a valid `.procedure.yml` to the methodology repo → CI job passes.
- Push an invalid file (missing `title`) → CI job fails with the field name in the log.

#### Acceptance Criteria
- CI job definition is syntactically valid YAML.
- `example.procedure.yml` passes `cairn.cli.validate_procedures` locally.

---

### Task 1.4: `sync_procedures()` in `chroma_sync.py` (FR-3, FR-7)

- **Estimate:** 3h
- **Priority:** Critical

#### Objectives
- [ ] Add `sync_procedures(collection, procedures_dir: Path) -> tuple[int, int]` to `cairn/sync/chroma_sync.py`
- [ ] Add `_procedure_doc_id(title: str, filepath: str) -> str` helper (sha256-based)
- [ ] Update `search_methodologies()` signature to accept `where: dict | None = None` and pass it to ChromaDB
- [ ] Wire `sync_procedures()` into the existing APScheduler job alongside `sync_sigma_rules()`
- [ ] Create `tests/test_chroma_sync_procedures.py` with 8+ tests

#### Files to Modify
- `cairn/sync/chroma_sync.py`
- `tests/test_chroma_sync_procedures.py`

#### Implementation Steps
1. Add `import hashlib` at top of `chroma_sync.py` if not present.
2. Implement `_procedure_doc_id(title, filepath)` — sha256 of `f"{title}\x00{filepath}"` hex digest.
3. Implement `sync_procedures(collection, procedures_dir)`:
   - Glob `*.procedure.yml` files sorted.
   - Per-file: `yaml.safe_load` → `ProcedureMethodology.model_validate` → build embedding document (`title + description + numbered steps`) → build metadata dict with `kind="procedure"` → `collection.upsert(ids=[doc_id], documents=[document], metadatas=[metadata])`.
   - Per-file try/except: log warning and increment `failed` on any exception.
   - Log summary; return `(synced, failed)` tuple.
4. Update `search_methodologies(collection, query, n, where=None)` — pass `where` kwarg to `collection.query()`; add `kind` field to output dicts defaulting to `"sigma"` for backward compatibility.
5. Extend the scheduler job: resolve `procedures_dir = Path(settings.gitlab_methodology_dir) / "procedures"`; call `sync_procedures(collection, procedures_dir)` if dir exists.
6. Write tests using `unittest.mock.MagicMock` for the ChromaDB collection.

#### Test Cases (`tests/test_chroma_sync_procedures.py`)
| Test | Assertion |
|---|---|
| `test_valid_file_ingested` | Valid `.procedure.yml` causes `collection.upsert` called once with correct args |
| `test_invalid_file_skipped_counted` | Invalid YAML returns `(0, 1)` and upsert not called |
| `test_metadata_has_kind_procedure` | `metadata["kind"] == "procedure"` in upsert call |
| `test_metadata_tags_csv` | Tags stored as comma-separated string |
| `test_doc_id_stable` | Same title + path produces identical doc_id across two calls |
| `test_empty_dir_returns_zero_zero` | `(0, 0)` returned for empty directory |
| `test_multiple_files_all_valid` | 3 valid files returns `(3, 0)`, upsert called 3 times |
| `test_one_invalid_in_batch` | 5 files with 1 invalid returns `(4, 1)` |

#### Acceptance Criteria
- `sync_procedures()` returns `(synced, failed)` counts.
- ChromaDB `upsert` called with `kind="procedure"` in metadata.
- `_procedure_doc_id()` is deterministic (same inputs → same output).
- `search_methodologies()` with `where={"kind": "procedure"}` passes filter to ChromaDB.
- 8+ tests passing.

---

### Task 1.5: `kind` Filter on Search Endpoint (FR-6)

- **Estimate:** 2h
- **Priority:** High

#### Objectives
- [ ] Add `kind: str` field to `MethodologySearchResult` in `cairn/api/routes/methodologies.py`
- [ ] Add `kind: Optional[Literal["sigma", "procedure", "any"]] = Query(default=None)` parameter to `search_methodologies_endpoint`
- [ ] Pass `where={"kind": kind}` to `search_methodologies()` when kind is not None and not `"any"`
- [ ] Create `tests/test_methodologies_kind_filter.py` with 5+ tests

#### Files to Modify
- `cairn/api/routes/methodologies.py`
- `tests/test_methodologies_kind_filter.py`

#### Implementation Steps
1. Add `kind: str = Field(default="sigma", ...)` to `MethodologySearchResult`.
2. Add `kind` query parameter with `Literal["sigma", "procedure", "any"] | None` annotation.
3. Compute `where = {"kind": kind} if (kind and kind != "any") else None`.
4. Call `search_methodologies(collection, q, n=n, where=where)`.
5. Map `r.get("kind", "sigma")` into each `MethodologySearchResult`.
6. Write tests using a mock ChromaDB collection that returns seeded metadata.

#### Test Cases (`tests/test_methodologies_kind_filter.py`)
| Test | Assertion |
|---|---|
| `test_kind_sigma_excludes_procedures` | `kind=sigma` passes `where={"kind": "sigma"}` to search |
| `test_kind_procedure_excludes_sigma` | `kind=procedure` passes `where={"kind": "procedure"}` to search |
| `test_kind_any_passes_no_filter` | `kind=any` passes `where=None` to search |
| `test_kind_omitted_passes_no_filter` | Omitting `kind` passes `where=None` (backward compat) |
| `test_kind_field_in_response` | Response items include `kind` field |
| `test_invalid_kind_returns_422` | `kind=unknown` returns HTTP 422 |

#### Acceptance Criteria
- Existing callers that omit `kind` get the same results as before (no filter applied).
- `kind=procedure` filters out sigma results.
- Each result includes `kind` field.
- 5+ tests passing (422 test counts).

---

### Task 1.6: Phase 1 Integration Test

- **Estimate:** 2h
- **Priority:** High

#### Objectives
- [ ] Create `tests/integration/test_phase45_route_a.py` with 3+ integration tests
- [ ] Verify end-to-end Route A: write `.procedure.yml` → `sync_procedures()` → ChromaDB query returns result with `kind=procedure`

#### Files to Create
- `tests/integration/test_phase45_route_a.py`

#### Test Cases
| Test | Assertion |
|---|---|
| `test_sync_and_query_roundtrip` | Write valid `.procedure.yml` to temp dir → call `sync_procedures()` → `search_methodologies(q=title, where={"kind": "procedure"})` returns matching result |
| `test_kind_field_on_result` | Result from above has `kind="procedure"` |
| `test_invalid_file_not_in_results` | Write invalid file alongside valid → only valid file found in ChromaDB |

#### Acceptance Criteria
- End-to-end Route A produces a queryable ChromaDB document.
- 3+ integration tests passing with a real ChromaDB instance (or `chromadb.EphemeralClient()`).

---

## Phase 2: Blackboard Promotion Route (Week 2, ~14 hours)

### Task 2.1: `extract_steps()` in `step_extractor.py` (FR-5)

- **Estimate:** 3h
- **Priority:** High

#### Objectives
- [ ] Create `cairn/nlp/step_extractor.py` with `extract_steps(text: str) -> list[str]`
- [ ] Implement three-tier heuristic: numbered list → bulleted list → sentence split
- [ ] Filter steps shorter than 10 characters
- [ ] Create `tests/test_step_extractor.py` with 10+ tests

#### Files to Create
- `cairn/nlp/step_extractor.py`
- `tests/test_step_extractor.py`

#### Implementation Steps
1. Define `_RE_NUMBERED = re.compile(r"^\d+[\.\)]\s+(.+)$", re.MULTILINE)`.
2. Define `_RE_BULLETED = re.compile(r"^[-*\u2022]\s+(.+)$", re.MULTILINE)`.
3. Define `_MIN_STEP_LEN = 10`.
4. Implement `_filter_short(steps)` — strips and filters steps with `len < _MIN_STEP_LEN`.
5. Implement `extract_steps(text)`:
   - Return `[]` for empty/whitespace-only input.
   - Try numbered regex; if matches, return `_filter_short(matches)`.
   - Try bulleted regex; if matches, return `_filter_short(matches)`.
   - Fall back to `text.split(". ")` sentence split with strip + filter.

#### Test Cases (`tests/test_step_extractor.py`)
| Test | Assertion |
|---|---|
| `test_numbered_list_dot` | `"1. Step one\n2. Step two"` returns `["Step one", "Step two"]` |
| `test_numbered_list_paren` | `"1) Step one\n2) Step two"` returns 2 steps |
| `test_bulleted_dash` | `"- Step one\n- Step two"` returns 2 steps |
| `test_bulleted_star` | `"* Step one\n* Step two"` returns 2 steps |
| `test_bulleted_unicode` | Unicode bullet (`\u2022`) lines return 2 steps |
| `test_sentence_split_fallback` | Plain prose with `. ` separators splits correctly |
| `test_short_steps_filtered` | Steps under 10 chars are excluded from result |
| `test_numbered_priority_over_bulleted` | Text with both numbered and bulleted lines returns numbered result |
| `test_empty_string_returns_empty` | `""` returns `[]` |
| `test_whitespace_only_returns_empty` | `"   "` returns `[]` |
| `test_single_sentence_returns_list` | Single sentence with no `. ` returns list of one element |

#### Acceptance Criteria
- All three priority tiers exercised by tests.
- Steps shorter than 10 chars never appear in output.
- 10+ tests passing.

---

### Task 2.2: `methodology_kind` Field on `PromoteRequest` (FR-4)

- **Estimate:** 1h
- **Priority:** High

#### Objectives
- [ ] Add `methodology_kind: Optional[Literal["sigma", "procedure"]] = None` to `PromoteRequest` in `cairn/api/routes/promotions.py`
- [ ] Verify existing promotion tests still pass
- [ ] Add 2+ new tests for the new field

#### Files to Modify
- `cairn/api/routes/promotions.py`

#### Implementation Steps
1. Add `from typing import Literal` import if not present.
2. Add `methodology_kind: Literal["sigma", "procedure"] | None = Field(default=None, description="...")` to `PromoteRequest`.
3. Run existing promotion tests to confirm backward compatibility.

#### Test Cases (add to existing promotion test file or new file)
| Test | Assertion |
|---|---|
| `test_promote_request_methodology_kind_optional` | `PromoteRequest(narrative="x")` instantiates without `methodology_kind` |
| `test_promote_request_methodology_kind_accepted` | `PromoteRequest(methodology_kind="procedure")` validates correctly |
| `test_promote_request_invalid_kind_rejected` | `methodology_kind="unknown"` raises `ValidationError` |

#### Acceptance Criteria
- Backward compatible — `methodology_kind` absent behaves identically to before.
- 2+ new tests passing, all existing promotion tests still green.

---

### Task 2.3: `write_procedure()` in `vault/writer.py` (FR-4)

- **Estimate:** 4h
- **Priority:** High

#### Objectives
- [ ] Add `async def write_procedure(vault_root, *, title, steps, tags, narrative, source_message_ids, promoted_at, author, severity, low_confidence, couchdb_client) -> WriteResult` to `cairn/vault/writer.py`
- [ ] Add `_build_procedure_note(...)` private helper
- [ ] Routes procedure notes to `cairn/procedures/` subdirectory
- [ ] Best-effort CouchDB dual-write (same pattern as `write_note()`)
- [ ] Create `tests/test_vault_writer_procedure.py` with 8+ tests

#### Files to Modify/Create
- `cairn/vault/writer.py`
- `tests/test_vault_writer_procedure.py`

#### Implementation Steps
1. Add `write_procedure()` signature matching the design doc exactly (keyword-only args after `vault_root`).
2. In `write_procedure()`:
   - Compute `target_dir = vault_root / _CAIRN_SUBDIR / "procedures"` and `mkdir(parents=True, exist_ok=True)`.
   - Compute `safe_name = _safe_filename(title)` and `note_file = target_dir / f"{safe_name}.md"`.
   - If file exists: call `_update_existing_note()` (same pattern as `write_note()`).
   - If new: call `_build_procedure_note(...)` and write to disk.
   - CouchDB best-effort sync block (same structure as `write_note()`) — failure logs warning, does not raise.
   - Return `WriteResult(vault_rel=..., couchdb_synced=..., couchdb_error=...)`.
3. Implement `_build_procedure_note()` producing YAML frontmatter with `procedure_source: blackboard`, `low_confidence: true` when applicable, `## Summary`, `## Steps` (numbered), `## Evidence` sections.
4. Write tests using `tmp_path` fixture (pytest) for vault root; mock `CouchDBVaultClient`.

#### Test Cases (`tests/test_vault_writer_procedure.py`)
| Test | Assertion |
|---|---|
| `test_numbered_body_extracts_steps` | Body with numbered list produces note with `## Steps` containing those steps |
| `test_prose_body_low_confidence` | Plain prose body produces note with `low_confidence: true` in frontmatter |
| `test_vault_note_has_procedure_source` | Frontmatter contains `procedure_source: blackboard` |
| `test_note_routed_to_procedures_subdir` | `vault_rel` contains `procedures/` path segment |
| `test_couchdb_failure_does_not_block` | `put_note` raising an exception still returns `WriteResult` with success from disk write |
| `test_write_result_success_on_disk_write` | `WriteResult.success` is truthy after successful disk write |
| `test_deduplication_updates_existing` | Second call with same title updates the file, does not create a duplicate |
| `test_tags_in_frontmatter` | Provided tags appear in YAML frontmatter alongside `procedure` and `cairn-promoted` |

#### Acceptance Criteria
- `write_procedure()` produces a valid markdown file under `cairn/procedures/`.
- CouchDB failure does not prevent vault write.
- 8+ tests passing.

---

### Task 2.4: Wire Promotion Route to `write_procedure()` (FR-4)

- **Estimate:** 2h
- **Priority:** High

#### Objectives
- [ ] Modify `promote_candidate` handler in `cairn/api/routes/promotions.py` to branch on `methodology_kind == "procedure"`
- [ ] Call `write_procedure()` instead of `write_note()` when kind is `"procedure"`
- [ ] Enqueue best-effort ChromaDB upsert with `kind="procedure"` after vault write
- [ ] Create `tests/integration/test_phase45_route_c.py` with 4+ tests

#### Files to Modify/Create
- `cairn/api/routes/promotions.py`
- `tests/integration/test_phase45_route_c.py`

#### Implementation Steps
1. Import `extract_steps` from `cairn.nlp.step_extractor` and `write_procedure` from `cairn.vault.writer` at the top of the handler file.
2. After fetching `source_findings`, add conditional:
   - `if body.methodology_kind == "procedure":` — call `extract_steps()`, compute `low_conf = len(steps) < 2`, call `write_procedure(settings.vault_path, title=entity[:60], steps=steps, ...)`.
   - `else:` — call `write_note()` as before.
3. After `write_procedure()` returns, add best-effort ChromaDB upsert block using `_procedure_doc_id()` from `chroma_sync` (try/except, log warning on failure).
4. Write integration tests using `httpx.AsyncClient` against the FastAPI app with mocked vault writer and ChromaDB.

#### Test Cases (`tests/integration/test_phase45_route_c.py`)
| Test | Assertion |
|---|---|
| `test_promote_procedure_kind_calls_write_procedure` | `POST /promotions/{id}/promote` with `methodology_kind=procedure` calls `write_procedure` |
| `test_vault_rel_contains_procedures_path` | Response `vault_rel` includes `procedures/` |
| `test_nil_methodology_kind_calls_write_note` | `methodology_kind=None` follows original `write_note()` path |
| `test_chroma_upsert_attempted_for_procedure` | ChromaDB upsert attempted with `kind="procedure"` in metadata |

#### Acceptance Criteria
- Route C conditional branch works without breaking existing promotion flow.
- 4+ integration tests passing.

---

### Task 2.5: Phase 2 End-to-End Integration Test

- **Estimate:** 2h
- **Priority:** High

#### Objectives
- [ ] Write 3+ end-to-end tests covering the full Route C pipeline in `tests/integration/test_phase45_route_c.py`

#### Additional Test Cases (append to `test_phase45_route_c.py`)
| Test | Scenario |
|---|---|
| `test_full_route_c_numbered_steps` | Message with numbered steps → `POST /promotions/{id}/promote` with `methodology_kind=procedure` → vault note written → ChromaDB upsert called → upsert document contains step text |
| `test_full_route_c_plain_prose_low_confidence` | Plain prose body → promote → vault note has `low_confidence: true` in frontmatter |
| `test_chroma_query_returns_promoted_procedure` | After promotion, `search_methodologies(kind="procedure")` returns the promoted document |

#### Acceptance Criteria
- 3+ end-to-end scenarios passing with real file I/O (tmp_path) and mocked ChromaDB/CouchDB.

---

### Task 2.6: spaCy Gate (NFR-1, FR-5)

- **Estimate:** 2h
- **Priority:** Low

#### Objectives
- [ ] Add `CAIRN_SPACY_ENABLED` setting to `cairn/config.py` (default: `False`)
- [ ] Add optional spaCy sentence boundary detection as tertiary fallback in `step_extractor.py` (when enabled and spaCy available)
- [ ] Add `spacy` as optional dependency in `pyproject.toml` under `[project.optional-dependencies]` → `nlp = ["spacy>=3.0"]`
- [ ] Update `.env.example` with `CAIRN_SPACY_ENABLED=false`

#### Files to Modify
- `cairn/config.py`
- `cairn/nlp/step_extractor.py`
- `pyproject.toml`
- `.env.example`

#### Implementation Steps
1. Add `spacy_enabled: bool = Field(default=False, alias="CAIRN_SPACY_ENABLED")` to `Settings`.
2. In `step_extractor.py` sentence-split tier: if `settings.spacy_enabled` and `spacy` importable, use spaCy `nlp(text).sents` instead of `text.split(". ")`.
3. Guard the import with `try/except ImportError` — `CAIRN_SPACY_ENABLED=true` with spaCy absent logs a warning and falls back to naive split.
4. Add `nlp = ["spacy>=3.0"]` under `[project.optional-dependencies]`.

#### Test Cases (add to `tests/test_step_extractor.py`)
| Test | Assertion |
|---|---|
| `test_spacy_disabled_uses_naive_split` | With `CAIRN_SPACY_ENABLED=false`, naive `. ` split is used |
| `test_spacy_unavailable_falls_back` | When spaCy raises `ImportError`, naive split is used without error |

#### Acceptance Criteria
- `extract_steps()` behaves identically with and without spaCy installed.
- 2+ new tests passing.

---

## Phase 3: Skill Client + Docs (Week 3, ~10 hours)

### Task 3.1: Agent Skill Client — `kind` Filter (US-2)

- **Estimate:** 3h
- **Priority:** High

#### Objectives
- [ ] Modify `find_methodology()` in `cairn/skill/client.py` to accept `kind: Optional[str] = None`
- [ ] Pass `kind` as query string parameter to `GET /methodologies/search` when not None
- [ ] Add `kind: str` field to `MethodologyRef` dataclass
- [ ] Create `tests/test_skill_client_methodology.py` with 4+ tests

#### Files to Modify/Create
- `cairn/skill/client.py`
- `tests/test_skill_client_methodology.py`

#### Implementation Steps
1. Update `MethodologyRef` dataclass (or equivalent) to include `kind: str = "sigma"`.
2. In `find_methodology(query, n, kind=None)`: build `params = {"q": query, "n": n}`; add `params["kind"] = kind` if kind is not None.
3. Parse `kind` from each API response item into `MethodologyRef.kind`.
4. Write tests using `responses` library or `unittest.mock.patch` to mock the HTTP call.

#### Test Cases (`tests/test_skill_client_methodology.py`)
| Test | Assertion |
|---|---|
| `test_kind_procedure_in_query_string` | `find_methodology("x", kind="procedure")` sends `?kind=procedure` |
| `test_kind_none_omitted_from_query` | `find_methodology("x")` does not include `kind` in query string |
| `test_methodology_ref_has_kind_field` | Parsed `MethodologyRef` includes `kind` from response |
| `test_procedure_result_parsed_correctly` | Response with `kind="procedure"` populates `MethodologyRef.kind` correctly |

#### Acceptance Criteria
- `kind=None` does not change existing behavior.
- `MethodologyRef` carries `kind` from API response.
- 4+ tests passing.

---

### Task 3.2: Procedure Template and Docs (US-1)

- **Estimate:** 2h
- **Priority:** Medium

#### Objectives
- [ ] Confirm `methodologies/procedures/example.procedure.yml` exists (created in Task 1.3) and passes CI lint
- [ ] Create `docs/phase4.5.md` — endpoint reference, procedure authoring guide, Route A vs Route C comparison, step format tips
- [ ] Add brief mention of procedure methodology support to `README.md` under Phase 4.5

#### Files to Create/Modify
- `docs/phase4.5.md`
- `README.md`

#### Implementation Steps
1. Write `docs/phase4.5.md` covering:
   - **Endpoint reference**: `GET /methodologies/search?kind=procedure`, `POST /promotions/{id}/promote` with `methodology_kind=procedure`.
   - **Procedure authoring guide**: required fields, step format tips (imperative voice, 10+ chars, start with a verb).
   - **Route A vs Route C**: when to use each, tradeoffs.
   - **spaCy gate**: how to enable, what it changes.
2. Add one-sentence note to `README.md` under the Phase 4.5 entry.

#### Test Cases
No unit tests. Manual review:
- `docs/phase4.5.md` renders correctly in a Markdown viewer.
- All code examples in the doc are syntactically valid.

#### Acceptance Criteria
- Docs are accurate and self-consistent with the implementation.
- `example.procedure.yml` passes `python -m cairn.cli.validate_procedures methodologies/procedures/`.

---

### Task 3.3: Performance Test (NFR-1, NFR-3)

- **Estimate:** 2h
- **Priority:** Medium

#### Objectives
- [ ] Create `tests/performance/test_procedure_sync_perf.py`
- [ ] Assert `sync_procedures()` wall time <= 60s for 100 synthetic files
- [ ] Assert `GET /methodologies/search?kind=procedure` p95 response time <= 200ms over 10 runs

#### Files to Create
- `tests/performance/test_procedure_sync_perf.py`
- `tests/performance/__init__.py` (if absent)

#### Implementation Steps
1. Generate 100 synthetic `.procedure.yml` files in `tmp_path` using a loop (random titles, fixed steps).
2. Call `sync_procedures(collection, tmp_path)` and measure wall time with `time.perf_counter()`.
3. Assert wall time <= 60 seconds.
4. Make 10 sequential `GET /methodologies/search?q=test&kind=procedure` requests via `httpx` against a live test server.
5. Compute p95 latency; assert <= 200ms.

#### Acceptance Criteria
- Both benchmarks pass on standard CI runner hardware.
- Tests are marked with `@pytest.mark.performance` so they can be excluded from the default test run.

---

### Task 3.4: End-to-End Integration Test (all phases)

- **Estimate:** 2h
- **Priority:** High

#### Objectives
- [ ] Add 2 full end-to-end scenarios to `tests/integration/` combining all three phases

#### Files to Create
- `tests/integration/test_phase45_e2e.py`

#### Test Cases
| Test | Scenario |
|---|---|
| `test_route_a_full_pipeline` | Analyst writes `.procedure.yml` → `sync_procedures()` ingests → `find_methodology(kind="procedure")` returns correct result |
| `test_route_c_full_pipeline` | Agent posts message with numbered steps → human calls `POST /promotions/{id}/promote` with `methodology_kind=procedure` → vault note written with `procedure_source: blackboard` → `search_methodologies(kind="procedure")` returns it |

#### Acceptance Criteria
- 2 complete end-to-end scenarios passing.
- Both Route A and Route C covered.

---

### Task 3.5: Final Documentation Pass

- **Estimate:** 1h
- **Priority:** Low

#### Objectives
- [ ] Update `README.md` to mention procedure methodology support under Phase 4.5
- [ ] Confirm `.env.example` contains `CAIRN_SPACY_ENABLED=false`
- [ ] Add CHANGELOG entry for Phase 4.5 covering all three new capabilities

#### Files to Modify
- `README.md`
- `.env.example`
- `CHANGELOG.md` (create if absent)

#### Implementation Steps
1. Scan `README.md` for Phase 4.5 section; add procedure support bullet if absent.
2. Confirm `.env.example` has `CAIRN_SPACY_ENABLED=false`.
3. Add Phase 4.5 CHANGELOG entry: Route A (GitLab sync), Route B (CI linter), Route C (promotion to vault).

#### Acceptance Criteria
- README accurately reflects what Phase 4.5 delivers.
- `.env.example` is complete.
- CHANGELOG is present with a 4.5 entry.

---

## Testing Summary

### Unit Tests (50+ total)

| File | Count |
|---|---|
| `tests/test_procedure_model.py` | 8 |
| `tests/test_validate_procedures_cli.py` | 5 |
| `tests/test_chroma_sync_procedures.py` | 8 |
| `tests/test_methodologies_kind_filter.py` | 6 |
| `tests/test_step_extractor.py` | 11 |
| `tests/test_vault_writer_procedure.py` | 8 |
| `tests/test_skill_client_methodology.py` | 4 |
| **Total** | **50** |

### Integration Tests (17+ total)

| File | Count |
|---|---|
| `tests/integration/test_phase45_route_a.py` | 3 |
| `tests/integration/test_phase45_route_c.py` | 7 |
| `tests/integration/test_methodology_kind_filter.py` | 5 |
| `tests/integration/test_phase45_e2e.py` | 2 |
| **Total** | **17** |

### Performance Tests (not counted in totals)

| File | Benchmarks |
|---|---|
| `tests/performance/test_procedure_sync_perf.py` | 2 |

---

## Success Criteria

### Phase 1 (MVP — GitLab Route)
- ✅ `.procedure.yml` schema validated by CI
- ✅ GitLab procedures appear in ChromaDB with `kind: procedure`
- ✅ `GET /methodologies/search?kind=procedure` returns procedure results only
- ✅ 30+ unit + integration tests passing

### Phase 2 (Promotion Route)
- ✅ `POST /promotions/{id}/promote` with `methodology_kind=procedure` writes vault note with `procedure_source: blackboard`
- ✅ Promoted procedure appears in ChromaDB within one corroboration cycle
- ✅ 40+ tests passing

### Phase 3 (Full Feature)
- ✅ `find_methodology(kind="procedure")` works in agent skill client
- ✅ Both Route A and Route C end-to-end scenarios pass
- ✅ `docs/phase4.5.md` complete
- ✅ 50+ unit tests + 17+ integration tests passing, performance benchmarks green

---

## Timeline

**Week 1 (Phase 1) — 12 hours:**
- Mon–Tue: Tasks 1.1–1.2 (4h)
- Wed: Tasks 1.3–1.4 start (4h)
- Thu–Fri: Tasks 1.4 finish + 1.5 + 1.6 (4h)

**Week 2 (Phase 2) — 14 hours:**
- Mon–Tue: Tasks 2.1–2.2 (4h)
- Wed–Thu: Tasks 2.3–2.4 (6h)
- Fri: Tasks 2.5–2.6 (4h)

**Week 3 (Phase 3) — 10 hours:**
- Mon–Tue: Tasks 3.1–3.2 (5h)
- Wed–Thu: Tasks 3.3–3.4 (4h)
- Fri: Task 3.5 (1h)

**Total: 36 hours over 3 weeks**
