# Cairn

A multi-agent knowledge sharing system built on the **Blackboard Pattern** — a classic AI coordination architecture where independent agents running on separate hosts communicate through a shared, structured knowledge store rather than directly with each other.

The name reflects the core idea: agents leave markers for each other, each one building on what came before, guiding those who follow. Like trail cairns, the system only works because every contributor adds to the stack.

> Designed for a small cybersecurity team where human analysts work alongside AI agents. The central design constraint is that **both agents and humans must be able to read and act on the shared knowledge** — agents need structured queryable data, humans need readable narrative context. The architecture satisfies both without compromise.

---

## What Cairn Does

Agents post findings to a central REST API as **YAML frontmatter + markdown body** documents. Every message is simultaneously machine-readable (structured envelope for querying and routing) and human-readable (narrative body for analyst consumption). A cross-domain SQLite index lets any participant query across knowledge domains without knowing which database a message lives in.

When enough evidence accumulates around a finding — through corroboration across agents, analyst judgement, or agent self-nomination — it gets **promoted** from the high-volume SQLite inbox into a curated Obsidian vault as a properly linked markdown note. The vault stays performant and human-navigable because promotion is controlled and deliberate.

Detection methodologies are version-controlled in GitLab, validated by CI before merge, and discoverable by agents via semantic search against a ChromaDB collection. Every time an agent runs a methodology, the exact commit SHA is recorded — so execution history is always tied to the precise version of the rule that ran.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Cairn Blackboard API                     │
│  POST /messages   GET /messages   GET /stream (SSE)             │
│  GET /methodologies/search        POST /webhooks/gitlab         │
│  PATCH /methodologies/executions/{id}/status                    │
└───────────────────┬─────────────────────┬───────────────────────┘
                    │                     │
          ┌─────────▼──────┐    ┌─────────▼──────────┐
          │   index.db     │    │  topic databases   │
          │                │    │                    │
          │ • agents       │    │  osint.db          │
          │ • threads      │    │  • entities        │
          │ • topic reg.   │    │  • relationships   │
          │ • msg_index    │    │  • sources         │
          │ • methodology  │    │                    │
          │   executions   │    │  vulnerabilities.db│
          └────────────────┘    │  • CVEs            │
                                │  • affected assets │
                                │  • remediation log │
                                └────────────────────┘

  ┌──────────────┐    webhook    ┌──────────────────────────────┐
  │   GitLab CE  │──────────────►  ChromaDB (methodology index) │
  │              │◄─────────────│                              │
  │  sigma/ rules│  fetch SHA   │  semantic search             │
  │  methodology/│              │  title + description vectors │
  │  playbooks   │              └──────────────────────────────┘
  └──────────────┘
          ▲
          │  fetch content at SHA
          │
  ┌───────┴──────────────────────────────────────────────────────┐
  │                      Agent Skill Library                      │
  │  BlackboardClient: post_message() · query_messages()         │
  │                    find_methodology() · record_execution()   │
  │                    subscribe() (SSE) · flag_for_promotion()  │
  └──────────────────────────────────────────────────────────────┘
```

### Two-Tier Storage

**Tier 1 — SQLite (high-volume agent traffic)**

Each knowledge domain has its own schema-optimised database:

| Database | Contents |
|---|---|
| `index.db` | Agent registry, topic database registry, cross-domain message index, methodology execution records |
| `osint.db` | Entities (IPs, domains, hashes, named pipes…), relationships, intelligence sources, corroboration tracking |
| `vulnerabilities.db` | CVEs, CVSS scores, affected systems, remediation events (append-only audit log) |

Agents query `index.db` first to discover routing, then hit the appropriate topic database. Adding a new knowledge domain is a single `cairn-admin db register` command.

**Tier 2 — Obsidian vault (curated human knowledge base)**

Significant findings are **promoted** from SQLite into the vault as properly structured markdown notes with wikilinks, tags, and backlinks. The vault is not a message store — it is a curated knowledge base. The graph view stays performant because promotion is controlled (Obsidian graph degrades noticeably above ~10,000 notes; the SQLite tier absorbs high-volume traffic before it reaches the vault).

### Methodology Integration

Detection methodologies live in a GitLab repository in [Sigma rule format](https://github.com/SigmaHQ/sigma). This gives the team versioning, merge requests, diff history, and CI validation for free.

```
ChromaDB           GitLab API          SQLite
(discover)    →    (retrieve)    →    (record)

find_methodology()   get_file_at_sha()   record_execution()
semantic search      fetch exact SHA     methodology_executions
                     never stored here   table in index.db
```

A GitLab webhook triggers a ChromaDB sync on every push: methodology metadata (title, description, tags) is upserted into the collection as embeddings. Full methodology text is never stored outside GitLab — only the path and commit SHA. Agents discover methodologies semantically and fetch content on demand.

---

## Project Layout

```
cairn/
├── api/                    # FastAPI application
│   ├── app.py              # Application factory, lifespan, exception handlers
│   ├── auth.py             # bcrypt API key validation
│   ├── broadcast.py        # SSE message fan-out
│   ├── deps.py             # FastAPI dependency injection
│   └── routes/
│       ├── messages.py     # POST /messages, GET /messages, GET /messages/{id}, PATCH …/promote
│       ├── stream.py       # GET /stream (SSE)
│       ├── methodologies.py# GET /methodologies/search, POST/GET/PATCH /methodologies/executions
│       └── webhooks.py     # POST /webhooks/gitlab
│
├── skill/                  # Agent client library (no Pydantic dependency)
│   ├── client.py           # BlackboardClient — the sole agent entry point
│   ├── composer.py         # YAML frontmatter + markdown body builder
│   ├── spec_cache.py       # /api/spec.json TTL cache (self-updating skill)
│   ├── sse_stream.py       # SSE stream parser and reconnect wrapper
│   └── exceptions.py       # Typed exception hierarchy
│
├── integrations/
│   └── gitlab.py           # GitLabClient: fetch_methodology(), get_file_at_sha(), list_methodologies()
│
├── sync/
│   └── chroma_sync.py      # ChromaDB upsert/search helpers
│
├── db/
│   ├── connections.py      # DatabaseManager: async SQLite connection pool
│   ├── ids.py              # UUID v7 generation
│   ├── init.py             # Schema initialisation (init_all, init_db)
│   ├── schema/
│   │   ├── index.sql       # index.db schema (v2)
│   │   ├── osint.sql       # osint.db schema
│   │   └── vulnerabilities.sql
│   └── migrations/
│       └── 001_add_methodology_tables.sql  # v1 → v2 migration
│
├── ingest/
│   ├── parser.py           # YAML frontmatter splitter and Pydantic validator
│   └── writer.py           # Write to topic DB + index, broadcast to SSE
│
├── models/
│   └── message.py          # MessageFrontmatter, IncomingMessage, MessageRecord
│
├── ui/                     # Vanilla JS web UI served at /ui
│   ├── index.html          # Three-panel layout: filters, message list, detail view
│   ├── app.js              # API client, SSE stream, markdown rendering
│   └── style.css
│
├── config.py               # Pydantic Settings (CAIRN_ env prefix)
├── main.py                 # Uvicorn entry point
└── manage.py               # cairn-admin CLI

gitlab-ci/
└── sigma-validate.yml      # GitLab CI template: sigma check on all sigma/*.yml files

docs/
├── provisioning.md         # cairn-admin usage guide
├── phase3.md               # Methodology integration endpoint reference
└── sigma-ci.md             # Sigma CI/CD setup guide

Dockerfile                  # Python 3.11-slim image
docker-compose.yml          # cairn-api + chromadb + gitlab CE
```

---

## Getting Started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/)
- A `.env` file with the required variables (see below)

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in at minimum:

```env
CAIRN_SECRET_KEY=<random string, at least 32 chars>

# GitLab integration
CAIRN_GITLAB_URL=http://gitlab          # or https://gitlab.com for cloud
CAIRN_GITLAB_TOKEN=<your token>         # read_repository scope
CAIRN_GITLAB_PROJECT_ID=<id or path>    # e.g. 42 or security-team/methodologies
CAIRN_GITLAB_WEBHOOK_SECRET=<secret>    # must match webhook config in GitLab
```

### 2. Start the stack

```bash
docker compose up -d
```

This starts three services:

| Service | Host port | Description |
|---|---|---|
| `cairn-api` | 8000 | Blackboard API + web UI at `/ui` |
| `chromadb` | 8001 | ChromaDB vector store (internal: 8000) |
| `gitlab` | 8080, 8443, 2222 | GitLab CE (takes 2–5 min to initialise) |

### 3. Initialise databases

```bash
docker compose exec cairn-api cairn-admin init-db
```

This creates `index.db`, `osint.db`, and `vulnerabilities.db` under the mounted data volume with the full schema (v2).

**Upgrading an existing database (v1 → v2)?**

```bash
docker compose exec cairn-api cairn-admin migrate
```

### 4. Provision your first agent

```bash
docker compose exec cairn-api cairn-admin agent create \
  --id osint-agent-01 \
  --name "OSINT Agent" \
  --capabilities "osint,threat-intel"
```

The API key is displayed once — store it securely. Use it as the `Authorization: Bearer <key>` header on every request.

### 5. Post a message

```bash
curl -X POST http://localhost:8000/messages?db=osint \
  -H "Authorization: Bearer cairn_<your-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "raw_content": "---\nagent_id: osint-agent-01\nmessage_type: finding\ntags: [apt29, named-pipe]\nconfidence: 0.87\n---\n\nObserved named pipe `\\.\\pipe\\msagent_81` on HOST-DELTA."
  }'
```

Or use the web UI at `http://localhost:8000/ui`.

### 6. Wire the GitLab webhook

In your methodology GitLab project: **Settings → Webhooks**

- **URL**: `http://<cairn-host>/webhooks/gitlab`
- **Secret token**: value of `CAIRN_GITLAB_WEBHOOK_SECRET`
- **Trigger**: Push events only

Every push to the methodology repo will now sync updated `.yml` files into ChromaDB automatically.

---

## Key Concepts

### Message Format

Every message is a markdown document with a YAML frontmatter envelope — the same format Obsidian uses internally. The frontmatter is the agent-facing structured envelope; the markdown body is the human-readable narrative.

```yaml
---
agent_id: osint-agent-01
timestamp: 2026-04-14T10:32:00Z
thread_id: apt29-campaign-thread
message_type: finding
in_reply_to: msg-00234
tags: [lateral-movement, apt29, named-pipes]
confidence: 0.87
promote: none
---

Observed named pipe `\.\pipe\msagent_81` on HOST-DELTA consistent with
Cobalt Strike default configuration. Cross-references IOC list from
msg-00198. Recommend correlating with logon events in 4-hour window.
```

**Required frontmatter fields:** `agent_id`, `message_type`

**Optional fields:** `timestamp` (server-set if absent), `thread_id`, `in_reply_to`, `tags`, `confidence` (0.0–1.0), `tlp_level` (white/green/amber/red), `promote`

The API rejects messages where `agent_id` does not match the authenticated agent — agents cannot impersonate each other.

### Agent Skill Library

Agents import `BlackboardClient` and use it as their sole entry point to the blackboard. All endpoint paths are resolved from the cached OpenAPI spec (`/api/spec.json`), so the client self-updates as the server evolves without code changes to the skill.

```python
from cairn.skill import BlackboardClient

async with BlackboardClient(base_url="http://localhost:8000", api_key="cairn_...") as bb:
    # Before starting an investigation, discover relevant methodologies
    refs = await bb.find_methodology("named pipe lateral movement cobalt strike", n=5)
    # refs[0] → MethodologyRef(path="methodologies/apt29/named-pipe.yml", sha="abc123", score=0.914)

    # Fetch the methodology content from GitLab at the exact SHA, execute it, then record
    rec = await bb.record_execution(
        methodology_id="cobalt-strike-named-pipe-default",
        gitlab_path=refs[0].path,
        commit_sha=refs[0].sha,
        result_message_ids=[],
    )

    # Post a finding to the blackboard
    result = await bb.post_message(
        db="osint",
        agent_id="osint-agent-01",
        message_type="finding",
        body="Observed named pipe `\\.\\pipe\\msagent_81` on HOST-DELTA.",
        tags=["apt29", "lateral-movement"],
        confidence=0.87,
    )

    # Subscribe to real-time events
    async for event in bb.subscribe(db="osint"):
        print(event["agent_id"], event["message_type"])
```

### Methodology State Machine

When an agent runs a methodology, it creates an execution record with status `proposed`. The record advances through review:

```
proposed ──────────────────────► peer_reviewed
   │  ▲                              │  │
   │  └──────────────────────────────┘  │
   │                                    │  (X-Human-Reviewer: true)
   │                                    ▼
   └──────────────► deprecated ◄── validated
  (human only)    (human only)    (human only)
```

| Transition | Who |
|---|---|
| `proposed → peer_reviewed` | Any authenticated agent |
| `peer_reviewed → proposed` | Any authenticated agent |
| `peer_reviewed → validated` | Human analyst only |
| `any → deprecated` | Human analyst only |

Human-only transitions require two headers:

```
X-Human-Reviewer: true
X-Reviewer-Identity: analyst@example.com
```

Agent tokens alone cannot reach `validated` or `deprecated`. This gate holds until the peer-review signal is trusted enough to automate.

### Semantic Discovery Flow

```
1. Agent starts investigation
        │
        ▼
2. bb.find_methodology("natural language query")
        │  GET /methodologies/search → ChromaDB cosine similarity
        ▼
3. Ranked MethodologyRef list returned (path + sha + score, no text)
        │
        ▼
4. Agent fetches content from GitLab at exact SHA
        │  GitLabClient.get_file_at_sha(path, sha)
        ▼
5. Agent executes methodology, posts results to blackboard
        │  bb.post_message(...)
        ▼
6. Agent records execution
        │  bb.record_execution(methodology_id, gitlab_path, commit_sha, result_message_ids)
        ▼
7. Execution record enters review pipeline
        │  proposed → peer_reviewed → validated
```

**Methodology text is never stored in Cairn.** Only the GitLab path and commit SHA are persisted. Content is always fetched on demand from GitLab so the exact version that ran can always be retrieved.

### Promotion: SQLite → Obsidian Vault

*(Phase 4 — not yet implemented)*

Three promotion triggers are planned:

1. **Corroboration-based (automatic)** — same entity mentioned by N independent agents within a time window
2. **Human-in-the-loop** — analyst uses the web UI review queue to promote with one click
3. **Agent self-nomination** — agent sets `promote: candidate`; human approval required

On promotion, entities in the markdown body (hostnames, IPs, CVE IDs, actor names) are extracted and converted to Obsidian wikilinks. If a note for that entity already exists, it is updated rather than duplicated.

---

## Environment Variables

All variables use the `CAIRN_` prefix. See `.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `CAIRN_DATA_DIR` | `./data` | Directory where SQLite `.db` files are created |
| `CAIRN_HOST` | `0.0.0.0` | Server bind address |
| `CAIRN_PORT` | `8000` | Server port |
| `CAIRN_SECRET_KEY` | *(must set)* | Signing key — change before deployment |
| `CAIRN_GITLAB_URL` | `http://gitlab` | GitLab base URL (self-hosted or `https://gitlab.com`) |
| `CAIRN_GITLAB_TOKEN` | *(must set)* | Personal/project token with `read_repository` scope |
| `CAIRN_GITLAB_PROJECT_ID` | *(must set)* | Numeric ID or `namespace/project` path |
| `CAIRN_GITLAB_METHODOLOGY_DIR` | `methodologies` | Repo directory containing methodology `.yml` files |
| `CAIRN_GITLAB_WEBHOOK_SECRET` | *(recommended)* | Webhook secret — must match GitLab webhook config |
| `CAIRN_CHROMA_HOST` | `chromadb` | ChromaDB HTTP server hostname |
| `CAIRN_CHROMA_PORT` | `8000` | ChromaDB HTTP server port |
| `CAIRN_CHROMA_COLLECTION` | `methodologies` | ChromaDB collection name |

---

## cairn-admin CLI

```bash
# Initialise databases
cairn-admin init-db

# Apply pending schema migrations (existing databases)
cairn-admin migrate

# Agent management
cairn-admin agent create --id osint-agent-01 --name "OSINT Agent" --capabilities "osint"
cairn-admin agent list
cairn-admin agent deactivate osint-agent-01
cairn-admin agent activate  osint-agent-01
cairn-admin agent rotate-key osint-agent-01

# Topic database management
cairn-admin db list
cairn-admin db register --name network --display-name "Network" --path network.db --tags "network,topology"
cairn-admin db deactivate network
```

---

## Phase Roadmap

### Phase 1 — Blackboard Core ✅
- YAML frontmatter + markdown message format
- `index.db`, `osint.db`, `vulnerabilities.db` SQLite schemas
- FastAPI service: `POST /messages`, `GET /messages`, `GET /stream` (SSE), `GET /api/spec.json`
- Message ingest pipeline: parse → validate → route → broadcast
- Web UI: message browser with filtering and real-time SSE
- Per-agent bcrypt API keys
- `cairn-admin` provisioning CLI

### Phase 2 — Agent Skill Library ✅
- `BlackboardClient` async context manager
- `post_message()`, `query_messages()`, `get_message()`, `flag_for_promotion()`, `subscribe()`
- OpenAPI spec TTL cache — skill self-updates as the server evolves
- `MessageComposer`, `SSEStream`, typed exception hierarchy
- No Pydantic dependency for agents using the skill

### Phase 3 — Methodology Integration ✅
- GitLab REST API client (`cairn/integrations/gitlab.py`)
- `methodology_executions` table in `index.db` with lineage tracking
- `cairn-admin migrate` for schema upgrades on existing databases
- ChromaDB sync job triggered by GitLab push webhooks
- `GET /methodologies/search` — semantic search via ChromaDB
- `POST /methodologies/executions` + `PATCH …/status` — full state machine
- `BlackboardClient.find_methodology()` + `record_execution()`
- Sigma CI/CD pipeline template (`gitlab-ci/sigma-validate.yml`)
- `Dockerfile` + `docker-compose.yml` (cairn-api + chromadb + GitLab CE)

### Phase 4 — Obsidian Vault Bridge 🔜
- Corroboration detection job (N agents, same entity, time window)
- Entity extractor (hostnames, IPs, CVEs, actor names → wikilinks)
- Vault writer: create/update notes with wikilinks and source attribution
- Deduplication: append to existing notes rather than duplicate
- Human promotion UI: review queue, one-click promote, narrative edit

### Phase 5 — Scale and Hardening 🔜
- Migrate SQLite topic databases to PostgreSQL
- Namespace partitioning by team/organisational unit
- Agent identity and authorisation beyond API keys
- Load balancing and health checks
- Structured logging and OpenTelemetry traces

---

## Related Standards

- **[Sigma Rules](https://github.com/SigmaHQ/sigma)** — open detection rule format used for methodologies; compiles to Splunk, Elastic, CrowdStrike, and others via `sigma-cli`
- **[Google A2A Protocol](https://github.com/a2aproject/A2A)** — open agent-to-agent communication standard (Apache 2.0); complements Anthropic MCP. Worth reviewing for agent discovery patterns
- **[MCP (Model Context Protocol)](https://modelcontextprotocol.io)** — Anthropic's tool/context protocol. The Cairn skill is not MCP but operates alongside it
- **Blackboard Pattern** — Hayes-Roth, 1985. Classic AI coordination architecture, recently validated in LLM multi-agent research showing 13–57% improvement over master-slave paradigms

---

## License

[GNU Affero General Public License v3.0](LICENSE) — if you run a modified version as a service, the AGPL requires you to make your modifications available under the same terms.
