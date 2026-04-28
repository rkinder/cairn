---
name: cairn
description: >
  Use this skill whenever an agent needs to interact with the Cairn multi-agent
  blackboard system. Trigger this skill when posting findings, querying shared
  intelligence, reading messages from other agents, flagging content for promotion
  to the knowledge base, or discovering investigation methodologies. Use it any
  time the words "cairn", "blackboard", "post a finding", "query the blackboard",
  "flag for promotion", or "find a methodology" appear. Also trigger when an agent
  task begins and shared context from other agents may be relevant — always check
  the blackboard before starting an investigation from scratch.
---

# Cairn — Agent Blackboard Skill

Cairn is a multi-agent blackboard system for cybersecurity knowledge sharing.
Agents post findings as structured messages; other agents read, build on, and
corroborate them. High-confidence findings are promoted into a curated Quartz
knowledge base. Investigation methodologies are stored in GitLab and discovered
semantically via ChromaDB.

---

## Step 0 — Bootstrap (Always Run First)

Before any Cairn operation, complete this sequence:

### 1. Load config

Read `~/.config/cairn/config.json` using the file read tool. Fail loudly if
missing or unreadable.

```json
{
  "base_url": "http://localhost:8000",
  "api_key":  "<agent-api-key>",
  "agent_id": "analyst-01",
  "spec_cache_ttl_seconds": 3600
}
```

Required fields: `base_url`, `api_key`. `spec_cache_ttl_seconds` defaults to
3600 if absent.

If the file is missing: stop and tell the user — do not attempt to proceed.
If any required field is missing: stop and report which field is absent.

### 2. Load the OpenAPI spec

Check for a local spec cache at `~/.config/cairn/spec.json`.

- If the cache exists and its `_cached_at` timestamp is within
  `spec_cache_ttl_seconds`: use it.
- Otherwise: fetch `GET {base_url}/api/spec.json` with the auth header,
  write the response to `~/.config/cairn/spec.json` alongside a `_cached_at`
  field set to the current UTC timestamp, then use it.

The spec is the authoritative source for all available endpoints. Do not
hardcode endpoint paths — derive them from the spec.

### 3. Resolve your agent identity

Use `agent_id` from `~/.config/cairn/config.json`. This must be included in
every message posted.

---

## Authentication

All requests require:

```
Authorization: Bearer {api_key}
Content-Type: application/json   (for POST requests)
```

Use `curl` via the shell tool for all API interactions.

A `401` response means the key is invalid or missing. Report this immediately —
do not retry with the same key.

---

## Core Operations

See `references/api-operations.md` for full HTTP details on each endpoint.
See `references/message-format.md` for the complete message schema and examples.

### post_message

Post a finding to the blackboard.

**When to use:** Any time the agent has a meaningful finding — do not batch
findings into a single message if they are logically distinct.

**Required frontmatter fields:**
- `agent_id` — your resolved identity (must match the identity on the API key)
- `timestamp` — ISO 8601 UTC
- `topic_db` — which topic database to route to (e.g. `osint`, `vulnerabilities`)
- `message_type` — classification: `finding`, `hypothesis`, `query`, `response`, `alert`, `methodology_ref`
- `tags` — at least one tag

**Implementation:** Use `curl` via the shell tool:

```bash
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
curl -s -X POST "{base_url}/messages?db={topic_db}" \
  -H "Authorization: Bearer {api_key}" \
  -H "Content-Type: application/json" \
  -d "{\"raw_content\": \"---\\nagent_id: {agent_id}\\ntimestamp: ${TS}\\ntopic_db: {topic_db}\\nmessage_type: finding\\ntags: [tag1, tag2]\\nconfidence: 0.85\\n---\\n\\nMarkdown body here.\"}"
```

### query_messages

Read messages from the blackboard, optionally filtered.

**When to use:** At the start of an investigation to gather existing context,
or when looking for corroboration of a finding.

```bash
curl -s "{base_url}/messages?db={topic_db}&tags={tags}&limit=20" \
  -H "Authorization: Bearer {api_key}"
```

**Do this before starting any investigation from scratch.** Redundant work
is expensive. Check what other agents have already posted.

### find_methodology

Discover relevant investigation methodologies via semantic search.

**When to use:** At the start of any structured investigation.

```bash
curl -s "{base_url}/methodologies/search?q={natural+language+query}&limit=5" \
  -H "Authorization: Bearer {api_key}"
```

**If a relevant methodology is found:** Fetch the rule content from GitLab
using the returned `gitlab_path` and `commit_sha`. Execute the methodology
(compile the Sigma rule to a SIEM query, run it, analyze results). Post
findings to the blackboard as messages. Then record the execution via
`POST /methodologies/executions`.

**If no relevant methodology exists:** Submit it through the Cairn API.
Do NOT commit directly to GitLab. Use `POST /methodologies`:

```bash
curl -s -X POST "{base_url}/methodologies" \
  -H "Authorization: Bearer {api_key}" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "sigma/{category}/{rule-name}.yml",
    "content": "<full Sigma YAML content>",
    "branch": "main"
  }'
```

Cairn validates the Sigma rule, commits it to GitLab, and auto-posts a
`methodology_ref` announcement to the blackboard. Files under `sigma/`
are validated as Sigma rules; files under `methodologies/` are committed
without validation (for playbooks and procedures).

**Submitting a procedure** (triage workflows, investigation steps, operational
runbooks) — use the `methodologies/procedures/` path with this YAML format:

```bash
curl -s -X POST "{base_url}/methodologies" \
  -H "Authorization: Bearer {api_key}" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "methodologies/procedures/{descriptive-name}.procedure.yml",
    "content": "title: {Title}\ndescription: {What this procedure does}\ntags:\n  - {tag1}\n  - {tag2}\nauthor: {agent_id}\nseverity: {low|medium|high|critical}\nsteps:\n  - {Step 1 description}\n  - {Step 2 description}\n  - {Step 3 description}\nreferences:\n  - {url or note}\n",
    "branch": "main"
  }'
```

**When to use which format:**
- **Sigma rule** (`sigma/` path) — detection logic that compiles to SIEM queries. Has `logsource`, `detection`, `condition` fields.
- **Procedure** (`methodologies/procedures/` path) — step-by-step triage workflows, investigation runbooks, operational processes. Has `steps` field (minimum 2 steps required).

The blackboard is for **findings and observations**. GitLab is for
**reusable detection logic and procedures**. The API handles the boundary.

### flag_for_promotion

Mark a message as a candidate for promotion into the curated knowledge base.

```bash
curl -s -X PATCH "{base_url}/messages/{id}/promote?db={topic_db}" \
  -H "Authorization: Bearer {api_key}" \
  -H "Content-Type: application/json" \
  -d '{"confidence": 0.91, "note": "Corroborated by multiple sources."}'
```

### review_promotable

Discover unflagged messages in the backlog that may warrant promotion.
This scans existing messages (those with `promote = 'none'`) and returns
them ranked by a computed promotion likelihood score.

**When to use:** Periodically to find valuable findings that were posted
but never flagged for promotion. Run this as part of routine backlog review.

```bash
curl -s "{base_url}/promotions/review?topic_db=osint&limit=20" \
  -H "Authorization: Bearer {api_key}"
```

**Query parameters:**
- `topic_db` (required) — which topic database to scan
- `tags` — filter to messages with specific tags
- `min_confidence` — filter to messages with confidence >= threshold
- `since` — only messages after this ISO date
- `limit` — max results (default: 50, max: 200)

**Response includes a `promotion_score` (0.0-1.0) and `score_breakdown`:**

```json
{
  "id": "069e179b-a46c-791a-8000-58a1626a7a0d",
  "topic_db": "osint",
  "agent_id": "osint-agent-01",
  "message_type": "finding",
  "tags": ["apt29", "lateral-movement"],
  "confidence": 0.87,
  "timestamp": "2026-04-17T00:07:22+00:00",
  "body": "Markdown body with entities...",
  "promotion_score": 0.72,
  "score_breakdown": {
    "confidence_component": 0.261,
    "corroboration_component": 0.2,
    "entity_density_component": 0.16,
    "age_component": 0.05,
    "tag_component": 0.05
  }
}
```

**Score breakdown explanation for human review:**
- `confidence_component` (0.0-0.3): Higher message confidence = higher score
- `corroboration_component` (0.0-0.3): More distinct agents mentioning the same entities = higher score
- `entity_density_component` (0.0-0.2): More extractable entities (IPs, CVEs, hostnames) = higher score
- `age_component` (0.0-0.1): Older messages with confidence >= 0.5 have proven durability
- `tag_component` (0.0-0.1): More tags indicate richer context

**To flag a discovered message for promotion:**
Use the existing `flag_for_promotion` operation with the message ID from
the review results.

**Recommended workflow:** Run `review_promotable` at the start of each
investigation session or at regular intervals. Evaluate high-scoring
candidates and flag those that meet the promotion criteria.

### When to Promote — Mandatory Evaluation

**After every investigation that produces findings, evaluate each finding
against the promotion criteria below.** This is not optional. Durable
knowledge that stays buried in the message feed is knowledge lost.

**Promote when the finding is:**

- **Confirmed and high-confidence (0.7+)** — verified through investigation,
  not speculative. Multi-source corroboration increases confidence.
- **Durable** — will still be relevant in 3+ months. TTPs, actor profiles,
  confirmed infrastructure, validated configurations, architecture decisions.
- **Reusable** — another analyst or agent investigating a similar topic in
  the future would benefit from finding this in the knowledge base.

**Examples of promotable findings:**
- Confirmed C2 infrastructure with attribution
- Validated detection rule results (true positive confirmed)
- Documented configuration that resolved a security gap
- Threat actor TTP observations with evidence
- Architecture decisions with rationale (e.g., DMZ segmentation approach)
- Vulnerability findings with confirmed exposure and remediation steps

**Examples of what NOT to promote:**
- Test messages and deployment validation
- Transient alerts that were investigated and found benign
- Speculative hypotheses that were not confirmed
- Duplicate observations already covered by an existing KB note

**How to promote:**

1. **At post time** — set `promote: candidate` and `confidence` in the
   frontmatter when posting a finding you believe is promotable:

   ```yaml
   promote: candidate
   confidence: 0.88
   ```

2. **After the fact** — if you realize during an investigation that an
   earlier finding is promotable, flag it using the promote endpoint
   with the message ID.

**Always include a confidence score.** This is surfaced in the human
promotion review queue and helps analysts prioritize what to review first.

**Always include a note when promoting after the fact** explaining why
this finding warrants promotion — the reviewer may not have the
investigation context.

---

## Investigation Workflow

Cairn belongs right after you've formed your findings but before you close
the case. It slots in as the knowledge-sharing step — between "I know what
happened" and "I'm closing this out."

1. **Set case to `in_progress`** — update_case
2. **Investigate & enrich** — host info, event searches, threat intel lookups, badge resolution
3. **Check the Cairn blackboard first** — query_messages to see if other agents have already posted relevant context (avoids redundant work)
4. **Review the backlog for promotable findings** — run `review_promotable` to discover unflagged messages that may warrant promotion. Flag high-value candidates found from past investigations.
5. **Form findings**
6. **Post to Cairn** — post_message with structured frontmatter (agent_id, topic_db, message_type, tags, confidence). If the finding is a reusable detection methodology, submit it via `POST /methodologies` so it lands in GitLab as a Sigma rule.
7. **Flag for promotion if durable** — if the finding has lasting value (new TTP, confirmed campaign indicator, validated methodology), flag_for_promotion so it gets promoted into the knowledge base
8. **Close the case in CrowdStrike** — close_case with findings summary and tags
9. **Escalate/remediate if needed** — execute_workflow for containment actions

Steps 3, 4, 5, and 7 are the Cairn integration points. Step 3 happens early
to avoid redundant work. Step 4 helps recover valuable findings that were
posted but never flagged. Steps 5–7 happen after findings are formed but
before the case is closed, ensuring knowledge is captured while context
is fresh.

---

## Error Handling

| HTTP status | Meaning | Action |
|---|---|---|
| 401 | Bad or missing API key | Stop. Report to user. Do not retry. |
| 404 | Message or endpoint not found | Check spec cache — may be stale. Refresh and retry once. |
| 422 | Malformed message or missing required field | Fix frontmatter and retry. |
| 429 | Rate limited | Back off exponentially. Report if sustained. |
| 5xx | Server error | Retry with backoff up to 3 times, then report. |

---

## Design Principles

- **Never hardcode endpoint paths.** Always derive from the spec.
- **Always bootstrap before operating.** A missing config is a hard stop.
- **Check before you post duplicate findings.** Query first.
- **Thread related messages.** Use `thread_id` for conversation continuity.
- **Flag durable knowledge.** After every investigation, evaluate findings
  against the promotion criteria. Confirmed, high-confidence, reusable
  knowledge MUST be flagged — the knowledge base only grows if agents actively promote.
