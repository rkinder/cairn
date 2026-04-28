# Phase 4.5 — Procedural Methodology Ingestion — Tasks

## Audit Status (updated 2026-04-24)

This file has been audited against the current repository state and test run (`uv run pytest -q` => 287 passed).

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
| 1.6 Route A integration tests | ✅ | `tests/integration/test_phase45_route_a.py` added |
| 2.1 `extract_steps()` + tests | ✅ | `cairn/nlp/step_extractor.py`, `tests/test_step_extractor.py` added |
| 2.2 `methodology_kind` on PromoteRequest | ✅ | `cairn/api/routes/promotions.py`, `tests/test_promotions_methodology_kind.py` |
| 2.3 `write_procedure()` + tests | ✅ | Implemented and tests passing. |
| 2.4 Promotion route wiring + integration tests | ✅ | Implemented and integrated. |
| 2.5 Route C end-to-end tests | ✅ | Implemented in `tests/integration/test_phase45_route_c.py`. |
| 2.6 spaCy gate | ✅ | config flag + optional dependency + env/tests implemented |
| 3.1 Skill client kind filter | ✅ | `tests/test_skill_client_methodology.py` added |
| 3.2 Docs + README mention | ✅ | `docs/phase4.5.md` added; README updated |
| 3.3 Performance tests | ✅ | `tests/performance/test_procedure_sync_perf.py` added |
| 3.4 Full e2e (all phases) | ✅ | `tests/integration/test_phase45_e2e.py` added |
| 3.5 Final docs/changelog pass | ✅ | CHANGELOG created, README updated, `.env.example` completed |

