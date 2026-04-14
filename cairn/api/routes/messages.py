"""POST /messages and GET /messages route handlers."""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from cairn.api.broadcast import MessageBroadcaster
from cairn.api.deps import (
    agent_can_write,
    authenticated_agent,
    get_broadcaster,
    get_db_manager,
    valid_topic_db,
)
from cairn.db.connections import DatabaseManager
from cairn.db.ids import new_id
from cairn.ingest.parser import ParseError, parse_message
from cairn.ingest.writer import write_message
from cairn.models.message import IncomingMessage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["messages"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class PostMessageResponse(BaseModel):
    id: str
    ingested_at: str
    topic_db: str


class MessageSummary(BaseModel):
    """Envelope-only view returned from cross-domain GET /messages queries.

    Full body and raw_content are omitted.  To retrieve the full record,
    issue GET /messages/{id}?db=<topic_slug>.
    """
    id: str
    topic_db: str
    agent_id: str
    thread_id: str | None
    message_type: str
    tags: list[str]
    confidence: float | None
    tlp_level: str | None
    promote: str
    timestamp: str
    ingested_at: str


class MessageDetail(MessageSummary):
    """Full message record including body and parsed frontmatter."""
    in_reply_to: str | None
    body: str
    raw_content: str
    frontmatter: dict
    ext: dict


# ---------------------------------------------------------------------------
# POST /messages
# ---------------------------------------------------------------------------

@router.post(
    "/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=PostMessageResponse,
    summary="Post a message to the blackboard",
    description=(
        "Accepts a YAML frontmatter + markdown body message and writes it "
        "to the specified topic database.  The `db` query parameter is "
        "required and must match a registered, active topic database slug."
    ),
)
async def post_message(
    payload: IncomingMessage,
    db_name: Annotated[str, Query(alias="db", description="Target topic database slug.")],
    agent: Annotated[dict, Depends(authenticated_agent)],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
    broadcaster: Annotated[MessageBroadcaster, Depends(get_broadcaster)],
) -> PostMessageResponse:
    # Validate topic DB exists and agent is allowed to write to it.
    valid_topic_db(db_name, db)
    agent_can_write(agent, db_name)

    message_id = new_id()

    try:
        record = parse_message(payload.raw_content, message_id)
    except ParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # The agent_id in the frontmatter must match the authenticated identity.
    if record.agent_id != agent["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Frontmatter agent_id '{record.agent_id}' does not match "
                f"authenticated agent '{agent['id']}'."
            ),
        )

    await write_message(record, db_name, db)

    # Fan out to SSE subscribers (fire-and-forget; never blocks the response).
    await broadcaster.broadcast({
        "id":           record.id,
        "topic_db":     db_name,
        "agent_id":     record.agent_id,
        "thread_id":    record.thread_id,
        "message_type": record.message_type.value,
        "tags":         record.tags,
        "confidence":   record.confidence,
        "tlp_level":    record.tlp_level.value if record.tlp_level else None,
        "promote":      record.promote.value,
        "timestamp":    record.timestamp.isoformat(),
        "ingested_at":  record.ingested_at.isoformat(),
    })

    logger.info(
        "Ingested message %s from agent '%s' into '%s'",
        record.id, agent["id"], db_name,
    )

    return PostMessageResponse(
        id=record.id,
        ingested_at=record.ingested_at.isoformat(),
        topic_db=db_name,
    )


# ---------------------------------------------------------------------------
# GET /messages
# ---------------------------------------------------------------------------

@router.get(
    "/messages",
    response_model=list[MessageSummary],
    summary="Query messages across topic databases",
    description=(
        "Returns envelope-only summaries from the cross-domain message_index. "
        "Specify `db` to restrict to a single topic database.  "
        "Body and raw_content are not returned; use GET /messages/{id} for full records."
    ),
)
async def get_messages(
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
    _agent: Annotated[dict, Depends(authenticated_agent)],
    # Filters
    db_name: Annotated[str | None, Query(alias="db")] = None,
    since: Annotated[str | None, Query(description="ISO8601 timestamp; return messages after this.")] = None,
    tags: Annotated[str | None, Query(description="Comma-separated tags; match any.")] = None,
    thread_id: Annotated[str | None, Query()] = None,
    agent_id: Annotated[str | None, Query()] = None,
    message_type: Annotated[str | None, Query()] = None,
    promote: Annotated[str | None, Query()] = None,
    tlp_level: Annotated[str | None, Query()] = None,
    # Pagination
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MessageSummary]:
    if db_name:
        valid_topic_db(db_name, db)

    where_clauses: list[str] = []
    params: dict = {}

    if db_name:
        where_clauses.append(
            "mi.topic_db_id = (SELECT id FROM topic_databases WHERE name = :db_name)"
        )
        params["db_name"] = db_name

    if since:
        where_clauses.append("mi.timestamp > :since")
        params["since"] = since

    if thread_id:
        where_clauses.append("mi.thread_id = :thread_id")
        params["thread_id"] = thread_id

    if agent_id:
        where_clauses.append("mi.agent_id = :agent_id")
        params["agent_id"] = agent_id

    if message_type:
        where_clauses.append("mi.message_type = :message_type")
        params["message_type"] = message_type

    if promote:
        where_clauses.append("mi.promote = :promote")
        params["promote"] = promote

    if tlp_level:
        where_clauses.append("mi.tlp_level = :tlp_level")
        params["tlp_level"] = tlp_level

    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            # Match any of the supplied tags using json_each.
            placeholders = ", ".join(f":tag_{i}" for i in range(len(tag_list)))
            where_clauses.append(
                f"EXISTS (SELECT 1 FROM json_each(mi.tags) WHERE value IN ({placeholders}))"
            )
            for i, tag in enumerate(tag_list):
                params[f"tag_{i}"] = tag

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = f"""
        SELECT
            mi.id,
            td.name   AS topic_db,
            mi.agent_id,
            mi.thread_id,
            mi.message_type,
            mi.tags,
            mi.confidence,
            mi.tlp_level,
            mi.promote,
            mi.timestamp,
            mi.ingested_at
        FROM message_index mi
        JOIN topic_databases td ON td.id = mi.topic_db_id
        {where_sql}
        ORDER BY mi.timestamp DESC
        LIMIT :limit OFFSET :offset
    """
    params["limit"] = limit
    params["offset"] = offset

    cursor = await db.index_conn.execute(sql, params)
    rows = await cursor.fetchall()

    return [
        MessageSummary(
            id=row["id"],
            topic_db=row["topic_db"],
            agent_id=row["agent_id"],
            thread_id=row["thread_id"],
            message_type=row["message_type"],
            tags=json.loads(row["tags"]),
            confidence=row["confidence"],
            tlp_level=row["tlp_level"],
            promote=row["promote"],
            timestamp=row["timestamp"],
            ingested_at=row["ingested_at"],
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# GET /messages/{id}
# ---------------------------------------------------------------------------

@router.get(
    "/messages/{message_id}",
    response_model=MessageDetail,
    summary="Retrieve a full message record",
    description=(
        "Returns the full message record including body and raw_content. "
        "The `db` query parameter is required to locate the record in the "
        "correct topic database."
    ),
)
async def get_message(
    message_id: str,
    db_name: Annotated[str, Query(alias="db", description="Topic database slug.")],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
    _agent: Annotated[dict, Depends(authenticated_agent)],
) -> MessageDetail:
    valid_topic_db(db_name, db)

    cursor = await db.topic_conn(db_name).execute(
        """
        SELECT
            id, agent_id, thread_id, message_type, in_reply_to,
            confidence, tlp_level, promote, tags,
            raw_content, frontmatter, body, timestamp, ingested_at, ext
        FROM messages WHERE id = :id
        """,
        {"id": message_id},
    )
    row = await cursor.fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message '{message_id}' not found in database '{db_name}'.",
        )

    return MessageDetail(
        id=row["id"],
        topic_db=db_name,
        agent_id=row["agent_id"],
        thread_id=row["thread_id"],
        message_type=row["message_type"],
        in_reply_to=row["in_reply_to"],
        tags=json.loads(row["tags"]),
        confidence=row["confidence"],
        tlp_level=row["tlp_level"],
        promote=row["promote"],
        timestamp=row["timestamp"],
        ingested_at=row["ingested_at"],
        body=row["body"],
        raw_content=row["raw_content"],
        frontmatter=json.loads(row["frontmatter"]),
        ext=json.loads(row["ext"]),
    )
