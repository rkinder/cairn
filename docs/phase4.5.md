# Phase 4.5 — Procedural Methodology Ingestion

This phase extends Cairn methodology support from Sigma-only metadata to dual-kind discovery and promotion (`sigma` + `procedure`), with Route A and Route C coverage.

## Endpoints

### `GET /methodologies/search`

Supports optional `kind` filter:

- `kind=sigma`
- `kind=procedure`
- `kind=any` (or omitted)

Response item includes:

- `gitlab_path`
- `commit_sha`
- `title`
- `tags`
- `status`
- `kind`
- `score`

### `POST /promotions/{candidate_id}/promote`

`PromoteRequest` accepts:

- `narrative` (optional)
- `methodology_kind` (optional, `sigma` or `procedure`)

When `methodology_kind=procedure`, the route extracts steps from narrative, writes a procedure-style note, and upserts metadata tagged as `kind=procedure`.

## Route A vs Route C

### Route A (GitLab → Chroma methodology search)

1. Author `.procedure.yml` under procedure directory.
2. Run sync path (`sync_procedures()`).
3. Query via `/methodologies/search?kind=procedure`.
4. Validate result carries `kind=procedure`.

### Route C (Promotion → Vault + Chroma upsert)

1. Analyst calls promote endpoint with human review headers.
2. Optional `methodology_kind=procedure` selects procedure branch.
3. Narrative steps extracted by `extract_steps()`.
4. Vault write + best-effort Chroma upsert.
5. Candidate status updated to `promoted`.

## Procedure authoring tips

- Keep title stable and specific.
- Prefer explicit numbered or bulleted steps (best extraction quality).
- Keep each step >= 10 characters.
- Include concise summary and operational tags.
- Treat `narrative` as analyst-curated context; steps should be executable.

## spaCy-gated sentence fallback

`extract_steps()` uses this order:

1. Numbered list extraction
2. Bulleted list extraction
3. Optional spaCy sentence segmentation fallback (gated)
4. Basic `. ` split fallback

Feature gate:

- `CAIRN_SPACY_ENABLED=false` by default
- Optional dependency extra: `pip install .[nlp]`

This keeps base image/runtime light while allowing improved sentence boundaries where needed.
