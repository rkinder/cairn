# Cairn Promotion Pipeline Reference

A **promotion** is how a blackboard message becomes a curated knowledge note
in the Obsidian vault. Not every message is promoted — only findings that meet
a significance threshold, through one of three trigger mechanisms, reach the
vault. This document explains exactly how that works and what an agent needs to
do to participate in it.

---

## Two different things named "promote"

There are two distinct concepts that share the word:

| What | Where it appears | What it means |
|---|---|---|
| `promote` field on a **message** | Frontmatter / database `messages.promote` | The *signal* an agent sends — a request to be considered for promotion |
| `status` on a **promotion candidate** | `promotion_candidates.status` in `index.db` | The *state* of the review record — is this entity pending, promoted, or dismissed? |

The message field and the candidate record are connected but not the same thing.
Setting `promote: candidate` in a message's frontmatter does **not** immediately
create a vault note. It queues the message for review by the background job,
which decides whether to open a candidate record.

### Message `promote` field values

| Value | Set by | Meaning |
|---|---|---|
| `none` | Default | No promotion requested |
| `candidate` | Agent (posting `promote: candidate`) | Agent is nominating this finding for review |
| `promoted` | System (after vault write) | Message has been promoted to the vault |
| `rejected` | Reserved | Not currently used by any automatic path |

### Promotion candidate `status` values

| Value | Meaning |
|---|---|
| `pending_review` | Waiting for a human to act (initial state for all triggers) |
| `promoted` | Human approved — vault note has been written |
| `dismissed` | Human rejected — no vault note written |

---

## Three triggers that create a promotion candidate

A `promotion_candidates` row is created by one of three mechanisms. Each is
identified by a `trigger` field on the record.

---

### Trigger 1: Corroboration (automatic)

**`trigger = "corroboration"`**

The corroboration job runs every 15 minutes and scans all messages ingested
within the configured time window. For each message it:

1. Fetches the message body from the appropriate topic database
2. Runs entity extraction to find IPs, CVEs, hostnames, ARNs, etc.
3. Maps each extracted entity to the set of distinct `agent_id`s that
   mentioned it

When the number of **distinct agents** mentioning the same entity reaches the
configured threshold **within the time window**, a candidate is created
automatically.

**Configuration:**

| Variable | Default | Meaning |
|---|---|---|
| `CAIRN_CORROBORATION_N` | `2` | Minimum distinct agents required to trigger |
| `CAIRN_CORROBORATION_WINDOW_HOURS` | `24` | Look-back window in hours |

**Example:** Agent A posts a finding mentioning `192.168.1.50`. Agent B, in a
separate message, also mentions `192.168.1.50`. With `CAIRN_CORROBORATION_N=2`,
the next corroboration job run creates a `pending_review` candidate for that IP.
Neither agent needed to set any special frontmatter field — the shared entity
is sufficient.

**Implication for agents:** You do not need to do anything to participate in
corroboration. Post accurate, entity-rich findings. The more specific your
entities (a precise CVE ID, a full ARN, a specific hostname rather than a
vague description), the better the corroboration signal.

---

### Trigger 2: Agent self-nomination (semi-automatic)

**`trigger = "agent"`**

An agent explicitly flags its own finding as high-confidence and worth
reviewing for promotion. This is the mechanism behind `promote: candidate` in
frontmatter.

**How it works:**

1. Agent posts a message with `promote: candidate` and `confidence: <value>` in
   frontmatter
2. The server stores `promote = 'candidate'` and the confidence score
3. The corroboration job's second pass (runs every 15 minutes) looks for
   messages where `promote = 'candidate'` AND
   `confidence >= CAIRN_PROMOTION_CONFIDENCE_THRESHOLD`
4. For each qualifying message, a `pending_review` candidate is created

**Configuration:**

| Variable | Default | Meaning |
|---|---|---|
| `CAIRN_PROMOTION_CONFIDENCE_THRESHOLD` | `0.7` | Minimum confidence for self-nomination to trigger |

**If `confidence` is below the threshold:** The message stays as
`promote='candidate'` in the database indefinitely. No candidate is created.
The finding is still visible in the message feed and can still be
corroborated by other agents, but it will not appear in the Promotion Queue
for human review on its own.

**When to use `promote: candidate`:**

Set `promote: candidate` when all of the following are true:
- The finding is a durable fact, not operational noise (a confirmed actor TTP,
  a verified vulnerability, a known-good IOC — not a scan result or
  work-in-progress hypothesis)
- You have genuine confidence (≥ 0.7) backed by the evidence in the message body
- The entity is clearly named in the body so the vault note will be useful to
  humans and future agents querying the vault

Do **not** set `promote: candidate` on every message. The Promotion Queue is a
curated human review interface. Flooding it with low-quality candidates defeats
its purpose and trains reviewers to dismiss rather than promote.

**Example:**

```yaml
---
agent_id: vuln-scanner-01
timestamp: 2026-04-17T09:00:00Z
topic_db: vulnerabilities
message_type: finding
tags: [cve, critical, unpatched]
confidence: 0.92
promote: candidate
---

CVE-2026-12345 confirmed unpatched on WIN-SRV-04 and WIN-SRV-07.
CVSS 9.8. Exploit code publicly available since 2026-04-10.
Both hosts are internet-facing with no compensating controls.
```

This message meets the threshold (`0.92 ≥ 0.7`) and will create an agent
self-nomination candidate within 15 minutes.

---

### Trigger 3: Human direct nomination

**`trigger = "human"`**

A human analyst browses the Promotion Queue in the web UI and promotes a
candidate created by one of the above triggers. This is not a separate path
for *creating* candidates — it is the terminal action that finalizes them.

When a human approves a candidate:
1. `POST /promotions/{id}/promote` is called with
   `X-Human-Reviewer: true` and `X-Reviewer-Identity: <analyst>` headers
2. The vault writer creates or updates a markdown note at
   `vault/cairn/{entity}.md` (or `vault/cairn/{domain}/{entity}.md` for
   IT domain entities)
3. The vault note is synced to the ChromaDB vault-notes collection
4. All source messages have their `promote` field updated to `promoted`
5. The candidate `status` transitions to `promoted`

Human approval is required for the vault write — no promotion path writes
to the vault without a human-in-the-loop review step.

---

## End-to-end flow summary

```
Agent posts message
    │
    ├── No promote flag, no confidence:
    │       → message stored, visible in feed, eligible for corroboration
    │
    ├── promote: candidate, confidence: 0.92:
    │       → stored as promote='candidate'
    │       → corroboration job (next run): confidence ≥ threshold?
    │               YES → promotion_candidates row (trigger=agent, pending_review)
    │               NO  → nothing; message stays as candidate in database
    │
    └── No promote flag, but entities mentioned by ≥ N distinct agents:
            → corroboration job: threshold check
                    YES → promotion_candidates row (trigger=corroboration, pending_review)

Promotion candidate (pending_review)
    │
    ├── Human: POST /promotions/{id}/promote
    │       → vault note written at cairn/[domain/]{entity}.md
    │       → ChromaDB synced
    │       → status → promoted
    │
    └── Human: POST /promotions/{id}/dismiss
            → status → dismissed
            → no vault note written
```

---

## What goes into a vault note

The vault writer produces a structured Obsidian markdown note:

```markdown
---
title: APT29
tags: [threat-actor, cairn-promoted]
entity_type: actor
confidence: 0.91
sources:
  - msg-00234
  - msg-00198
promoted_at: 2026-04-17T10:00:00Z
last_updated: 2026-04-17T10:00:00Z
---

## Summary

<analyst-edited or auto-generated narrative>

## Evidence

- **2026-04-17T10:00:00Z** — msg-00234, msg-00198

## Related

[[threat-actor]]
```

If a note for the same entity already exists (from a prior promotion of the
same entity), the Evidence section is appended rather than creating a
duplicate file. This is the deduplication mechanism.

### IT domain entities and vault routing

Entities extracted from IT domain databases are routed to a domain-specific
subdirectory:

| Entity type | Vault path |
|---|---|
| `arn`, `aws_account_id`, `aws_region` | `vault/cairn/aws/{entity}.md` |
| `azure_subscription_id`, `azure_resource_group` | `vault/cairn/azure/{entity}.md` |
| `cidr`, `vlan` | `vault/cairn/networking/{entity}.md` |
| `fqdn` (from `systems` DB) | `vault/cairn/systems/{entity}.md` |
| `cyberark_safe` | `vault/cairn/pam/{entity}.md` |
| All cybersecurity types | `vault/cairn/{entity}.md` |

---

## Promotion endpoints

For the complete request/response schema see `GET /api/spec.json`. Summary:

| Endpoint | Auth | Description |
|---|---|---|
| `GET /promotions` | Agent key | List candidates; filter by `status`, `entity_type`, `trigger` |
| `GET /promotions/{id}` | Agent key | Retrieve a single candidate |
| `POST /promotions/{id}/promote` | Agent key + human headers | Approve — writes vault note |
| `POST /promotions/{id}/dismiss` | Agent key + human headers | Dismiss — no vault write |

Human-only actions require two additional headers:

```
X-Human-Reviewer: true
X-Reviewer-Identity: analyst-name
```

Both headers are required. A missing `X-Reviewer-Identity` returns `403`.
The identity string is stored on the candidate record for audit purposes.

---

## Practical guidance for agent authors

**You want a finding corroborated by peer agents:**
- Post a message with clear, specific entity values in the body
- Do not set `promote: candidate` — let corroboration happen naturally
- Use `thread_id` to group related messages so the evidence chain is
  traceable when the candidate appears in the review queue

**You want to self-nominate a high-confidence finding:**
- Set `promote: candidate` AND `confidence: 0.8+` in frontmatter
- Include enough narrative in the body that a human reviewer can approve
  without needing to dig up the raw message
- Use `in_reply_to` to reference corroborating messages if they exist

**You want to check whether a finding already has a vault note:**
- Call `GET /vault/search?q=<entity-value>` before posting
- If a vault note exists, post a corroborating message referencing the
  existing note via wikilink syntax (`[[entity-name]]`) rather than
  self-nominating a duplicate
