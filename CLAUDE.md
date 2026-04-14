# Cairn — Project Context & Development Guide

## What This Project Is

**Cairn** is a multi-agent knowledge sharing system built on the **Blackboard Pattern** — a classic AI coordination architecture where independent agents running on separate hosts communicate through a shared, structured knowledge store rather than directly with each other.

The name reflects the core idea: agents leave markers for each other, each one building on what came before, guiding those who follow. Like trail cairns, the system only works because every contributor adds to the stack.


The system is designed for a small cybersecurity team where human analysts work alongside AI agents. The central design constraint is that **both agents and humans must be able to read and act on the shared knowledge** — agents need structured queryable data, humans need readable narrative context. The architecture satisfies both without compromise.

This project was designed bottom-up from first principles, intentionally avoiding frameworks like LangGraph or CrewAI in order to understand the orchestration mechanics those tools abstract away. It is also designed to scale beyond a small team to 50-200+ person security organizations without fundamental architectural changes.

---

## Core Architecture Decisions

### Message Format: YAML Frontmatter + Markdown Body

Every message an agent posts to the blackboard is a markdown document with a YAML frontmatter envelope. This is the same format Obsidian uses internally.

```yaml
---
agent_id: osint-agent-01
timestamp: 2026-04-14T10:32:00Z
thread_id: apt29-campaign-thread
message_type: finding
in_reply_to: msg-00234
tags: [lateral-movement, apt29, named-pipes]
confidence: 0.87
promote: false
---

Observed named pipe `\.\pipe\msagent_81` on HOST-DELTA consistent with
Cobalt Strike default configuration. Cross-references IOC list from
msg-00198. Recommend correlating with logon events in 4-hour window.
```

The frontmatter is the agent-facing structured envelope. The markdown body is the human-readable narrative. The API parses both but stores them together as a single artifact.

### Storage: Two-Tier Architecture

**Tier 1 — SQLite Topic Databases (high-volume agent traffic)**

Separate SQLite databases per knowledge domain, each schema-optimized for its domain:

- `osint.db` — entities, relationships, sources, confidence scores
- `vulnerabilities.db` — CVEs, affected systems, CVSS scores, remediation status
- `network.db` — hosts, services, topology, anomalies
- `threat_actors.db` — TTPs, campaigns, IOCs, attribution
- `incidents.db` — timeline, artifacts, affected assets, chain of custody

Plus one routing database:

- `index.db` — registry of all topic databases, their locations, schemas, and metadata for agent query routing

Agents hit the index first, get routed to the right topic database, query there directly. Adding a new domain is just registering it in the index — agents discover it without code changes.

**Tier 2 — Obsidian Vault (curated human knowledge base)**

Significant findings are **promoted** from SQLite into the vault as proper markdown notes with wikilinks, tags, and backlinks. The vault is not a message store — it is a curated knowledge base. The graph view stays performant because promotion is controlled. Obsidian graph view degrades noticeably above ~10,000 notes; the SQLite tier absorbs high-volume traffic before it reaches the vault.

### API: Self-Describing OpenAPI Endpoint

The blackboard exposes a REST API with a queryable spec:

- `POST /messages` — agents write messages
- `GET /messages?since=<id>&tags=<tag>&db=<topic>` — agents and humans poll
- `GET /stream` — SSE endpoint for push-based agent subscriptions
- `GET /api/spec.json` — OpenAPI spec, fetched by agent skills on first use

The OpenAPI spec at `/api/spec.json` means the agent skill is self-updating. As endpoints are added, agents can discover them by re-fetching the spec without code changes to the skill itself.

### Agent Integration: Skill-Based, Not MCP

Agents interact with the blackboard via a **skill** — a self-contained API client the agent carries — rather than an MCP service. This makes agents self-sufficient; they only need the blackboard to be reachable over HTTP. The skill fetches the OpenAPI spec on first use (or periodically), caches it, and discovers new endpoints automatically.

### Methodology Storage: GitLab Repo

Detection methodologies, playbooks, and investigation pipelines live in a **separate GitLab repository**, not in the blackboard databases. This gives the team versioning, branching, merge requests, diff history, and CI/CD for free.

The blackboard SQLite layer stores only references and execution records:

| Field | Description |
|---|---|
| `methodology_id` | Unique ID |
| `gitlab_path` | Project path + file path in repo |
| `commit_sha` | Exact commit version executed |
| `status` | proposed / peer_reviewed / validated / deprecated |
| `execution_results` | Linked back to this SHA |

Methodology content lives in git. Execution history and outcomes live in SQLite. Clean boundary.

**Sigma Rule Format** — Methodologies should be written in Sigma rule format where applicable. Sigma compiles to CrowdStrike NG-SIEM queries, Splunk SPL, Elastic DSL, and others via `sigma-cli`. This provides cross-platform portability and enables CI/CD validation of detection logic before merge.

**Semantic Discovery via ChromaDB** — A sync job triggered by GitLab webhooks pulls methodology metadata and descriptions into a ChromaDB collection on each commit. When an agent starts an investigation, it queries ChromaDB semantically before attempting to derive methodology from scratch. Discovery path: ChromaDB. Retrieval path: GitLab API. Execution record path: SQLite.

### Promotion: SQLite → Obsidian Vault

Promotion is how significant agent findings become curated knowledge. Three trigger types:

1. **Corroboration-based (automatic)** — Same entity mentioned by N agents within a time window, or a finding corroborated across two independent sources. Most reliable signal; maps to how threat intelligence works in practice.
2. **Human-in-the-loop** — Web UI shows high-signal unreviewed messages with one-click promote. Analyst can edit narrative before promoting.
3. **Agent self-nomination** — Agent sets `promote: true` in frontmatter or confidence above threshold. Treated as a candidate, not an automatic promotion. Requires human approval initially until thresholds are tuned.

The promotion process on any trigger:
- Extract entities (hostnames, IPs, CVE IDs, actor names) and convert to wikilinks
- Check for existing note on this entity — append/update rather than duplicate
- Preserve link back to originating SQLite record for evidence chain
- Carry forward frontmatter tags into the note

### Methodology Knowledge Compounding

When Analyst A's agent composes a detection pipeline, it is committed to the GitLab methodology repo as `proposed`. When Analyst B's agent executes it and posts results back referencing the commit SHA, it enters `peer_reviewed`. Human sign-off moves it to `validated`. This is the compounding mechanism — each analyst's experience builds on the others'.

Methodology versioning uses explicit parent references (`parent_version` field) so agents can query lineage and know when they are running a superseded version. When an agent retrieves a methodology and the current HEAD SHA differs from the SHA in its last execution record, it flags this automatically.

---

## Technology Stack

| Component | Technology | Rationale |
|---|---|---|
| Blackboard API | Python / FastAPI | Async, OpenAPI generation built in |
| Topic databases | SQLite → PostgreSQL at scale | Simple now, clear migration path |
| Vector search | ChromaDB | Already in use; methodology semantic search |
| Methodology repo | GitLab | Versioning, MR workflow, CI/CD, webhooks |
| Agent skills | Python HTTP client | Self-updating via OpenAPI spec fetch |
| Human UI | Lightweight web app | Serves markdown rendered, SSE stream |
| Knowledge base | Obsidian vault | Graph view, Bases, wikilinks, YAML frontmatter |
| Detection format | Sigma rules | Cross-platform, CI/CD validatable |

---

## Development Phases

### Phase 1 — Blackboard Core
*Everything else depends on this. Build it first, get it stable.*

- [ ] Define message schema (YAML frontmatter fields, required vs optional)
- [ ] Design `index.db` schema with cross-domain query support in mind
- [ ] Design `osint.db` and `vulnerabilities.db` schemas as first two topic databases
- [ ] Implement FastAPI service with core endpoints: `POST /messages`, `GET /messages`, `GET /stream` (SSE), `GET /api/spec.json`
- [ ] Message ingest pipeline: parse frontmatter, route to correct topic database, store raw markdown alongside structured fields
- [ ] Basic web UI: browse messages rendered as markdown, filter by tag/agent/thread
- [ ] Authentication: per-agent API keys

### Phase 2 — Agent Skill
*The client library agents use to interact with the blackboard.*

- [ ] OpenAPI spec fetcher and local cache with TTL
- [ ] `post_message()` — compose and post YAML frontmatter + markdown
- [ ] `query_messages()` — query by tag, agent, thread, time window, topic database
- [ ] `subscribe()` — SSE subscription wrapper
- [ ] `flag_for_promotion()` — set `promote: true` with optional confidence score
- [ ] Skill self-update logic when spec endpoint returns a newer version

### Phase 3 — Methodology Integration
*Connect the GitLab methodology repo and ChromaDB discovery layer.*

- [ ] GitLab API integration: fetch methodology by path + SHA, list methodologies by tag
- [ ] Methodology execution record table in SQLite: SHA, results, status, lineage
- [ ] ChromaDB sync job triggered by GitLab webhook on commit
- [ ] Semantic search endpoint: `GET /methodologies/search?q=<query>`
- [ ] Agent skill method: `find_methodology()` — semantic query before investigation start
- [ ] Validation state machine: proposed → peer_reviewed → validated → deprecated
- [ ] Sigma rule CI/CD pipeline in GitLab methodology repo

### Phase 4 — Obsidian Vault Bridge
*Promotion pipeline from SQLite into curated knowledge.*

- [ ] Corroboration detection job: identify messages referencing same entity from N agents
- [ ] Human promotion UI: review queue, one-click promote, narrative edit before write
- [ ] Entity extractor: parse markdown body for hostnames, IPs, CVEs, actor names
- [ ] Wikilink resolver: check vault for existing note, generate link or create stub
- [ ] Vault writer: produce properly structured markdown note with frontmatter, wikilinks, source attribution
- [ ] Deduplication: append/update existing notes rather than creating duplicates
- [ ] GitLab webhook → ChromaDB sync (also needed here for vault-level methodology links)

### Phase 5 — Scale and Hardening
*Address the friction points that appear as the team grows.*

- [ ] Migrate SQLite topic databases to PostgreSQL for concurrent write support
- [ ] Namespace partitioning in index database (by team/organizational unit)
- [ ] Agent identity and authorization beyond API keys
- [ ] Blackboard API load balancing and health checks
- [ ] Observability: structured logging, OpenTelemetry traces on agent interactions
- [ ] CODEOWNERS in methodology repo for domain-based review authority

---

## Key Design Principles to Preserve

**Agents and humans share the same artifact.** Never build a separate human view that diverges from what agents read. The YAML+markdown format is the contract.

**SQLite is the inbox, the vault is the knowledge base.** High volume goes to SQLite. Significance earns promotion to the vault. The vault stays curated and the graph stays performant.

**GitLab owns methodology content, SQLite owns execution history.** Never store methodology text in the database. Store the commit SHA so you always know exactly what was run.

**Human sign-off on validation until trust is established.** Agents can propose and peer-review methodologies freely. Only humans can mark something `validated` until the peer-review signal is reliable enough to trust.

**The OpenAPI spec is the API contract.** Never hardcode endpoints in the agent skill. Always derive from the spec so agents self-update as the API evolves.

---

## Related Standards and Prior Art

- **Google A2A Protocol** — Open agent-to-agent communication standard (Apache 2.0, Linux Foundation). Built on HTTP, SSE, JSON-RPC. Complements Anthropic MCP. Worth reviewing the spec for agent discovery patterns even if not directly adopted. `github.com/a2aproject/A2A`
- **Sigma Rules** — Open standard for detection rule format. `github.com/SigmaHQ/sigma`
- **MCP (Model Context Protocol)** — Anthropic's tool/context protocol. The agent skill in this project is not MCP but operates alongside it.
- **Blackboard Pattern** — Hayes-Roth, 1985. Classic AI architecture, recently validated in LLM multi-agent research showing 13–57% improvement over master-slave paradigms.

---

## Starting the First Claude Code Session

Suggested opening prompt for Phase 1:

```
I'm building an agent blackboard system for multi-agent cybersecurity 
knowledge sharing. The CLAUDE.md in this repo contains the full 
architectural context. 

Today's task: Design the SQLite schema for index.db and the first 
two topic databases (osint.db and vulnerabilities.db). The index.db 
must support cross-domain query routing and have the domain 
partitioning rationale from ARCHITECTURE.md in mind. Start by 
proposing the schema before writing any code so we can review it first.
```
