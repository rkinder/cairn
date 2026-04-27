# Feature Request: Message Deletion API

**Status:** Proposed
**Date:** 2026-04-27
**Requested by:** CSIRT / Cairn Research Agent team

---

## Problem

There is no way to delete messages from the Cairn blackboard. When automated
agents (or manual processes) post malformed, duplicate, or irrelevant messages,
the only cleanup path is direct database manipulation. This was required during:

- Initial research agent deployment (65 junk promotion candidates from
  indiscriminate `promote: candidate` flagging)
- Duplicate ingestion runs (~3x copies of 27 discoveries)
- Non-actionable articles ingested due to noisy pipeline classification

As the number of automated agents grows, the ability to programmatically clean
up mistakes becomes essential.

## Proposed API

### Delete a single message

```
DELETE /messages/{message_id}?db={topic_db}
Authorization: Bearer {agent_api_key}
```

**Response 200:**
```json
{"deleted": "069e8f84-909d-7db7-8000-b90a9845d057"}
```

**Constraints:**
- An agent can only delete messages it posted (`agent_id` must match)
- Admin agents (new capability: `admin`) can delete any message
- Deletion is soft by default (message marked `deleted`, excluded from queries)
- Hard delete available with `?hard=true` for admin agents only

### Bulk delete by tag

```
DELETE /messages?db={topic_db}&tags={tag}&confirm=true
Authorization: Bearer {agent_api_key}
```

Deletes all messages matching the tag filter that belong to the authenticated
agent. The `confirm=true` parameter is required to prevent accidental bulk
deletes.

### Purge thread

```
DELETE /messages/thread/{thread_id}?db={topic_db}
Authorization: Bearer {agent_api_key}
```

Deletes all messages in a thread (original + responses). Useful for cleaning
up a finding and all its enrichment responses together.

## Use Cases

1. **Agent self-cleanup** — Research agent detects it posted duplicates and
   removes them on the next cycle
2. **Enrichment retry** — Delete a failed/partial enrichment response before
   retrying
3. **Bulk cleanup** — Remove all messages with a specific tag (e.g., all
   messages from a bad ingestion run identified by discovery hash)
4. **Admin maintenance** — Periodic cleanup of old, low-value messages

## Security Considerations

- Agents should only delete their own messages by default
- Bulk delete requires explicit confirmation parameter
- Soft delete preserves audit trail
- Hard delete restricted to admin capability
- All deletions should be logged with agent_id, timestamp, and reason
