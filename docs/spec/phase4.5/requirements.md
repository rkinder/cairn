# Phase 4.5 — Procedural Methodology Ingestion — Requirements

## Overview

Phase 4.5 extends Cairn's methodology ingestion pipeline to support procedural methodologies — step-by-step investigation playbooks and response procedures that do not conform to the Sigma rule format. Currently, only Sigma detection rules are validated and ingested into ChromaDB; any methodology expressed as a sequence of human-readable steps has no promotion pathway and is invisible to agent search.

This phase introduces two complementary ingestion routes: **Route A** (GitLab-authored), where analysts write structured `.procedure.yml` files that pass CI validation and are synced to ChromaDB; and **Route C** (blackboard-promoted), where an agent or human reviewer promotes a blackboard message directly as a procedure, bypassing the GitLab repo and writing the result through the existing vault/ChromaDB pipeline. Both routes populate the same `methodology` ChromaDB collection using a `kind: procedure` metadata field that distinguishes procedures from `kind: sigma` rules.

## Problem Statement

Users report that methodologies expressed as ordered investigation steps — "check SPF/DKIM headers, pivot on sending IP, correlate with threat intel, escalate if score > 7" — cannot be promoted into ChromaDB. Only Sigma YAML rules are ingested today. This means agents performing similarity search find no results for procedural queries, forcing analysts to re-explain known playbooks in every investigation session.

**Pain Points:**
- Procedural methodologies authored in GitLab or discovered during live investigations are not searchable by agents via ChromaDB
- No schema or CI validation exists for procedure files, creating inconsistency across manually-written playbooks
- Blackboard promotions that describe investigation steps are written to the Obsidian vault but never ingested into ChromaDB, losing their searchability

## User Stories (EARS Format)

### US-1: GitLab-Authored Procedure Ingestion
**WHEN** an analyst merges a `.procedure.yml` file into the `methodologies/procedures/` directory of the GitLab methodology repo  
**THEN** the system SHALL validate the file structure via CI, embed the concatenated steps as a ChromaDB document with `kind: procedure` metadata, and confirm ingestion in the pipeline log  
**SO THAT** agents can retrieve the procedure via semantic search during future investigations

**Acceptance Criteria:**
- A valid `.procedure.yml` passes CI and appears in ChromaDB within one sync cycle (≤ 15 minutes)
- A malformed file (missing required field) fails CI with a descriptive error message and is not ingested
- The embedded document includes title, tags, and all steps joined as searchable prose

---

### US-2: Agent Procedure Discovery
**WHEN** an agent calls `find_methodology()` with a natural-language query  
**IF** the `kind` filter is set to `procedure` or `any`  
**THEN** the system SHALL return the top-N most semantically similar procedural methodologies with title, tags, steps, and similarity score  
**SO THAT** the agent can follow established steps without re-deriving them from scratch

**Acceptance Criteria:**
- Procedure results are returned alongside or separately from Sigma results depending on `kind` filter
- Each result includes `kind: procedure` in metadata so the agent can distinguish it from Sigma rules
- Query latency ≤ 200ms at p95 for the ChromaDB similarity search step

---

### US-3: Blackboard-Promoted Procedure
**WHEN** a human reviewer promotes a blackboard message and sets `methodology_kind: procedure`  
**THEN** the system SHALL serialize the message body as a `.procedure.yml`-compatible structure, write it to the Obsidian vault, and trigger ChromaDB ingestion  
**SO THAT** emergent procedures discovered during live investigations are captured without requiring a GitLab commit

**Acceptance Criteria:**
- Promoted message appears in ChromaDB `methodology` collection with `kind: procedure` within one corroboration cycle (≤ 15 minutes)
- The vault note includes a `procedure_source: blackboard` frontmatter field to distinguish it from GitLab-authored procedures
- Promotion succeeds even if the message body contains no detected step patterns (free-form prose is accepted)

---

### US-4: CI Validation of Procedure Schema
**WHEN** a GitLab pipeline runs against a branch containing `.procedure.yml` files  
**THEN** the system SHALL validate each file for required fields (`title`, `tags`, `steps`), valid tag format, and minimum step count (≥ 2 steps)  
**SO THAT** analysts catch schema errors before merge rather than discovering silent ingestion failures

**Acceptance Criteria:**
- Missing `title` or `steps` fails CI with exit code 1 and a human-readable error
- Empty `steps` list fails CI
- A single-step procedure triggers a CI warning but does not fail
- Valid files pass CI without warnings

---

### US-5: Kind-Filtered Methodology Search
**WHEN** an agent or API consumer calls `GET /methodologies/search` with a `kind` query parameter  
**THEN** the system SHALL restrict ChromaDB results to documents matching that `kind` value  
**IF** `kind` is omitted, **THEN** the system SHALL return results of all kinds ranked by similarity  
**SO THAT** callers can target the most relevant methodology format for their context

**Acceptance Criteria:**
- `kind=sigma` returns only Sigma rule documents
- `kind=procedure` returns only procedural methodology documents
- `kind=any` or omitted returns mixed results, with `kind` field present on every result
- Invalid `kind` values return HTTP 422 with a list of valid options

---

### US-6: Malformed Procedure Graceful Handling
**WHEN** a `.procedure.yml` file fails CI validation  
**IF** other valid procedure files exist in the same commit  
**THEN** the system SHALL skip only the invalid file and continue ingesting valid files  
**SO THAT** a single malformed file does not block the entire methodology sync

**Acceptance Criteria:**
- A batch of 5 files where 1 is malformed results in 4 ingested, 1 logged error, pipeline exit code 1
- The error log identifies the specific file and field that failed validation

---

## Functional Requirements (EARS Format)

### FR-1: Procedure YAML Schema
**WHEN** the system reads a `.procedure.yml` file  
**THEN** the system SHALL validate it against a schema with the following required fields: `title` (string), `tags` (list of strings), `steps` (list of strings with ≥ 2 entries)  
**WHERE** optional fields include: `description` (string), `references` (list of URLs), `author` (string), `severity` (low | medium | high | critical)

**Implementation:**
- Schema defined as a Pydantic model `ProcedureMethodology` in `cairn/models/methodology.py`
- Validation used by both the CI linter (`gitlab-ci/procedure-validate.yml`) and the sync job (`cairn/sync/chroma_sync.py`)
- YAML parsing uses `PyYAML`; Pydantic handles field validation

*Satisfies US-1, US-4, US-6*

---

### FR-2: GitLab CI Procedure Validator
**WHEN** the GitLab CI pipeline runs against a branch  
**THEN** the system SHALL execute a lint job that loads each `.procedure.yml` under `methodologies/procedures/` and validates it via FR-1  
**WHERE** the job uses the same Python image as `cairn-api` so schema changes stay in sync

**Implementation:**
- New CI job `procedure-lint` in `gitlab-ci/procedure-validate.yml`
- Script: `python -m cairn.cli.validate_procedures methodologies/procedures/`
- Exit code 0 for all-valid, 1 for any validation failure
- Single-step procedures log a warning to stderr but exit 0

*Satisfies US-4, US-6*

---

### FR-3: ChromaDB Ingestion — Procedure Path
**WHEN** the `chroma_sync` job runs  
**THEN** the system SHALL scan `methodologies/procedures/*.procedure.yml` in the local GitLab clone, embed each valid file's concatenated `title + description + steps` as the document text, and upsert into the `methodology` collection with metadata `{kind: "procedure", title, tags, author, severity}`  
**WHERE** document ID is derived from `sha256(title + file_path)` to enable stable upserts

**Implementation:**
- New method `sync_procedures()` in `cairn/sync/chroma_sync.py`
- Called alongside existing `sync_sigma_rules()` in the scheduler job
- Embedding document: `f"{title}\n\n{description}\n\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))`

*Satisfies US-1, US-2*

---

### FR-4: Blackboard Promotion — Procedure Serialization
**WHEN** a promotion record is created with `methodology_kind: "procedure"`  
**THEN** the system SHALL serialize the promoted message body into a `ProcedureMethodology` structure, using the message body as a single step if no step patterns are detected, then write it to the Obsidian vault and enqueue it for ChromaDB ingestion  
**WHERE** the vault writer adds `procedure_source: blackboard` to the YAML frontmatter

**Implementation:**
- `cairn/vault/writer.py` gains a `write_procedure()` branch triggered by `methodology_kind`
- `cairn/nlp/step_extractor.py` (new): regex heuristics to split numbered/bulleted text into discrete steps
- If extraction yields < 2 steps, the full message body is stored as `steps: [<full body>]` with a `low_confidence: true` flag
- ChromaDB ingestion calls the same `sync_procedures()` path used by Route A

*Satisfies US-3*

---

### FR-5: Step Extraction Heuristics
**WHEN** the system processes a blackboard message body for procedure promotion  
**THEN** the system SHALL attempt to extract an ordered step list by detecting numbered lists (`1.`, `2.`), bulleted lists (`-`, `*`), or sentence-boundary sequences  
**WHERE** extraction is best-effort; failure to extract clean steps does not block promotion

**Implementation:**
- `cairn/nlp/step_extractor.py`: `extract_steps(text: str) -> list[str]`
- Priority: numbered list > bulleted list > sentence split (spaCy sentence boundaries)
- Minimum step length: 10 characters (filters noise)

*Satisfies US-3, FR-4*

---

### FR-6: Search API — Kind Filter
**WHEN** `GET /methodologies/search` is called  
**THEN** the system SHALL accept an optional `kind` query parameter (`sigma` | `procedure` | `any`)  
**IF** `kind` is provided, **THEN** the system SHALL pass it as a ChromaDB `where` filter  
**WHERE** the default behavior (no `kind` param) returns results of all kinds

**Implementation:**
- Update `cairn/api/routes/methodologies.py` search endpoint
- ChromaDB `where={"kind": kind}` when kind is not `any`
- Response items include a `kind` field

*Satisfies US-2, US-5*

---

### FR-7: Error Handling and Partial Ingestion
**WHEN** the sync job encounters an invalid `.procedure.yml`  
**THEN** the system SHALL log the file path and validation error, skip that file, and continue processing remaining files  
**WHERE** the overall sync job exit code reflects whether any errors occurred

**Implementation:**
- Per-file try/except in `sync_procedures()` collecting errors into a list
- Summary log at end: `"Synced N procedures; M failed (see above)"`

*Satisfies US-6*

---

## Non-Functional Requirements (EARS Format)

### NFR-1: Performance
**WHEN** the ChromaDB sync job processes a batch of procedure files  
**THEN** the system SHALL complete ingestion of up to 100 procedure files  
**WHERE** total sync time ≤ 60 seconds on the reference hardware (4-core, 8GB RAM)

### NFR-2: Reliability
**WHEN** ChromaDB is temporarily unavailable during a sync run  
**THEN** the system SHALL retry up to 3 times with exponential backoff before logging a failure and exiting  
**WHERE** no partial writes are left in an inconsistent state (upsert semantics)

### NFR-3: Search Latency
**WHEN** an agent calls `GET /methodologies/search`  
**THEN** the system SHALL return results in ≤ 200ms at p95  
**WHERE** the collection contains up to 10,000 documents (Sigma + procedure combined)

### NFR-4: Schema Stability
**WHEN** the `ProcedureMethodology` Pydantic model is modified  
**THEN** the system SHALL maintain backward compatibility with existing `.procedure.yml` files by marking new fields as `Optional` with defaults  
**WHERE** existing ingested documents in ChromaDB are re-upserted on next sync (stable ID ensures idempotency)

### NFR-5: Maintainability
**WHEN** a developer adds a new methodology kind in the future  
**THEN** the system SHALL require changes only in `cairn/models/methodology.py` and `cairn/sync/chroma_sync.py`  
**WHERE** the route layer, validation CLI, and ChromaDB collection remain unchanged

---

## Success Criteria

### MVP Success (Phase 1)
- ✅ `.procedure.yml` schema defined and validated by CI
- ✅ GitLab-authored procedures appear in ChromaDB after sync
- ✅ `kind=procedure` filter works in `/methodologies/search`
- ✅ 15+ unit tests passing

### Enhanced Success (Phase 2)
- ✅ Blackboard promotion path writes procedures to vault and ChromaDB
- ✅ Step extractor correctly parses numbered and bulleted lists
- ✅ 30+ tests passing including promotion integration tests

### Full Feature Success (Phase 3)
- ✅ Agent skill client `find_methodology()` updated to support `kind` filter
- ✅ End-to-end: blackboard message → promoted procedure → ChromaDB → agent retrieval
- ✅ 40+ tests passing
- ✅ Documented in `docs/phase4.5.md` and `.env.example` updated if needed

---

## Out of Scope

### MVP (Phase 1)
- Blackboard promotion path (Route C) — Phase 2
- Step extractor NLP — Phase 2
- Agent skill client updates — Phase 3
- UI for browsing procedures separately from Sigma rules

### All Phases
- Auto-generation of procedures from agent conversation transcripts (future AI feature)
- Versioning or diff-tracking of procedures across GitLab commits
- Procedure execution tracking (separate from methodology_executions which covers Sigma)
- Multi-language procedure support (English only)

---

## Dependencies

### Required
- ChromaDB ≥ 0.5.0 (already in `pyproject.toml`)
- PyYAML (already in `pyproject.toml`)
- Pydantic ≥ 2.0 (already in `pyproject.toml`)
- GitLab CE container (already in `docker-compose.yml`)

### Optional
- spaCy (Phase 2, for sentence-boundary step extraction fallback) — adds ~50MB to container image; gated behind `CAIRN_SPACY_ENABLED` env var

---

## Risks & Mitigation

### Risk 1: Step Extraction Quality
**Impact:** Medium  
**Probability:** High  
**Mitigation:**
- Accept free-form prose as a single step with `low_confidence: true` flag — extraction failure never blocks promotion
- Surface `low_confidence` in the vault note so human reviewers can clean up

### Risk 2: ChromaDB Collection Pollution
**Impact:** Medium  
**Probability:** Low  
**Mitigation:**
- Stable document IDs (SHA-256 of title + path) ensure re-syncs are idempotent
- `kind` metadata enables filtered queries so Sigma searches are unaffected by procedure documents

### Risk 3: CI Validation Drift from Runtime Schema
**Impact:** High  
**Probability:** Low  
**Mitigation:**
- Both CI linter and sync job import the same `ProcedureMethodology` Pydantic model — schema is the single source of truth
- Schema changes require updating one file; CI catches breakage on first merge attempt

### Risk 4: spaCy Image Size (Phase 2)
**Impact:** Low  
**Probability:** Medium  
**Mitigation:**
- Gate behind `CAIRN_SPACY_ENABLED=false` default; regex fallback always available
- If image size is a blocker, sentence splitting can use stdlib `re` patterns instead

### Risk 5: Analyst Adoption of `.procedure.yml` Format
**Impact:** Medium  
**Probability:** Medium  
**Mitigation:**
- Provide a `docs/procedure-template.yml` example file in the repo
- CI error messages should name the missing field and link to the template

---

## Timeline

### Phase 1: MVP (Week 1)
- Days 1–2: `ProcedureMethodology` Pydantic model, CLI validator, CI job
- Days 3–4: `sync_procedures()` in `chroma_sync.py`, kind filter in search endpoint
- Day 5: Unit tests (15+), integration test, procedure template doc

**Estimate:** 12 hours

### Phase 2: Enhanced (Week 2)
- Days 1–2: `step_extractor.py`, `write_procedure()` in vault writer
- Days 3–4: Blackboard promotion path wired end-to-end
- Day 5: Integration tests (15+ new), spaCy gate

**Estimate:** 14 hours

### Phase 3: Full Feature (Week 3)
- Days 1–2: Agent skill client `find_methodology()` kind filter, `MethodologyRef` updated
- Days 3–4: End-to-end test, `docs/phase4.5.md`, `.env.example` update if needed
- Day 5: Performance test, final polish

**Estimate:** 10 hours

**Total:** 36 hours over 3 weeks
