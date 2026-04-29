# ENHANCEMENT: Human-friendly titles for promoted KB notes

## Summary

Promoted findings that lack extractable entities get titled `msg:<UUID>.md`,
which is meaningless to human reviewers browsing the KB. The title should
be derived from the message content or editable by the reviewer during
promotion.

## Current Behavior

When the entity extractor finds no real entities (IPs, CVEs, domains, etc.)
in a message body, the promotion pipeline falls back to using the message
ID as the entity name. This produces KB notes titled:

```
msg:069efa67-f613-778d-8000-d956dcb5fcf9
```

These appear in the Quartz KB, the promotion queue, and ChromaDB search
results with no human-readable context.

## Proposed Solutions

### Option A: Auto-generate title from content (recommended)

Derive the title from the message body:
1. Use the first markdown heading (`# Title` or `## Title`) if present
2. Fall back to the first N characters of the body text (e.g., first 80 chars)
3. Fall back to `msg:<UUID>` only as a last resort

This requires changes to the entity extractor or the promotion candidate
creation logic to set a meaningful `entity` value.

### Option B: Editable title during promotion review

Add a "Title" field to the promotion card in the UI, pre-populated with
the auto-generated title. The reviewer can edit it before clicking Promote.
The edited title becomes the KB note filename and the `title` frontmatter
field.

This requires:
- UI change: add title input to the promotion card
- API change: accept `title` in the `POST /promotions/{id}/promote` request
- Writer change: use the provided title for the filename and frontmatter

### Option C: Both

Auto-generate a reasonable default (Option A) and let the reviewer
override it (Option B). Best UX but most implementation work.

## Examples

| Current title | Better title (Option A) |
|---|---|
| `msg:069efa67-...` | `Case Finding Reporting Workflow` |
| `msg:069e6773-...` | `104.234.32.23 VPN Anonymizer Confirmation` |

## Files to Modify

| File | Change |
|---|---|
| `cairn/api/routes/promotions.py` | Accept optional `title` in promote request |
| `cairn/vault/writer.py` | Use provided title for filename and frontmatter |
| `cairn/ui/app.js` | Add editable title field to promotion card |
| `cairn/jobs/corroboration.py` | Extract title from message body when creating candidates |
