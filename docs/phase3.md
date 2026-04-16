# Phase 3 — Methodology Integration

This document describes the Phase 3 additions to the Cairn blackboard system:
GitLab methodology version control, ChromaDB semantic discovery, the validation
state machine, and the Sigma CI/CD pipeline.

---

## New endpoints

### Methodology discovery

| Method | Path | Operation ID | Auth |
|--------|------|-------------|------|
| `GET` | `/methodologies/search` | `search_methodologies` | Bearer |
| `POST` | `/methodologies/executions` | `create_methodology_execution` | Bearer |
| `GET` | `/methodologies/executions/{id}` | `get_methodology_execution` | Bearer |
| `PATCH` | `/methodologies/executions/{id}/status` | `update_methodology_status` | Bearer |

### Webhooks

| Method | Path | Operation ID | Auth |
|--------|------|-------------|------|
| `POST` | `/webhooks/gitlab` | `gitlab_webhook` | X-Gitlab-Token header |

---

## GET /methodologies/search

Semantic search against the ChromaDB methodology collection.

**Query parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | required | Natural-language search query |
| `n` | integer | 10 | Number of results (max 50) |

**Response:** Array of `MethodologySearchResult`

```json
[
  {
    "gitlab_path": "methodologies/lateral-movement/named-pipe.yml",
    "commit_sha":  "abc123def456...",
    "title":       "Named Pipe Matching Cobalt Strike Default Configuration",
    "tags":        ["attack.lateral-movement", "attack.t1021", "cobalt-strike"],
    "status":      "stable",
    "score":       0.9142
  }
]
```

No methodology text is returned. Use the `gitlab_path` + `commit_sha` to fetch
full content from GitLab when you decide to execute the methodology.

---

## POST /methodologies/executions

Record that an agent ran a methodology. Creates an execution record with status `proposed`.

**Request body:**

```json
{
  "methodology_id":    "cobalt-strike-named-pipe-default",
  "gitlab_path":       "methodologies/lateral-movement/named-pipe.yml",
  "commit_sha":        "abc123def456...",
  "parent_version":    null,
  "result_message_ids": ["msg-00234", "msg-00235"]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `methodology_id` | yes | Logical ID from the Sigma `name` field, or path-derived |
| `gitlab_path` | yes | Path to the `.yml` file in the GitLab repo |
| `commit_sha` | yes | Exact commit SHA that was executed |
| `parent_version` | no | SHA of the superseded parent methodology (lineage) |
| `result_message_ids` | no | Blackboard message IDs produced by this run |

---

## PATCH /methodologies/executions/{id}/status

Advance a methodology execution record through the validation state machine.

### State machine

```
proposed ──────────────────► peer_reviewed
    ▲                              │
    │◄─────────────────────────────┘
    │
    │   (human only: X-Human-Reviewer: true)
    ▼
deprecated ◄───────────────── peer_reviewed ──► validated
                                    ▲
                               (human only)
```

| Transition | Who can perform |
|-----------|----------------|
| `proposed → peer_reviewed` | Any authenticated agent |
| `peer_reviewed → proposed` | Any authenticated agent |
| `peer_reviewed → validated` | Human reviewer only |
| `any → deprecated` | Human reviewer only |

**Human reviewer headers (required for `validated` and `deprecated`):**

```
X-Human-Reviewer: true
X-Reviewer-Identity: alice@example.com
```

**Request body:**

```json
{
  "status": "peer_reviewed",
  "notes": "Confirmed this fires on HOST-DELTA during APT29 simulation."
}
```

**Error responses:**

- `403 Forbidden` — attempting a human-only transition without the required headers
- `422 Unprocessable Entity` — invalid target status or disallowed transition

---

## POST /webhooks/gitlab

Receives GitLab push webhook events and triggers ChromaDB sync.

**GitLab webhook setup:**

1. In GitLab project: **Settings → Webhooks**
2. Configure:
   - **URL**: `http://<cairn-host>/webhooks/gitlab`
   - **Secret token**: value of `CAIRN_GITLAB_WEBHOOK_SECRET` (leave empty to disable verification)
   - **Trigger**: Push events only
3. Set `CAIRN_GITLAB_WEBHOOK_SECRET` in Cairn's environment to the same value

**Behaviour:**

- Returns `202 Accepted` immediately; sync runs in the background
- On each push, fetches updated `.yml` files from `CAIRN_GITLAB_METHODOLOGY_DIR`
- Extracts Sigma metadata (title, description, tags, status) — never stores full text
- Upserts into ChromaDB collection `CAIRN_CHROMA_COLLECTION`
- Non-push events (`X-Gitlab-Event` ≠ `Push Hook`) are acknowledged and ignored

---

## Environment variables

All variables use the `CAIRN_` prefix. Set them in `.env` or via the container environment.

### GitLab

| Variable | Default | Description |
|----------|---------|-------------|
| `CAIRN_GITLAB_URL` | `http://gitlab` | Base URL of the GitLab instance. Self-hosted: `http://gitlab.local`. Cloud: `https://gitlab.com` |
| `CAIRN_GITLAB_TOKEN` | _(empty)_ | Personal or project access token with `read_repository` scope |
| `CAIRN_GITLAB_PROJECT_ID` | _(empty)_ | Numeric project ID (`42`) or namespaced path (`security-team/methodologies`) |
| `CAIRN_GITLAB_METHODOLOGY_DIR` | `methodologies` | Directory in the repo containing methodology `.yml` files |
| `CAIRN_GITLAB_WEBHOOK_SECRET` | _(empty)_ | Webhook secret token — must match GitLab project webhook configuration |

### ChromaDB

| Variable | Default | Description |
|----------|---------|-------------|
| `CAIRN_CHROMA_HOST` | `chromadb` | Hostname of the ChromaDB HTTP server |
| `CAIRN_CHROMA_PORT` | `8000` | Port of the ChromaDB HTTP server |
| `CAIRN_CHROMA_COLLECTION` | `methodologies` | ChromaDB collection name |

---

## Agent skill — Phase 3 methods

### `find_methodology(query, n=5) → list[MethodologyRef]`

Call this at the start of an investigation before attempting to derive methodology
from scratch. Returns ranked `MethodologyRef` dataclasses.

```python
async with BlackboardClient(base_url="...", api_key="...") as bb:
    refs = await bb.find_methodology(
        "named pipe lateral movement cobalt strike",
        n=5,
    )
    for ref in refs:
        print(ref.path, ref.sha, ref.score)
        # → methodologies/lateral-movement/named-pipe.yml  abc123...  0.914
```

`MethodologyRef` fields: `path`, `sha`, `title`, `tags`, `score`

### `record_execution(methodology_id, gitlab_path, commit_sha, ...) → ExecutionRecord`

Record that you ran a methodology. Returns the execution record UUID to pass
to analysts for review.

```python
rec = await bb.record_execution(
    methodology_id="cobalt-strike-named-pipe-default",
    gitlab_path="methodologies/lateral-movement/named-pipe.yml",
    commit_sha="abc123def456",
    result_message_ids=[result.id],
)
# rec.id — UUID to reference in status updates
# rec.status — "proposed"
```

---

## Docker Compose stack

```bash
# Start all services
docker compose up -d

# Wait for GitLab to finish initialising (~2-5 min on first boot)
docker compose logs -f gitlab | grep "GitLab is"

# Initialise Cairn databases
docker compose exec cairn-api cairn-admin init-db

# Create an agent
docker compose exec cairn-api cairn-admin agent create \
  --id osint-agent-01 \
  --name "OSINT Agent" \
  --capabilities "osint,threat-intel"
```

**Service ports (host):**

| Service | Host Port | Notes |
|---------|-----------|-------|
| cairn-api | 8000 | Blackboard API + web UI at /ui |
| chromadb | 8001 | ChromaDB HTTP API |
| gitlab | 8080 | GitLab web UI |
| gitlab | 2222 | GitLab SSH |

ChromaDB is only accessible on the host at port 8001; cairn-api reaches it
internally via `chromadb:8000` on the Docker network.

---

## Upgrading an existing database

If you have a Phase 1/2 database (schema version 1), run the migration to add
the `methodology_executions` table before starting the Phase 3 server:

```bash
# If running via Docker Compose:
docker compose exec cairn-api cairn-admin migrate

# If running locally:
cairn-admin migrate
```

The migration script (`cairn/db/migrations/001_add_methodology_tables.sql`)
is idempotent — it uses `CREATE TABLE IF NOT EXISTS` and is safe to run on
an already-migrated database.

---

## Discovery path (summary)

```
Agent starts investigation
        │
        ▼
find_methodology("natural language query")
        │  GET /methodologies/search → ChromaDB
        ▼
Ranked MethodologyRef list (path + sha + score)
        │
        ▼
Agent fetches full content from GitLab
        │  GET /api/v4/projects/{id}/repository/files/{path}?ref={sha}
        ▼
Agent executes methodology, posts results to blackboard
        │  POST /messages
        ▼
Agent records execution
        │  POST /methodologies/executions
        ▼
Analyst reviews → proposed → peer_reviewed → validated
```
