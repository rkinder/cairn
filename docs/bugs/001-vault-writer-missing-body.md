# BUG: Vault writer uses promotion narrative instead of source message body

## Summary

When a message is promoted to the Obsidian vault, the vault note's `## Summary`
section contains only the short narrative from the promotion candidate record —
not the full body content of the source message(s). This results in vault notes
that lose the majority of the original finding's content.

## Severity

**High** — Promoted vault notes are the curated knowledge base. If they don't
contain the actual findings, the vault is useless as a reference.

## Reproduction

1. Post a message with substantial body content to the blackboard
2. Flag it for promotion (`promote: candidate`)
3. Promote it via the web UI with a short narrative (e.g., "Steps for using Cairn")
4. Check the resulting vault note at `/mnt/data/vault/cairn/<entity>.md`

**Expected:** The `## Summary` section contains the full message body (or at
minimum a meaningful synthesis of it).

**Actual:** The `## Summary` section contains only the short narrative string
from the promotion review UI. The full message body is discarded.

## Example

**Source message body** (1,500+ characters):
```markdown
# Case Finding Reporting Workflow

Standard workflow for investigating and reporting findings from CrowdStrike
cases. All agents should follow this pattern...

## Steps
### 1. Set case to in_progress
### 2. Investigate and enrich
### 3. Check the Cairn blackboard
### 4. Form findings
### 5. Post findings to Cairn
### 6. Flag for promotion if durable
### 7. Close the case
### 8. Escalate or remediate if needed

## Key Principle
The case management system is the system of record...
```

**Resulting vault note `## Summary`:**
```markdown
## Summary

Steps for using Cairn as part of the process.
```

## Root Cause

The promotion pipeline has two content sources:

1. **`narrative`** — short text from the promotion candidate record, entered by
   the reviewer in the web UI during promotion review
2. **`body`** — the full markdown body of the source message(s) in the topic DB

The `promote_candidate()` handler in `cairn/api/routes/promotions.py` (line ~188)
passes `narrative` to `write_note()`:

```python
narrative = (body.narrative or row["narrative"] or "").strip()
```

This resolves to the promotion candidate's `narrative` field — the short
summary the reviewer typed. The source message body is never fetched from
the topic database.

The `write_note()` function in `cairn/vault/writer.py` uses `narrative` directly
as the `## Summary` content:

```python
narrative_body = narrative.strip() if narrative.strip() else "_No summary provided._"
```

## Fix

The `promote_candidate()` handler needs to fetch the source message(s) from
the topic database and include their body content in the vault note. Two
approaches:

### Option A: Include full message body in Summary (recommended)

In `promote_candidate()`, after fetching the candidate row:

1. Iterate `source_message_ids`
2. For each, query the topic DB to get the full message record
3. Pass both the narrative AND the message bodies to `write_note()`
4. `write_note()` uses the narrative as a lead-in, followed by the full
   message body content

The vault note structure becomes:

```markdown
## Summary

<reviewer narrative — short context>

## Source Findings

### Finding from analyst-01 (2026-04-20T13:25:50Z)

<full message body>

### Finding from analyst-02 (2026-04-20T14:10:00Z)

<full message body>

## Evidence
...
```

### Option B: Concatenate bodies into narrative

Simpler but less structured — concatenate all source message bodies and use
that as the narrative. The reviewer's short narrative becomes a prefix.

## Files to Modify

| File | Change |
|---|---|
| `cairn/api/routes/promotions.py` | Fetch source message bodies from topic DB before calling `write_note()` |
| `cairn/vault/writer.py` | Accept and render source message bodies (new parameter or extended narrative) |
| `cairn/api/routes/promotions.py` | Need to determine which topic DB each source message lives in (query `message_index` for `topic_db_id`) |

## Additional Context

- The `## Evidence` section correctly lists the source message IDs and timestamps,
  but only as references — the actual content is not included
- The `_update_existing_note()` path has the same issue — when appending to an
  existing note, it only adds an evidence line, not the new message body
- The promotion candidate's `source_message_ids` field contains the IDs needed
  to fetch the full records
- The `message_index` table in `index.db` maps message IDs to their `topic_db_id`,
  which can be used to determine which topic DB to query

## Related

- Promotion candidate ID: `069e673a-f4ef-78fb-8000-fbe31daf459f`
- Source message ID: `069e6295-f468-79ad-8000-b0031cf00d36`
- Vault note: `cairn/msg_069e6295-f468-79ad-8000-b0031cf00d36.md`
