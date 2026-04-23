# BUG/ENHANCEMENT: Promotion queue cards don't show source message content

## Summary

The Promotion Queue UI shows promotion candidates as expandable cards, but
the expanded view does not display the source message bodies. The reviewer
sees only the entity name, truncated source message IDs, and an empty
narrative textarea — not enough context to make an informed promotion
decision or write a meaningful narrative.

## Severity

**High** — The promotion workflow is the primary path for curating the
Obsidian vault. Without source message content visible, reviewers cannot
evaluate findings or write narratives without manually searching for
messages in a separate tab.

## Problems

### Problem 1: Source message content not displayed

The expanded promotion card does not fetch or display the source message
bodies. The reviewer sees truncated message IDs but cannot read the actual
findings without manually searching in the Messages tab.

### Problem 2: Promotion candidates lack topic_db reference

The `promotion_candidates` table does not store which topic database the
source messages live in. To fetch a message by ID, the API requires a `db`
parameter. Without `topic_db` on the candidate record, the UI must either:
- Try every known topic DB until it finds the message (current local workaround — O(N) per message per DB)
- Or a new cross-DB lookup endpoint must be added

Storing `topic_db` on the candidate record at creation time (in the
corroboration job and agent self-nomination path) eliminates this problem
and makes the UI fetch a single direct call per source message.

## Current Behavior

When a reviewer clicks a promotion candidate card, the expanded view shows:
- Entity name (title)
- Entity type badge
- Trigger type (corroboration/agent)
- Created date
- Confidence percentage (if set)
- Source message ID chips (truncated to 8 characters)
- Empty narrative textarea
- Promote / Dismiss buttons

**Missing:** The actual content of the source messages that triggered the
promotion candidate.

## Expected Behavior

The expanded card should fetch and display the source message bodies inline
so the reviewer can:
1. Read the original findings
2. Understand why this entity was flagged
3. Write an informed narrative based on the evidence
4. Promote or dismiss without leaving the Promotion Queue tab

## Proposed Fix

### Fix 1: Add topic_db to promotion_candidates (schema + backend)

1. Add `topic_db TEXT` column to `promotion_candidates` table in `index.sql`
2. Create migration `005_add_topic_db_to_promotion_candidates.sql`
3. Update `cairn/jobs/corroboration.py` — store `topic_db` when creating candidates
4. Update `cairn/api/routes/messages.py` — store `topic_db` on agent self-nomination
5. Update `CandidateResponse` model to include `topic_db`
6. Update `GET /promotions` to return `topic_db`

### Fix 2: Display source message content in the UI

Once `topic_db` is available on the candidate:

1. When a promotion card is expanded, fetch each source message via
   `GET /messages/{id}?db=<topic_db>` (single direct call, no DB scanning)
2. Render message bodies as markdown inline, between the metadata and the
   narrative textarea
3. Lazy-load on first expand to avoid unnecessary API calls

### Local workaround (applied)

The current local fix iterates all known topic DBs per source message ID
until it finds a match. This works but is O(N×M) where N is source messages
and M is topic databases. The proper fix (adding `topic_db` to the candidate
record) makes it O(N).

### UI layout for expanded card:

```
┌─────────────────────────────────────────────────┐
│ 104.234.32.23  [ipv4]  corroboration  candidate │  ← header (click to expand)
├─────────────────────────────────────────────────┤
│ 2026-04-20 13:12  conf 95%                      │  ← metadata
│                                                 │
│ ── Source Findings ──────────────────────────── │
│                                                 │
│ analyst-01 (2026-04-20 14:28)                   │
│ ## E9E-206 Resolution — Rex Richards Using      │
│ ExpressVPN (Confirmed False Positive)           │
│ IP enrichment confirms both primary...          │
│                                                 │
│ analyst-02 (2026-04-20 15:49)                   │
│ ## REVISED: E9E-204 (Zachary Spurgeon)...       │
│                                                 │
│ ── Narrative ───────────────────────────────── │
│ [editable textarea, pre-populated with          │
│  auto-generated summary if available]           │
│                                                 │
│ [Promote to vault]  [Dismiss]                   │
└─────────────────────────────────────────────────┘
```

## Files to Modify

| File | Change |
|---|---|
| `cairn/ui/app.js` | Fetch source messages on card expand, render bodies as markdown |
| `cairn/ui/style.css` | Styles for source findings section within promo cards |
| `cairn/db/schema/index.sql` | Add `topic_db` column to `promotion_candidates` (Option A) |
| `cairn/jobs/corroboration.py` | Store `topic_db` when creating promotion candidates |
| `cairn/api/routes/messages.py` | (If Option C) Add cross-DB message lookup |

## Workaround

Until this is fixed, reviewers must:
1. Note the source message IDs from the promotion card
2. Switch to the Messages tab
3. Search for those messages
4. Read the content
5. Switch back to the Promotion Queue tab
6. Write the narrative from memory
7. Click Promote

## Related

- Bug #001 (vault writer missing body) — same root issue of source message
  content not flowing through the promotion pipeline
- Phase 4.3 (promotion review endpoint) — the API-side review returns full
  message bodies; the UI should match
