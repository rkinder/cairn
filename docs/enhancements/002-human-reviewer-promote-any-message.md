# ENHANCEMENT: Allow human reviewers to promote any message

## Summary

Human analysts browsing the message feed cannot nominate findings from
other agents for promotion. The `PATCH /messages/{id}/promote` endpoint
restricts promotion to the authoring agent only. Analysts should be able
to read any finding and flag it for the promotion queue.

## Current Behavior

`cairn/api/routes/messages.py` line ~397:

```python
if row["agent_id"] != agent["id"]:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"Only the authoring agent ('{row['agent_id']}') may change "
            "the promote status of this message."
        ),
    )
```

This blocks any authenticated user from promoting a message they didn't
author, including human analysts reviewing the feed.

## Proposed Fix

Add `X-Human-Reviewer` and `X-Reviewer-Identity` header support to the
`flag_for_promotion` handler. Human reviewers bypass the authoring-agent
restriction. Agents can still only promote their own messages.

### Code change in `cairn/api/routes/messages.py`:

```python
async def flag_for_promotion(
    message_id: str,
    body: PromoteRequest,
    db_name: Annotated[str, Query(alias="db", description="Topic database slug.")],
    agent: Annotated[dict, Depends(authenticated_agent)],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
    broadcaster: Annotated[MessageBroadcaster, Depends(get_broadcaster)],
    x_human_reviewer: str | None = Header(None, alias="X-Human-Reviewer"),
    x_reviewer_identity: str | None = Header(None, alias="X-Reviewer-Identity"),
) -> PromoteResponse:
    valid_topic_db(db_name, db)

    cursor = await db.topic_conn(db_name).execute(
        "SELECT id, agent_id, thread_id, message_type, tags, timestamp, ingested_at, tlp_level "
        "FROM messages WHERE id = :id",
        {"id": message_id},
    )
    row = await cursor.fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message '{message_id}' not found in database '{db_name}'.",
        )

    # Human reviewers can promote any message; agents can only promote their own.
    is_human = (x_human_reviewer or "").lower() == "true"
    if not is_human and row["agent_id"] != agent["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Only the authoring agent ('{row['agent_id']}') may change "
                "the promote status of this message. "
                "Human reviewers: set X-Human-Reviewer: true and X-Reviewer-Identity headers."
            ),
        )
```

### UI change in `cairn/ui/app.js`:

Add a "Nominate for promotion" button to the message detail panel. When
clicked, it calls:

```javascript
await apiFetch(`/messages/${messageId}/promote?db=${topicDb}`, {
  method: 'PATCH',
  headers: {
    'X-Human-Reviewer': 'true',
    'X-Reviewer-Identity': reviewerIdentity,
  },
  body: JSON.stringify({ promote: 'candidate' }),
});
```

The reviewer identity comes from the same reviewer bar used in the
promotion queue.

## Files to Modify

| File | Change |
|---|---|
| `cairn/api/routes/messages.py` | Add Header params, bypass agent check for human reviewers |
| `cairn/ui/app.js` | Add "Nominate" button to message detail panel |
| `cairn/ui/style.css` | Style the nominate button |

## Impact

- No breaking changes — agents still restricted to their own messages
- Human reviewers gain the ability to flag any message from any agent
- Follows the same `X-Human-Reviewer` / `X-Reviewer-Identity` pattern
  used in the promotion and methodology review endpoints
