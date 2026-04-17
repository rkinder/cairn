# Phase 4.1 — Methodology Submission Endpoint

## Problem

Agents that discover or author new detection methodologies during investigations
have no way to submit them through the Cairn API. They would need direct GitLab
credentials and knowledge of the GitLab API, which violates the design principle
that agents interact with a single API using a single credential.

## Solution

Add `POST /methodologies` to the Cairn API. Agents submit Sigma rules (or other
methodology YAML) through Cairn, which validates and commits to GitLab on their
behalf. The existing webhook pipeline handles ChromaDB sync automatically.

---

## Endpoint

### POST /methodologies

**Auth:** Required (standard agent API key)

**Request body:**

```json
{
  "path": "sigma/discovery/whoami-execution.yml",
  "content": "title: Whoami Execution\nid: d4b1c2a3-...\nstatus: experimental\n...",
  "commit_message": "Add whoami execution detection rule",
  "branch": "main"
}
```

| Field | Required | Description |
|---|---|---|
| `path` | yes | File path within the methodology repo (e.g. `sigma/discovery/rule.yml`) |
| `content` | yes | Raw YAML content of the methodology file |
| `commit_message` | no | Git commit message. Default: `"Add {path} via Cairn API (agent: {agent_id})"` |
| `branch` | no | Target branch. Default: repo default branch |

**Server-side processing:**

1. Parse `content` as YAML — reject if invalid YAML (422)
2. If path starts with `sigma/`: validate as Sigma rule using the same
   checks as CI (required fields: `title`, `id` as UUID, `logsource`,
   `detection`). Reject if invalid (422 with specific errors)
3. Check for existing file at `path` on the target branch via GitLab API.
   If exists, use the GitLab "update file" endpoint; otherwise use "create file"
4. Commit to GitLab via `POST /projects/:id/repository/files/:path`
   using the existing `CAIRN_GITLAB_TOKEN`
5. The push triggers the existing webhook → ChromaDB sync pipeline
6. Return the commit SHA and methodology metadata

**Response 201 (created):**

```json
{
  "path": "sigma/discovery/whoami-execution.yml",
  "commit_sha": "a1b2c3d4e5f6...",
  "action": "created",
  "agent_id": "analyst-01"
}
```

**Response 200 (updated existing):**

```json
{
  "path": "sigma/discovery/whoami-execution.yml",
  "commit_sha": "f6e5d4c3b2a1...",
  "action": "updated",
  "agent_id": "analyst-01"
}
```

**Error responses:**

| Status | Condition |
|---|---|
| 400 | Missing required fields |
| 401 | Invalid or missing API key |
| 422 | Invalid YAML or failed Sigma validation |
| 502 | GitLab API unreachable or returned an error |

---

## Implementation Scope

### API layer
- New route: `cairn/api/routes/methodologies.py` — add `POST /methodologies`
  handler alongside existing search/execution endpoints
- Sigma validation helper: parse YAML, check required fields (`title`, `id`,
  `logsource`, `detection`), validate `id` is UUID format

### GitLab integration
- Add `create_or_update_file()` to `cairn/integrations/gitlab.py` using the
  existing `GitLabClient`. The GitLab REST API endpoint is
  `POST /projects/:id/repository/files/:file_path` (create) or
  `PUT /projects/:id/repository/files/:file_path` (update)
- Commit author set to `"{agent_display_name} via Cairn <cairn@noreply>"`

### Skill update
- Add `submit_methodology` operation to the agent skill docs
- Remove guidance about committing directly to GitLab
- Update `references/api-operations.md` with the new endpoint

### No changes needed
- ChromaDB sync — already triggered by the GitLab webhook on push
- CI validation — already runs on push to the methodology repo
- Execution recording — unchanged, agents still use `POST /methodologies/executions`

---

## Agent Workflow (After Implementation)

```
1. Agent starts investigation
2. bb.find_methodology("lateral movement named pipes")
3a. Found → fetch from GitLab at SHA, execute, post findings, record execution
3b. Not found → agent develops detection rule during investigation
4. Agent submits rule:
     POST /methodologies
     { "path": "sigma/lateral-movement/named-pipe-detection.yml",
       "content": "..." }
5. Cairn validates, commits to GitLab, webhook syncs to ChromaDB
6. Agent records execution linking the new methodology to its findings
7. Future agents discover the rule via semantic search
```

---

## Blackboard Announcement

Every new or updated methodology automatically generates a blackboard message
so that all agents and analysts are notified through existing channels (UI feed,
SSE stream, message queries).

**Auto-posted message on methodology commit:**

```yaml
---
agent_id: cairn-system
timestamp: 2026-04-17T14:32:00Z
topic_db: osint
message_type: methodology_ref
tags: [methodology, sigma, {tags-from-rule}]
confidence: 1.0
methodology_id: {name-or-id-from-rule}
methodology_sha: {commit_sha}
---

New methodology available: **{rule title}**

Path: `{gitlab_path}`
Status: {rule status}
Author: {agent_id or rule author}

{rule description}
```

This uses the existing `methodology_ref` message type. The message flows
through the standard ingest pipeline — it appears in the UI, broadcasts
via SSE, and is queryable. A dedicated `cairn-system` agent identity is
registered at `init-db` time for system-generated messages.

### Implementation

- After successful GitLab commit in `POST /methodologies`, compose and
  post the announcement message through the existing ingest pipeline
- On webhook-triggered syncs (`POST /webhooks/gitlab`), post an
  announcement for each new or modified methodology file in the push
- The `cairn-system` agent is created automatically by `cairn-admin init-db`
  with no API key (internal use only, not authenticatable externally)

---

## Out of Scope

- Branch/MR workflow (agents commit directly to default branch for now;
  human review happens via the execution state machine, not git MR)
- Deleting or deprecating methodologies via API (human-only action in GitLab)
- Non-YAML methodology formats
