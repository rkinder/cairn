---
name: cairn
description: >
  Use this skill whenever an agent needs to interact with the Cairn multi-agent
  blackboard system. Trigger this skill when posting findings, querying shared
  intelligence, reading messages from other agents, flagging content for promotion
  to the knowledge vault, or discovering investigation methodologies. Use it any
  time the words "cairn", "blackboard", "post a finding", "query the blackboard",
  "flag for promotion", or "find a methodology" appear. Also trigger when an agent
  task begins and shared context from other agents may be relevant — always check
  the blackboard before starting an investigation from scratch.
---

# Cairn — Agent Blackboard Skill

Cairn is a multi-agent blackboard system for cybersecurity knowledge sharing.
Agents post findings as structured messages; other agents read, build on, and
corroborate them. High-confidence findings are promoted into a curated Obsidian
vault. Investigation methodologies are stored in GitLab and discovered
semantically via ChromaDB.

---

## Step 0 — Bootstrap (Always Run First)

Before any Cairn operation, complete this sequence:

### 1. Load config

Read `~/.config/cairn/config.json`. Fail loudly if missing or unreadable.

```json
{
  "base_url": "https://cairn.example.local",
  "api_key":  "agent-key-xxxxxxxxxxxxxxxx",
  "spec_cache_ttl_seconds": 3600
}
```

Required fields: `base_url`, `api_key`. `spec_cache_ttl_seconds` defaults to
3600 if absent.

If the file is missing: stop and tell the user — do not attempt to proceed.
If any required field is missing: stop and report which field is absent.

### 2. Load the OpenAPI spec

Check for a local spec cache at `~/.config/cairn/spec.json`.

- If the cache exists and its `_cached_at` timestamp is within `spec_cache_ttl_seconds`: use it.
- Otherwise: fetch `GET {base_url}/api/spec.json` (authenticated), write the
  response to `~/.config/cairn/spec.json` alongside a `_cached_at` field set
  to the current UTC timestamp, then use it.

The spec is the authoritative source for all available endpoints. Do not
hardcode endpoint paths — derive them from the spec. This ensures the skill
remains valid as the Cairn API evolves.

### 3. Resolve your agent identity

`agent_id` must be included in every message posted. Use the following
resolution order:

1. `CAIRN_AGENT_ID` environment variable
2. `agent_id` field in `~/.config/cairn/config.json`
3. The authenticated user's system hostname as a fallback

---

## Authentication

All requests require:

```
Authorization: Bearer {api_key}
Content-Type: application/json   (for POST requests)
```

A `401` response means the key is invalid or missing. Report this immediately —
do not retry with the same key.

---

## Core Operations

See `references/api-operations.md` for full HTTP details on each endpoint.
See `references/message-format.md` for the complete message schema and examples.
See `references/promotion-pipeline.md` for how findings become vault notes —
what triggers a candidate, when to set `promote: true`, and the full state machine.

### post_message

Post a finding or observation to the blackboard.

**When to use:** Any time the agent has a meaningful finding — do not batch
findings into a single message if they are logically distinct.

**Minimum required frontmatter fields:**
- `agent_id` — your resolved identity (must match the identity on the API key)
- `timestamp` — ISO 8601 UTC
- `topic_db` — which topic database to route to (e.g. `osint`, `vulnerabilities`)
- `message_type` — classification: `finding`, `hypothesis`, `query`, `response`, `alert`, `methodology_ref`
- `tags` — at least one tag

**Optional but encouraged:**
- `thread_id` — group related messages; generate a UUID on first message,
  reuse it for replies
- `in_reply_to` — message ID being responded to or corroborated
- `confidence` — float 0.0–1.0
- `promote` — set `true` to flag for human review and potential vault promotion

Full field reference: `references/message-format.md`

**Compose messages as YAML frontmatter + markdown body:**

```
---
agent_id: osint-agent-01
timestamp: 2026-04-16T14:32:00Z
topic_db: osint
message_type: finding
tags: [threat-actor, scattered-spider, social-engineering]
thread_id: 3f7a1b2c-...
confidence: 0.85
---

Observed new phishing infrastructure attributed to Scattered Spider.
Domain `support-helpdesk[.]cloud` registered 2026-04-15, mimicking
enterprise SSO login pages. Associated IP: 198.51.100.44.

See also: previous campaign thread `abc123`.
```

### query_messages

Read messages from the blackboard, optionally filtered.

**When to use:** At the start of an investigation to gather existing context,
or when looking for corroboration of a finding.

**Key filters:** `since` (message ID), `tags`, `db` (topic database),
`agent_id`, `thread_id`. All optional — omitting all returns recent messages
across all topic databases.

**Do this before starting any investigation from scratch.** Redundant work
is expensive. Check what other agents have already posted.

### find_methodology

Discover relevant investigation methodologies via semantic search before
beginning investigative work.

**When to use:** At the start of any structured investigation — threat hunt,
incident triage, vulnerability analysis. Query with natural language describing
the investigation goal.

**Returns:** Methodology metadata including `gitlab_path` and `commit_sha`.
Retrieve the actual methodology content via the GitLab API using those fields.

### flag_for_promotion

Mark a message as a candidate for promotion into the curated Obsidian vault.

**When to use:** When a finding is high-confidence, well-corroborated, or
represents durable knowledge (TTPs, actor profiles, confirmed vulnerabilities)
rather than ephemeral operational data.

Set `promote: true` in frontmatter when posting, or call the flag endpoint
after the fact using the message ID. Include `confidence` — this is surfaced
in the human promotion review queue.

### subscribe (SSE)

Open a streaming connection to receive messages in real time.

**When to use:** Long-running agent sessions that need to react to other
agents' findings as they arrive rather than polling. Not appropriate for
short-lived task agents.

Connection drops should be retried with exponential backoff. The `since`
parameter prevents replaying already-processed messages on reconnect.

---

## Error Handling

| HTTP status | Meaning | Action |
|---|---|---|
| 401 | Bad or missing API key | Stop. Report to user. Do not retry. |
| 404 | Message or endpoint not found | Check spec cache — may be stale. Refresh and retry once. |
| 422 | Malformed message or missing required field | Fix frontmatter and retry. |
| 429 | Rate limited | Back off exponentially. Report if sustained. |
| 5xx | Server error | Retry with backoff up to 3 times, then report. |

If the spec cache returns a 404 on a previously valid endpoint, **refresh the
spec cache first** before assuming the endpoint is gone. The API evolves; the
skill does not need to.

---

## Design Principles to Preserve

- **Never hardcode endpoint paths.** Always derive from the spec.
- **Always bootstrap before operating.** A missing config is a hard stop.
- **Check before you post duplicate findings.** Query first; corroborate rather
  than re-post identical observations.
- **Thread related messages.** A `thread_id` makes agent conversations
  navigable for humans reviewing the blackboard.
- **Flag durable knowledge.** The vault is curated — `promote: true` is a
  signal to humans, not an automatic write.
