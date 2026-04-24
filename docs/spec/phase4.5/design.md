# Phase 4.5 — Procedural Methodology Ingestion — Design

## Overview

Phase 4.5 adds a second methodology kind — **procedure** — alongside the existing Sigma rule kind. A procedure is a lightweight, human-authored YAML file listing ordered response steps. Unlike Sigma rules, procedures do not compile to a query language; they are checklists that agents and analysts follow during an investigation.

The phase adds three capabilities:

1. **Route A — GitLab sync**: `sync_procedures()` globs `.procedure.yml` files from the methodology repo, validates them, and upserts into ChromaDB alongside Sigma rules.
2. **Route B — CI linter**: A GitLab CI job runs `cairn.cli.validate_procedures` on every push to the methodology repo, blocking invalid files before merge.
3. **Route C — Blackboard promotion**: When an analyst promotes a blackboard message with `methodology_kind=procedure`, `write_procedure()` in the vault writer extracts steps from the message body, produces a structured vault note, and enqueues a ChromaDB upsert.

---

## Architecture

```
cairn/
  models/
    methodology.py            # FR-1: ProcedureMethodology Pydantic model, MethodologyKind enum
  nlp/
    entity_extractor.py       # (existing)
    step_extractor.py         # FR-5: extract_steps() heuristics
  sync/
    chroma_sync.py            # FR-3: sync_procedures() added alongside existing functions
  vault/
    writer.py                 # FR-4: write_procedure() branch
  api/
    routes/
      methodologies.py        # FR-6: kind filter on /search, kind field in response
      promotions.py           # FR-4: methodology_kind field on PromoteRequest
  cli/
    validate_procedures.py    # FR-2: CLI entrypoint

gitlab-ci/
  procedure-validate.yml      # FR-2: CI linter job

methodologies/
  procedures/
    example.procedure.yml     # template

tests/
  unit/
    test_procedure_model.py
    test_step_extractor.py
    test_sync_procedures.py
    test_cli_validate.py
  integration/
    test_procedure_route_a.py
    test_procedure_route_c.py
    test_methodology_kind_filter.py
```

---

## Core Components

### FR-1: ProcedureMethodology Model — `cairn/models/methodology.py`

```python
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class MethodologyKind(str, Enum):
    sigma     = "sigma"
    procedure = "procedure"


class ProcedureMethodology(BaseModel):
    """Schema for .procedure.yml files in the methodology repo."""

    title:       str         = Field(..., description="Short human-readable title.")
    tags:        list[str]   = Field(..., description="Discovery tags (e.g. ['phishing', 'triage']).")
    steps:       list[str]   = Field(..., min_length=2, description="Ordered response steps.")
    description: str | None  = Field(default=None)
    references:  list[str]   = Field(default_factory=list)
    author:      str | None  = Field(default=None)
    severity:    Literal["low", "medium", "high", "critical"] | None = Field(default=None)


class SigmaMethodology(BaseModel):
    """Stub model for future Sigma validation (Phase 5+).

    Full Sigma validation is currently handled by cairn.ingest.sigma;
    this stub exists so MethodologyKind can be dispatched uniformly.
    """

    title:       str
    tags:        list[str]   = Field(default_factory=list)
    description: str | None  = None
    status:      str         = "experimental"
```

**Validation behaviour**:

- `steps` with `min_length=2` causes Pydantic to raise `ValidationError` when fewer than two steps are provided — the CLI validator and `sync_procedures()` both catch this.
- A `steps` list with exactly one element is not separately warned at the model layer; the CLI validator emits a warning separately so analysts see it during CI.
- `severity` is optional; absent severity is treated as `None` in ChromaDB metadata (stored as empty string to satisfy ChromaDB's string-only metadata constraint).

---

### FR-2: Procedure CLI Validator — `cairn/cli/validate_procedures.py`

```python
"""CLI entrypoint: validate all .procedure.yml files in a directory.

Usage:
    python -m cairn.cli.validate_procedures methodologies/procedures/

Exit codes:
    0 — all files valid (or directory empty)
    1 — one or more files failed validation, or directory not found
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from cairn.models.methodology import ProcedureMethodology


def validate_directory(procedures_dir: Path) -> int:
    """Validate all .procedure.yml files under procedures_dir.

    Returns the count of files that failed validation.
    """
    if not procedures_dir.is_dir():
        print(f"ERROR: directory not found: {procedures_dir}", file=sys.stderr)
        return 1

    files  = sorted(procedures_dir.glob("*.procedure.yml"))
    failed = 0

    for path in files:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            ProcedureMethodology.model_validate(raw)
            print(f"  OK  {path.name}")
        except (yaml.YAMLError, ValidationError) as exc:
            print(f"FAIL  {path.name}: {exc}", file=sys.stderr)
            failed += 1

    if not files:
        print("No .procedure.yml files found — nothing to validate.")

    return failed


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m cairn.cli.validate_procedures <directory>", file=sys.stderr)
        sys.exit(1)

    failed = validate_directory(Path(sys.argv[1]))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

---

### FR-2: GitLab CI Job — `gitlab-ci/procedure-validate.yml`

```yaml
# Validates all .procedure.yml files on every push to the methodology repo.
# Mirrors the existing sigma-validate job pattern.

procedure-validate:
  stage: validate
  image: python:3.12-slim
  script:
    - pip install --quiet pyyaml pydantic
    - pip install --quiet -e .
    - python -m cairn.cli.validate_procedures methodologies/procedures/
  rules:
    - changes:
        - "methodologies/procedures/**/*.procedure.yml"
```

The job installs only the lightweight subset of Cairn needed for model validation (`pyyaml`, `pydantic`) so the CI image stays small. It runs only when procedure files change, matching the existing `sigma-validate` rule pattern.

---

### FR-3: `sync_procedures()` — ChromaDB Ingestion — addition to `cairn/sync/chroma_sync.py`

`sync_procedures()` is a free function added to the existing `chroma_sync` module alongside the existing `upsert_methodology()` and `search_methodologies()` functions. It follows the same module-level style — no class wrapper.

```python
import hashlib
from pathlib import Path

import yaml
from pydantic import ValidationError

from cairn.models.methodology import ProcedureMethodology


def sync_procedures(
    collection: chromadb.Collection,
    procedures_dir: Path,
) -> tuple[int, int]:
    """Glob .procedure.yml files under procedures_dir and upsert into ChromaDB.

    Mirrors the existing upsert_methodology() call pattern.  Each procedure is
    upserted with kind="procedure" in metadata so the kind filter in
    search_methodologies_endpoint() can distinguish it from Sigma rules.

    Args:
        collection:     ChromaDB collection (from get_collection()).
        procedures_dir: Local path to the procedures/ directory in the repo.

    Returns:
        (synced_count, failed_count) tuple.
    """
    files   = sorted(procedures_dir.glob("*.procedure.yml"))
    synced  = 0
    failed  = 0

    for path in files:
        try:
            raw  = yaml.safe_load(path.read_text(encoding="utf-8"))
            proc = ProcedureMethodology.model_validate(raw)

            steps_prose = "\n".join(
                f"{i + 1}. {step}" for i, step in enumerate(proc.steps)
            )
            document = "\n\n".join(filter(None, [
                proc.title,
                proc.description,
                steps_prose,
            ]))

            doc_id = _procedure_doc_id(proc.title, str(path))
            metadata: dict[str, Any] = {
                "kind":     "procedure",
                "title":    proc.title,
                "tags":     ",".join(proc.tags),
                "author":   proc.author or "",
                "severity": proc.severity or "",
            }
            collection.upsert(
                ids=[doc_id],
                documents=[document],
                metadatas=[metadata],
            )
            logger.info("ChromaDB upsert procedure: %s", path.name)
            synced += 1

        except (yaml.YAMLError, ValidationError, Exception) as exc:
            logger.warning("sync_procedures: skipping %s — %s", path.name, exc)
            failed += 1

    logger.info("sync_procedures: synced %d procedures; %d failed", synced, failed)
    return synced, failed


def _procedure_doc_id(title: str, filepath: str) -> str:
    """Derive a stable ChromaDB document ID from procedure title + filepath.

    Uses sha256 so IDs are fixed-length and safe for ChromaDB regardless of
    special characters in the title or path.  Distinct from _path_to_chroma_id()
    which is used for Sigma rules (path-based, no hash).
    """
    raw = f"{title}\x00{filepath}".encode()
    return hashlib.sha256(raw).hexdigest()
```

**Integration with the existing scheduler**:

The existing APScheduler job that calls `sync_sigma_rules()` is extended to also call `sync_procedures()`. The procedures directory is resolved from `settings.gitlab_methodology_dir` with a `procedures/` suffix:

```python
# In the scheduler job (existing pattern, extended):
from cairn.sync.chroma_sync import get_collection, sync_procedures

procedures_dir = Path(settings.gitlab_methodology_dir) / "procedures"
if procedures_dir.is_dir():
    sync_procedures(collection, procedures_dir)
```

---

### FR-5: Step Extractor — `cairn/nlp/step_extractor.py`

```python
"""Heuristic step extraction from unstructured markdown message bodies.

Used by write_procedure() (Route C) to extract steps from a blackboard
message body that was not originally authored as a .procedure.yml.

Priority order:
  1. Numbered list:  lines matching ^\d+[\.\)]\s+
  2. Bulleted list:  lines matching ^[-*\u2022]\s+
  3. Sentence split: split on ". " with length filter

Steps shorter than 10 characters are filtered out as noise.
Empty result is valid — caller (write_procedure) handles the low-confidence case.
"""

from __future__ import annotations

import re

_RE_NUMBERED = re.compile(r"^\d+[\.\)]\s+(.+)$", re.MULTILINE)
_RE_BULLETED = re.compile(r"^[-*\u2022]\s+(.+)$", re.MULTILINE)
_MIN_STEP_LEN = 10


def extract_steps(text: str) -> list[str]:
    """Extract an ordered list of steps from markdown text.

    Args:
        text: Raw markdown body of a blackboard message.

    Returns:
        List of step strings.  May be empty.
    """
    if not text or not text.strip():
        return []

    # Priority 1: numbered list
    numbered = _RE_NUMBERED.findall(text)
    if numbered:
        return _filter_short(numbered)

    # Priority 2: bulleted list
    bulleted = _RE_BULLETED.findall(text)
    if bulleted:
        return _filter_short(bulleted)

    # Priority 3: sentence split
    sentences = [s.strip() for s in text.split(". ") if s.strip()]
    return _filter_short(sentences)


def _filter_short(steps: list[str]) -> list[str]:
    return [s.strip() for s in steps if len(s.strip()) >= _MIN_STEP_LEN]
```

---

### FR-4: `write_procedure()` — Vault Writer Branch — addition to `cairn/vault/writer.py`

`write_procedure()` is a new async function added to the existing `writer.py` module. It follows the same style as `write_note()`: disk write first, best-effort CouchDB sync, returns `WriteResult`.

```python
async def write_procedure(
    vault_root: Path,
    *,
    title: str,
    steps: list[str],
    tags: list[str] | None = None,
    narrative: str | None = None,
    source_message_ids: list[str],
    promoted_at: str,
    author: str | None = None,
    severity: str | None = None,
    low_confidence: bool = False,
    couchdb_client: "CouchDBVaultClient | None" = None,
) -> WriteResult:
    """Write or update an Obsidian vault note for a promoted procedure.

    Procedures are routed to cairn/procedures/ within the vault, separate from
    entity notes (cairn/ or cairn/{domain}/).  Deduplication uses the same
    _safe_filename() + exist-check pattern as write_note().

    Args:
        vault_root:         Absolute path to the Obsidian vault root.
        title:              Procedure title (from message subject or first 60 chars).
        steps:              Extracted response steps.
        tags:               Obsidian tags.
        narrative:          Optional analyst narrative for ## Summary.
        source_message_ids: Blackboard message IDs that triggered this promotion.
        promoted_at:        ISO8601 promotion timestamp.
        author:             Optional author identifier.
        severity:           Optional severity string.
        low_confidence:     True when fewer than 2 steps were extracted —
                            surfaced as a frontmatter flag for analyst review.
        couchdb_client:     Optional CouchDB client for LiveSync sync.

    Returns:
        WriteResult with vault_rel and CouchDB sync status.
    """
    target_dir = vault_root / _CAIRN_SUBDIR / "procedures"
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(title)
    note_file = target_dir / f"{safe_name}.md"
    vault_rel = f"{_CAIRN_SUBDIR}/procedures/{safe_name}.md"
    now_iso   = _now_iso()

    if note_file.exists():
        _update_existing_note(
            note_file,
            source_message_ids=source_message_ids,
            promoted_at=promoted_at,
            now_iso=now_iso,
        )
        logger.info("vault/writer: updated existing procedure note %s", vault_rel)
    else:
        content = _build_procedure_note(
            title=title,
            steps=steps,
            tags=tags or [],
            narrative=narrative or "",
            source_message_ids=source_message_ids,
            promoted_at=promoted_at,
            author=author,
            severity=severity,
            low_confidence=low_confidence,
            now_iso=now_iso,
        )
        note_file.write_text(content, encoding="utf-8")
        logger.info("vault/writer: created procedure note %s", vault_rel)

    # CouchDB sync — best effort, disk is the primary store (same pattern as write_note)
    couchdb_synced = False
    couchdb_error: str | None = None

    if couchdb_client is not None:
        stat         = note_file.stat()
        ctime_ms     = int(stat.st_ctime * 1000)
        mtime_ms     = int(stat.st_mtime * 1000)
        file_content = note_file.read_text(encoding="utf-8")

        put_result = await couchdb_client.put_note(
            vault_rel_path=vault_rel,
            content=file_content,
            ctime_ms=ctime_ms,
            mtime_ms=mtime_ms,
        )
        couchdb_synced = put_result.success
        couchdb_error  = put_result.error
        if not put_result.success:
            logger.warning(
                "vault/writer: CouchDB sync failed for procedure %s: %s",
                vault_rel, couchdb_error,
            )

    return WriteResult(
        vault_rel=vault_rel,
        couchdb_synced=couchdb_synced,
        couchdb_error=couchdb_error,
    )


def _build_procedure_note(
    *,
    title: str,
    steps: list[str],
    tags: list[str],
    narrative: str,
    source_message_ids: list[str],
    promoted_at: str,
    author: str | None,
    severity: str | None,
    low_confidence: bool,
    now_iso: str,
) -> str:
    """Render the full markdown content for a new procedure vault note."""
    all_tags  = list(dict.fromkeys(["procedure", "cairn-promoted"] + tags))
    tags_yaml = "[" + ", ".join(all_tags) + "]"

    if source_message_ids:
        sources_yaml = "\n" + "\n".join(f"  - {mid}" for mid in source_message_ids)
    else:
        sources_yaml = " []"

    optional_lines = ""
    if author:
        optional_lines += f"author: {author}\n"
    if severity:
        optional_lines += f"severity: {severity}\n"
    if low_confidence:
        optional_lines += "low_confidence: true\n"

    steps_md = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(steps))
    summary  = narrative.strip() if narrative.strip() else "_No summary provided._"
    evidence = _format_evidence_entry(promoted_at=promoted_at, source_message_ids=source_message_ids)

    return (
        f"---\n"
        f"title: {title}\n"
        f"tags: {tags_yaml}\n"
        f"procedure_source: blackboard\n"
        f"{optional_lines}"
        f"sources:{sources_yaml}\n"
        f"promoted_at: {promoted_at}\n"
        f"last_updated: {now_iso}\n"
        f"---\n"
        f"\n"
        f"## Summary\n"
        f"\n"
        f"{summary}\n"
        f"\n"
        f"## Steps\n"
        f"\n"
        f"{steps_md}\n"
        f"\n"
        f"## Evidence\n"
        f"\n"
        f"{evidence}\n"
    )
```

---

### FR-4: `PromoteRequest` — `methodology_kind` Field — `cairn/api/routes/promotions.py`

The existing `PromoteRequest` model gains one optional field:

```python
class PromoteRequest(BaseModel):
    narrative: str | None = Field(
        default=None,
        description="Optional narrative override for the vault note ## Summary section.",
    )
    methodology_kind: Literal["sigma", "procedure"] | None = Field(
        default=None,
        description=(
            "When set to 'procedure', the promotion writes a structured procedure "
            "note via write_procedure() instead of the standard entity note path."
        ),
    )
```

The `promote_candidate` route handler gains a conditional branch after the existing `write_note()` call:

```python
# In promote_candidate(), after fetching source_findings:
from cairn.nlp.step_extractor import extract_steps
from cairn.vault.writer import write_procedure

if body.methodology_kind == "procedure":
    steps        = extract_steps(source_findings[0]["body"] if source_findings else narrative or "")
    low_conf     = len(steps) < 2
    write_result = await write_procedure(
        settings.vault_path,
        title=entity[:60],
        steps=steps,
        tags=json.loads(row.get("tags_json") or "[]"),
        narrative=narrative,
        source_message_ids=source_ids,
        promoted_at=now_iso,
        low_confidence=low_conf,
        couchdb_client=couchdb_client,
    )
else:
    write_result = await write_note(
        settings.vault_path,
        entity=entity,
        # ... existing arguments unchanged ...
    )
```

The ChromaDB upsert that follows the vault write also enqueues a procedure-kind document when `methodology_kind == "procedure"`, using `_procedure_doc_id()` from `chroma_sync`:

```python
# After write_procedure(), best-effort ChromaDB upsert:
try:
    from cairn.sync.chroma_sync import get_collection, _procedure_doc_id

    chroma   = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    col      = get_collection(chroma)
    doc_id   = _procedure_doc_id(entity[:60], write_result.vault_rel)
    document = "\n\n".join(filter(None, [
        entity[:60], narrative,
        "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)),
    ]))
    col.upsert(
        ids=[doc_id],
        documents=[document],
        metadatas=[{
            "kind":     "procedure",
            "title":    entity[:60],
            "tags":     ",".join(json.loads(row.get("tags_json") or "[]")),
            "author":   "",
            "severity": "",
        }],
    )
except Exception:
    logger.warning("promote_candidate: ChromaDB upsert failed for procedure %s", candidate_id)
```

---

### FR-6: `kind` Filter — `cairn/api/routes/methodologies.py`

The existing `MethodologySearchResult` gains a `kind` field:

```python
class MethodologySearchResult(BaseModel):
    gitlab_path: str
    commit_sha:  str
    title:       str
    tags:        list[str]
    status:      str
    score:       float = Field(..., ge=0.0, le=1.0)
    kind:        str   = Field(default="sigma", description="'sigma' or 'procedure'")
```

The `search_methodologies_endpoint` gains a `kind` query parameter:

```python
@router.get("/search", ...)
async def search_methodologies_endpoint(
    _agent: Annotated[dict, Depends(authenticated_agent)],
    q: Annotated[str, Query(min_length=1)],
    n: Annotated[int, Query(ge=1, le=50)] = 10,
    kind: Annotated[
        Literal["sigma", "procedure", "any"] | None,
        Query(description="Filter by methodology kind. Omit or pass 'any' for all kinds.")
    ] = None,
) -> list[MethodologySearchResult]:
    settings = get_settings()
    try:
        import chromadb
        from cairn.sync.chroma_sync import get_collection, search_methodologies

        client     = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        collection = get_collection(client)

        where   = {"kind": kind} if (kind and kind != "any") else None
        results = search_methodologies(collection, q, n=n, where=where)
    except Exception as exc:
        logger.exception("ChromaDB methodology search failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Methodology search unavailable: {exc}",
        ) from exc

    return [
        MethodologySearchResult(
            gitlab_path=r["gitlab_path"],
            commit_sha=r["commit_sha"],
            title=r["title"],
            tags=r["tags"],
            status=r["status"],
            score=r["score"],
            kind=r.get("kind", "sigma"),
        )
        for r in results
    ]
```

The `search_methodologies()` function in `chroma_sync.py` gains an optional `where` parameter:

```python
def search_methodologies(
    collection: chromadb.Collection,
    query: str,
    n: int = 10,
    where: dict | None = None,
) -> list[dict[str, Any]]:
    count = collection.count()
    if count == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=min(n, count),
        include=["metadatas", "distances"],
        where=where,          # None means no filter (all kinds)
    )

    ids       = results.get("ids",       [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    output: list[dict[str, Any]] = []
    for _, meta, dist in zip(ids, metadatas, distances):
        # With hnsw:space=cosine, distance = 1 - cosine_similarity in [0, 2].
        # Map to [0, 1] similarity score: score = 1 - clamp(distance, 0, 1).
        score = round(max(0.0, 1.0 - float(dist)), 4)
        output.append({
            "gitlab_path": meta.get("gitlab_path", ""),
            "commit_sha":  meta.get("commit_sha",  ""),
            "title":       meta.get("title",       ""),
            "tags":        [t for t in meta.get("tags", "").split(",") if t],
            "status":      meta.get("status",      ""),
            "kind":        meta.get("kind",        "sigma"),   # new field; existing docs default to sigma
            "score":       score,
        })
    return output
```

**Note**: Existing Sigma documents in ChromaDB do not have a `kind` metadata field. `search_methodologies()` defaults missing `kind` to `"sigma"` so backward compatibility is maintained without a re-sync.

---

## Example `.procedure.yml` Template — `methodologies/procedures/example.procedure.yml`

```yaml
title: "Phishing Email Triage"
description: "Standard procedure for triaging suspected phishing emails"
tags: ["phishing", "email", "triage"]
author: "soc-team"
severity: "high"
steps:
  - "Check SPF, DKIM, and DMARC headers for alignment failures"
  - "Extract sender domain and query against threat intel (VirusTotal, URLhaus)"
  - "Examine all URLs in the body — detonate in sandbox if unfamiliar domain"
  - "Check if any recipients clicked links using email gateway logs"
  - "Quarantine the email and notify affected recipients if links were clicked"
  - "File IOCs (sender IP, domains, URLs) to the Cairn blackboard"
references:
  - "https://attack.mitre.org/techniques/T1566/"
```

---

## Testing Strategy

### Unit Tests — Phase 1 (target: 15+)

**`tests/unit/test_procedure_model.py`**

| Test | Assertion |
|---|---|
| `test_valid_model` | Full valid YAML parses without error |
| `test_missing_title_fails` | `ValidationError` raised |
| `test_empty_steps_fails` | `ValidationError` raised (min_length=2) |
| `test_single_step_fails` | `ValidationError` raised (min_length=2) |
| `test_optional_fields_default` | `description`, `author`, `severity` are `None` when absent |
| `test_severity_enum_invalid` | `ValidationError` for severity not in Literal |
| `test_references_default_empty` | `references` defaults to `[]` |

**`tests/unit/test_step_extractor.py`**

| Test | Assertion |
|---|---|
| `test_numbered_list` | `"1. Step one\n2. Step two"` returns 2 steps |
| `test_numbered_paren` | `"1) Step one\n2) Step two"` returns 2 steps |
| `test_bulleted_dash` | `"- Step one\n- Step two"` returns 2 steps |
| `test_bulleted_star` | `"* Step one\n* Step two"` returns 2 steps |
| `test_bulleted_bullet` | Unicode bullet lines return 2 steps |
| `test_sentence_split_fallback` | Plain prose with `. ` sentences splits correctly |
| `test_short_steps_filtered` | Steps under 10 chars are excluded |
| `test_numbered_takes_priority` | Mixed numbered + bulleted text returns numbered result |
| `test_empty_input` | `""` returns `[]` |
| `test_whitespace_only` | `"   "` returns `[]` |

**`tests/unit/test_sync_procedures.py`**

| Test | Assertion |
|---|---|
| `test_valid_file_ingested` | Valid `.procedure.yml` causes `collection.upsert` to be called once |
| `test_invalid_file_skipped` | Invalid YAML returns `(0, 1)`, no upsert |
| `test_metadata_correct` | `kind="procedure"` in metadata; tags stored as CSV |
| `test_doc_id_stable` | Same title + path produces same doc_id across calls |
| `test_empty_dir` | Empty directory returns `(0, 0)` |
| `test_multiple_files` | 3 valid files returns `(3, 0)` |

**`tests/unit/test_cli_validate.py`**

| Test | Assertion |
|---|---|
| `test_valid_dir_exits_0` | Temp dir with valid file exits 0 |
| `test_invalid_file_exits_1` | Invalid `.procedure.yml` exits 1 |
| `test_missing_dir_exits_1` | Non-existent path exits 1 |
| `test_empty_dir_exits_0` | Empty directory exits 0 (nothing to fail) |

### Integration Tests — Phase 2 (target: 15+)

**`tests/integration/test_procedure_route_a.py`** — Route A (GitLab sync to ChromaDB)

| Test | Assertion |
|---|---|
| `test_full_sync_roundtrip` | Write `.procedure.yml` to temp dir then `sync_procedures()` then `search_methodologies(q=title)` returns it |
| `test_kind_in_result` | Result from above has `kind="procedure"` |
| `test_upsert_idempotent` | Sync same file twice leaves collection.count() unchanged |
| `test_description_optional` | File without `description` syncs without error |

**`tests/integration/test_procedure_route_c.py`** — Route C (promotion to vault note to ChromaDB)

| Test | Assertion |
|---|---|
| `test_promote_with_procedure_kind` | `POST /promotions/{id}/promote` with `methodology_kind=procedure` returns `vault_rel` containing `procedures/` |
| `test_vault_note_has_steps_section` | Written note contains `## Steps` section |
| `test_vault_note_has_procedure_source` | Frontmatter contains `procedure_source: blackboard` |
| `test_low_confidence_flag` | Message body with no extractable steps sets `low_confidence: true` in frontmatter |
| `test_chroma_upsert_called` | ChromaDB upsert is attempted with `kind="procedure"` |
| `test_couchdb_sync_attempted` | CouchDB `put_note` is called when client is provided |

**`tests/integration/test_methodology_kind_filter.py`** — Kind filter on `/methodologies/search`

| Test | Assertion |
|---|---|
| `test_sigma_filter_excludes_procedures` | `kind=sigma` returns no procedure results |
| `test_procedure_filter_excludes_sigma` | `kind=procedure` returns no sigma results |
| `test_any_returns_both` | `kind=any` returns both kinds |
| `test_no_kind_returns_both` | Omitting `kind` returns both kinds |
| `test_kind_field_in_response` | Each result has `kind` field set correctly |

---

## Implementation Order

1. `cairn/models/methodology.py` — model first; everything depends on it.
2. `cairn/nlp/step_extractor.py` — pure function, no dependencies.
3. `cairn/cli/validate_procedures.py` + `gitlab-ci/procedure-validate.yml` — FR-2 complete.
4. `cairn/sync/chroma_sync.py` — add `sync_procedures()` and `_procedure_doc_id()`; update `search_methodologies()` signature.
5. `cairn/vault/writer.py` — add `write_procedure()` and `_build_procedure_note()`.
6. `cairn/api/routes/promotions.py` — add `methodology_kind` to `PromoteRequest`; add branch in `promote_candidate`.
7. `cairn/api/routes/methodologies.py` — add `kind` to response model and query param.
8. Unit tests, then integration tests.
