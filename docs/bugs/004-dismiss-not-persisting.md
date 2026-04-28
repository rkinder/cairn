# BUG: Dismissed promotion candidates reappear after page refresh

## Summary

When a promotion candidate is dismissed via the UI with a justification,
it disappears from the queue immediately but reappears after a page
refresh. The dismiss action does not persist.

## Severity

**Medium** — Dismissed items clutter the promotion queue and force
reviewers to re-dismiss the same candidates repeatedly.

## Reproduction

1. Open the Promotion Queue tab in the Cairn UI
2. Expand a pending candidate
3. Click "Dismiss" and provide a justification
4. Observe the candidate disappears from the list
5. Refresh the page
6. The dismissed candidate reappears as `pending_review`

## Probable Cause

The `POST /promotions/{id}/dismiss` endpoint may not be updating the
`status` field in the `promotion_candidates` table, or the UI is
removing the card from the DOM without waiting for a successful API
response. The `promote` field on the source message in `message_index`
may also need to be reset from `candidate` back to `none` on dismiss.

## Investigation Steps

1. Check the API response from the dismiss endpoint — is it returning 200?
2. Query the database directly after dismissing:
   ```bash
   docker exec cairn-api python3 -c "
   import asyncio, aiosqlite
   async def check():
       async with aiosqlite.connect('/data/index.db') as db:
           db.row_factory = aiosqlite.Row
           c = await db.execute('SELECT id, status, narrative FROM promotion_candidates ORDER BY updated_at DESC LIMIT 5')
           for r in await c.fetchall():
               print(f'{r[\"id\"][:12]} status={r[\"status\"]} narrative={r[\"narrative\"][:40]}')
   asyncio.run(check())
   "
   ```
3. Check if the corroboration job is re-creating dismissed candidates
   for the same entity — if the entity still has corroboration signals,
   the job may be inserting a new `pending_review` candidate

## Files to Investigate

| File | What to check |
|---|---|
| `cairn/api/routes/promotions.py` | `dismiss_candidate()` handler — verify DB update |
| `cairn/ui/app.js` | `doDismiss()` — verify it waits for API success |
| `cairn/jobs/corroboration.py` | Check if it skips entities that have been dismissed |
