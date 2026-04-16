# Phase 4 — Obsidian Vault Bridge

This document covers the Phase 4 additions: automatic corroboration detection, the promotion pipeline from SQLite into the Obsidian vault, ChromaDB indexing of promoted notes, and the CouchDB + LiveSync setup for multi-device vault access.

---

## Architecture Overview

```
message_index (SQLite)
        │
        │  ┌──────────────────────────────────────┐
        │  │  Corroboration job (APScheduler)      │
        │  │  runs every 15 min                    │
        │  │  - detect ≥ N agents mentioning same  │
        │  │    entity in time window               │
        │  │  - detect agent self-nominations       │
        │  └──────────────────────────────────────┘
        │                    │
        ▼                    ▼
   message_index      promotion_candidates
   promote=candidate  status=pending_review
                             │
                    Human reviews in UI
                             │
                   POST /promotions/{id}/promote
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        vault/cairn/   ChromaDB          message_index
        <entity>.md    vault-notes       promote=promoted
        (written to    collection
         bind mount)   (indexed for
                        semantic search)
              │
        CouchDB ← Obsidian LiveSync → Obsidian clients
```

---

## New Endpoints

### `GET /promotions`

List promotion candidates.

**Query parameters:**

| Parameter   | Type   | Description                                              |
|-------------|--------|----------------------------------------------------------|
| `status`    | string | Filter: `pending_review` \| `promoted` \| `dismissed`   |
| `entity_type` | string | Filter by entity type                                  |
| `trigger`   | string | Filter: `corroboration` \| `human` \| `agent`            |
| `limit`     | int    | Default 50, max 500                                      |
| `offset`    | int    | Pagination offset                                        |

**Response:** Array of `CandidateResponse` objects.

```json
[
  {
    "id": "01hvz...",
    "entity": "APT29",
    "entity_type": "actor",
    "trigger": "corroboration",
    "status": "pending_review",
    "confidence": null,
    "source_message_ids": ["01hvy...", "01hvw..."],
    "narrative": "",
    "reviewer_id": null,
    "vault_path": null,
    "created_at": "2026-04-14T10:30:00Z",
    "updated_at": "2026-04-14T10:30:00Z"
  }
]
```

---

### `GET /promotions/{id}`

Retrieve a single promotion candidate by ID.

---

### `POST /promotions/{id}/promote`

Promote a candidate to the Obsidian vault.

**Required headers:**

```
X-Human-Reviewer: true
X-Reviewer-Identity: analyst-name
```

**Request body (optional):**

```json
{
  "narrative": "APT29 (Cozy Bear) is a Russian state-sponsored threat actor..."
}
```

If `narrative` is provided it overrides the narrative stored on the candidate.

**What happens:**
1. Validates the candidate is in `pending_review`.
2. Writes a structured markdown note to `CAIRN_VAULT_PATH/cairn/<entity>.md`.
3. Upserts the note into ChromaDB `vault-notes` collection.
4. Updates `promotion_candidates` status to `promoted`.
5. Updates `message_index.promote` to `promoted` for all source messages.

**Response:** Updated `CandidateResponse` with `vault_path` populated.

---

### `POST /promotions/{id}/dismiss`

Dismiss a promotion candidate.

**Required headers:**

```
X-Human-Reviewer: true
X-Reviewer-Identity: analyst-name
```

**Request body (optional):**

```json
{
  "reason": "Duplicate of existing APT29 note."
}
```

**Response:** Updated `CandidateResponse` with `status: "dismissed"`.

---

### `GET /vault/search`

Semantic search over promoted vault notes.

**Query parameters:**

| Parameter | Type   | Description                    |
|-----------|--------|--------------------------------|
| `q`       | string | Natural-language search query  |
| `n`       | int    | Max results (default 5, max 50)|

**Response:**

```json
[
  {
    "vault_path": "cairn/APT29.md",
    "title": "APT29",
    "entity_type": "actor",
    "confidence": 0.91,
    "promoted_at": "2026-04-14T10:32:00Z",
    "score": 0.94
  }
]
```

Returns 503 if ChromaDB is unreachable.

---

## Vault Note Format

Every promoted note is written to `CAIRN_VAULT_PATH/cairn/<entity>.md`.

```markdown
---
title: APT29
tags: [threat-actor, cairn-promoted]
entity_type: actor
confidence: 0.91
sources:
  - 01hvy...
  - 01hvw...
promoted_at: 2026-04-14T10:32:00Z
last_updated: 2026-04-14T10:32:00Z
---

## Summary

APT29 (also known as Cozy Bear) is a Russian state-sponsored threat actor
associated with SVR. Active since at least 2008, primarily targeting
government, healthcare, and energy sectors in NATO countries.

## Evidence

- **2026-04-14T10:32:00Z** — 01hvy..., 01hvw...

## Related

[[threat-actor]]
```

If the note already exists (same entity promoted again), a new `## Evidence`
entry is appended and `last_updated` is refreshed — no duplicate file is created.

---

## Corroboration Detection

A background job runs every 15 minutes (APScheduler `AsyncIOScheduler`).
Two detection passes:

### Pass 1 — Corroboration

1. Fetch all messages from `message_index` ingested within `CAIRN_CORROBORATION_WINDOW_HOURS`.
2. For each message, fetch the body from the topic database.
3. Run the regex entity extractor (`cairn.nlp.entity_extractor`) on the body + tags.
4. Find entities mentioned by ≥ `CAIRN_CORROBORATION_N` distinct agent IDs.
5. For each such entity not already in `promotion_candidates` (pending or promoted),
   create a new `pending_review` candidate with `trigger=corroboration`.

### Pass 2 — Agent self-nomination

1. Query `message_index` for messages with `promote = 'candidate'` AND
   `confidence >= CAIRN_PROMOTION_CONFIDENCE_THRESHOLD`.
2. For each message not already referenced in `promotion_candidates`,
   create a new `pending_review` candidate with `trigger=agent`.

---

## Entity Extraction

The extractor (`cairn.nlp.entity_extractor`) uses regex only — no NLP dependencies.

| Entity type | Example                   | Pattern                              |
|-------------|---------------------------|--------------------------------------|
| `ipv4`      | `203.0.113.42`            | Four dotted octets 0–255             |
| `ipv6`      | `2001:db8::1`             | Colon-separated hex groups           |
| `fqdn`      | `host.example.com`        | ≥2 labels, TLD ≥2 alpha chars        |
| `cve`       | `CVE-2024-12345`          | `CVE-YYYY-NNNNN`                     |
| `technique` | `T1059.003`               | `T\d{4}(\.\d{3})?`                   |
| `actor`     | `APT29`                   | Parsed from `actor:` / `group:` tags |

---

## Wikilink Resolution

Before writing a vault note, `WikilinkResolver` scans the vault directory once
(cached for the process lifetime) to find existing notes by:

1. **Filename stem** — `APT29.md` → `[[APT29]]`
2. **Frontmatter `aliases`** — `aliases: [Cozy Bear]` → `[[APT29]]`

If no match is found, `[[entity-value]]` is written as an unresolved wikilink
(Obsidian renders this in red — a useful signal that a note should be created).

After a new note is written, `resolver.register(title)` adds it to the cache
immediately so subsequent promotions in the same process can find it.

---

## Agent Skill Usage

```python
from cairn.skill import BlackboardClient

async with BlackboardClient(base_url="http://localhost:8000", api_key="cairn_...") as bb:

    # Before generating analysis on a known entity, check the vault for
    # prior human-curated knowledge.
    notes = await bb.find_vault_note("APT29 Cozy Bear Russia", n=3)
    for note in notes:
        print(f"{note.title} — {note.vault_path} (score {note.score:.2f})")
        # Read the actual file from the vault bind mount if you need the narrative.
```

`VaultNoteRef` fields:

| Field         | Type          | Description                                |
|---------------|---------------|--------------------------------------------|
| `vault_path`  | `str`         | Vault-relative path (`cairn/APT29.md`)    |
| `title`       | `str`         | Entity canonical name                      |
| `entity_type` | `str`         | `ipv4` / `ipv6` / `fqdn` / `cve` / `technique` / `actor` |
| `confidence`  | `float\|None` | Promotion confidence score                 |
| `promoted_at` | `str`         | ISO8601 promotion timestamp                |
| `score`       | `float`       | Similarity score [0, 1]                    |

---

## Environment Variables

| Variable                              | Default         | Description                                                     |
|---------------------------------------|-----------------|-----------------------------------------------------------------|
| `CAIRN_VAULT_PATH`                    | `./vault`       | Absolute path to the Obsidian vault on the host                |
| `CAIRN_VAULT_COLLECTION`              | `vault-notes`   | ChromaDB collection name for promoted vault notes              |
| `CAIRN_CORROBORATION_N`               | `2`             | Minimum distinct agents for corroboration trigger              |
| `CAIRN_CORROBORATION_WINDOW_HOURS`    | `24`            | Time window (hours) for corroboration detection                |
| `CAIRN_PROMOTION_CONFIDENCE_THRESHOLD`| `0.7`           | Minimum confidence for agent self-nomination                   |
| `COUCHDB_USER`                        | `cairn`         | CouchDB admin username (for LiveSync)                          |
| `COUCHDB_PASSWORD`                    | —               | CouchDB admin password                                          |

---

## Setup Instructions

### 1. Pre-initialise the vault

The vault must be pre-initialised by Obsidian before starting the Cairn stack.
Cairn writes notes but never bootstraps a new vault.

```bash
# On your host machine, open the vault directory in Obsidian once.
# This creates the .obsidian/ folder.
# Then set CAIRN_VAULT_PATH in your .env to the absolute path.
```

### 2. Start the stack

```bash
cp .env.example .env
# Edit .env — set CAIRN_VAULT_PATH, CAIRN_GITLAB_TOKEN, CAIRN_GITLAB_PROJECT_ID,
#             COUCHDB_USER, COUCHDB_PASSWORD, CAIRN_SECRET_KEY
docker compose up -d

# Apply Phase 4 schema migration
docker compose exec cairn-api cairn-admin init-db
docker compose exec cairn-api cairn-admin migrate
```

### 3. Configure Obsidian LiveSync (optional)

To sync vault changes between the container and Obsidian clients:

1. Open the CouchDB admin UI: `http://<host>:5984/_utils`
2. Log in with `COUCHDB_USER` / `COUCHDB_PASSWORD`.
3. Create a database named `obsidian-vault` (or your preferred name).
4. In Obsidian → Settings → Obsidian LiveSync:
   - **Remote URI**: `http://<host>:5984/obsidian-vault`
   - **Username / Password**: your CouchDB credentials
   - Enable **LiveSync** mode.

CouchDB handles all client sync. Cairn only writes files to the bind-mounted
vault directory — replication from disk to CouchDB is managed by LiveSync.

### 4. Use the Promotion Queue UI

The web UI at `http://localhost:8000/ui` now includes a **Promotion Queue** tab.

- Open the tab to see all `pending_review` candidates.
- Enter your name/ID in the reviewer bar.
- Expand a card to review sources and edit the narrative.
- Click **Promote to vault** or **Dismiss**.

---

## Database Schema

### `promotion_candidates`

| Column               | Type    | Description                                              |
|----------------------|---------|----------------------------------------------------------|
| `id`                 | TEXT    | UUID v7 primary key                                      |
| `entity`             | TEXT    | Canonical entity value (IP, CVE, actor name, etc.)      |
| `entity_type`        | TEXT    | `ipv4` / `ipv6` / `fqdn` / `cve` / `technique` / `actor` |
| `trigger`            | TEXT    | `corroboration` / `human` / `agent`                     |
| `status`             | TEXT    | `pending_review` / `promoted` / `dismissed`             |
| `confidence`         | REAL    | Optional confidence score [0, 1]                        |
| `source_message_ids` | TEXT    | JSON array of blackboard message IDs                    |
| `narrative`          | TEXT    | Markdown narrative for the vault note ## Summary        |
| `reviewer_id`        | TEXT    | Set when status transitions to promoted/dismissed       |
| `vault_path`         | TEXT    | Vault-relative path of the written note                 |
| `created_at`         | TEXT    | ISO8601                                                  |
| `updated_at`         | TEXT    | ISO8601                                                  |
| `ext`                | TEXT    | JSON extension point                                     |

This table is in `index.db` (schema version 3).  Apply with:

```bash
cairn-admin migrate
```
