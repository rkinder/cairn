# Copyright (C) 2026 Ryan Kinder
#
# This file is part of Cairn.
#
# Cairn is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# Cairn is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for
# more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Cairn. If not, see <https://www.gnu.org/licenses/>.

"""POST /messages and GET /messages route handlers."""

from __future__ import annotations

import json
import logging
from typing import Annotated

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from cairn.api.broadcast import MessageBroadcaster
from cairn.api.deps import (
    agent_can_write,
    authenticated_agent,
    get_broadcaster,
    get_db_manager,
    require_admin,
    valid_topic_db,
)
from cairn.db.connections import DatabaseManager
from cairn.db.ids import new_id
from cairn.ingest.parser import ParseError, parse_message
from cairn.ingest.writer import write_message
from cairn.models.message import IncomingMessage, PromoteStatus

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
    deleted_at: str | None = None
    deleted_by: str | None = None


class MessageDetail(MessageSummary):
    """Full message record including body and parsed frontmatter."""
    in_reply_to: str | None
    body: str
    raw_content: str
    frontmatter: dict
    ext: dict


class DeleteMessageResponse(BaseModel):
    id: str
    topic_db: str
    deleted: bool
    hard_deleted: bool = False
    deleted_at: str | None = None
    deleted_by: str | None = None


class BulkDeleteResponse(BaseModel):
    topic_db: str
    tags: list[str]
    deleted_count: int
    deleted_ids: list[str]


class DeleteThreadResponse(BaseModel):
    topic_db: str
    thread_id: str
    deleted_count: int
    deleted_ids: list[str]


class PromoteRequest(BaseModel):
    promote: PromoteStatus = PromoteStatus.CANDIDATE
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class PromoteResponse(BaseModel):
    id: str
    promote: str
    confidence: float | None
    updated_at: str


# ---------------------------------------------------------------------------
# POST /messages
# ---------------------------------------------------------------------------

@router.post(
    "/messages",
    operation_id="post_message",
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
    operation_id="query_messages",
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
    include_deleted: Annotated[bool, Query(description="Include soft-deleted messages (admin only).")] = False,
) -> list[MessageSummary]:
    if db_name:
        valid_topic_db(db_name, db)

    if include_deleted:
        require_admin(_agent)

    where_clauses: list[str] = []
    params: dict = {}

    if not include_deleted:
        where_clauses.append("mi.deleted_at IS NULL")

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
            mi.ingested_at,
            mi.deleted_at,
            mi.deleted_by
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
    operation_id="get_message",
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
    include_deleted: Annotated[bool, Query(description="Return deleted record if present (admin only).")] = False,
) -> MessageDetail:
    valid_topic_db(db_name, db)

    cursor = await db.topic_conn(db_name).execute(
        """
        SELECT
            id, agent_id, thread_id, message_type, in_reply_to,
            confidence, tlp_level, promote, tags,
            raw_content, frontmatter, body, timestamp, ingested_at, deleted_at, deleted_by, ext
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

    if row["deleted_at"] is not None and not include_deleted:
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
        deleted_at=row["deleted_at"],
        deleted_by=row["deleted_by"],
        body=row["body"],
        raw_content=row["raw_content"],
        frontmatter=json.loads(row["frontmatter"]),
        ext=json.loads(row["ext"]),
    )


# ---------------------------------------------------------------------------
# PATCH /messages/{id}/promote
# ---------------------------------------------------------------------------

@router.patch(
    "/messages/{message_id}/promote",
    operation_id="flag_for_promotion",
    response_model=PromoteResponse,
    summary="Flag a message for promotion",
    description=(
        "Updates the promote status of a message.  Only the agent that posted "
        "the message may change its promote status.  Sets promote=candidate by "
        "default; humans use the web UI to advance to promoted or rejected."
    ),
)
async def flag_for_promotion(
    message_id: str,
    body: PromoteRequest,
    db_name: Annotated[str, Query(alias="db", description="Topic database slug.")],
    agent: Annotated[dict, Depends(authenticated_agent)],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
    broadcaster: Annotated[MessageBroadcaster, Depends(get_broadcaster)],
) -> PromoteResponse:
    valid_topic_db(db_name, db)

    # Verify the message exists and belongs to the authenticated agent.
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

    if row["agent_id"] != agent["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Only the authoring agent ('{row['agent_id']}') may change "
                "the promote status of this message."
            ),
        )

    updated_at = datetime.now(tz=timezone.utc).isoformat()
    new_promote = body.promote.value

    # Update topic DB — primary store.
    async with db.topic(db_name) as conn:
        await conn.execute(
            """
            UPDATE messages
               SET promote    = :promote,
                   confidence = COALESCE(:confidence, confidence)
             WHERE id = :id
            """,
            {"promote": new_promote, "confidence": body.confidence, "id": message_id},
        )

    # Update message_index — best-effort.
    try:
        async with db.index() as conn:
            await conn.execute(
                """
                UPDATE message_index
                   SET promote    = :promote,
                       confidence = COALESCE(:confidence, confidence)
                 WHERE id = :id
                """,
                {"promote": new_promote, "confidence": body.confidence, "id": message_id},
            )
    except Exception:
        logger.exception(
            "Failed to update promote status in message_index for message %s", message_id
        )

    # Re-read the final confidence value to return the accurate result.
    cursor = await db.topic_conn(db_name).execute(
        "SELECT confidence FROM messages WHERE id = :id", {"id": message_id}
    )
    final_row = await cursor.fetchone()
    final_confidence = final_row["confidence"] if final_row else body.confidence

    # Broadcast the updated summary so SSE subscribers see the status change.
    await broadcaster.broadcast({
        "id":           message_id,
        "topic_db":     db_name,
        "agent_id":     row["agent_id"],
        "thread_id":    row["thread_id"],
        "message_type": row["message_type"],
        "tags":         json.loads(row["tags"]),
        "confidence":   final_confidence,
        "tlp_level":    row["tlp_level"],
        "promote":      new_promote,
        "timestamp":    row["timestamp"],
        "ingested_at":  row["ingested_at"],
    })

    return PromoteResponse(
        id=message_id,
        promote=new_promote,
        confidence=final_confidence,
        updated_at=updated_at,
    )


@router.delete(
    "/messages/{message_id}",
    operation_id="delete_message",
    response_model=DeleteMessageResponse,
    summary="Delete a message",
    description=(
        "Soft-delete a message by default. "
        "Only message owner may soft-delete. "
        "Admin may hard-delete with ?hard=true."
    ),
)
async def delete_message(
    message_id: str,
    db_name: Annotated[str, Query(alias="db", description="Topic database slug.")],
    agent: Annotated[dict, Depends(authenticated_agent)],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
    hard: Annotated[bool, Query(description="Hard delete the message (admin only).")] = False,
) -> DeleteMessageResponse:
    valid_topic_db(db_name, db)

    cursor = await db.topic_conn(db_name).execute(
        "SELECT id, agent_id, deleted_at, deleted_by FROM messages WHERE id = :id",
        {"id": message_id},
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message '{message_id}' not found in database '{db_name}'.",
        )

    if hard:
        require_admin(agent)

        async with db.topic(db_name) as conn:
            await conn.execute("DELETE FROM messages WHERE id = :id", {"id": message_id})

        async with db.index() as conn:
            await conn.execute("DELETE FROM message_index WHERE id = :id", {"id": message_id})

        return DeleteMessageResponse(
            id=message_id,
            topic_db=db_name,
            deleted=True,
            hard_deleted=True,
            deleted_at=None,
            deleted_by=None,
        )

    if row["agent_id"] != agent["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Only the authoring agent ('{row['agent_id']}') may soft-delete this message."
            ),
        )

    if row["deleted_at"] is not None:
        return DeleteMessageResponse(
            id=message_id,
            topic_db=db_name,
            deleted=True,
            hard_deleted=False,
            deleted_at=row["deleted_at"],
            deleted_by=row["deleted_by"],
        )

    deleted_at = datetime.now(tz=timezone.utc).isoformat()
    deleted_by = agent["id"]

    async with db.topic(db_name) as conn:
        await conn.execute(
            """
            UPDATE messages
               SET deleted_at = :deleted_at,
                   deleted_by = :deleted_by
             WHERE id = :id
            """,
            {"deleted_at": deleted_at, "deleted_by": deleted_by, "id": message_id},
        )

    async with db.index() as conn:
        await conn.execute(
            """
            UPDATE message_index
               SET deleted_at = :deleted_at,
                   deleted_by = :deleted_by
             WHERE id = :id
            """,
            {"deleted_at": deleted_at, "deleted_by": deleted_by, "id": message_id},
        )

    return DeleteMessageResponse(
        id=message_id,
        topic_db=db_name,
        deleted=True,
        hard_deleted=False,
        deleted_at=deleted_at,
        deleted_by=deleted_by,
    )


@router.delete(
    "/messages",
    operation_id="bulk_delete_messages_by_tag",
    response_model=BulkDeleteResponse,
    summary="Bulk delete messages by tags",
    description="Soft-delete messages in a topic DB matching one or more tags (requires confirm=true).",
)
async def bulk_delete_messages_by_tag(
    db_name: Annotated[str, Query(alias="db", description="Topic database slug.")],
    tags: Annotated[str, Query(description="Comma-separated tags to match.")],
    confirm: Annotated[bool, Query(description="Must be true to execute deletion.")],
    agent: Annotated[dict, Depends(authenticated_agent)],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
) -> BulkDeleteResponse:
    valid_topic_db(db_name, db)
    agent_can_write(agent, db_name)

    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bulk delete requires confirm=true.",
        )

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if not tag_list:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one tag must be provided.",
        )

    placeholders = ", ".join(f":tag_{i}" for i in range(len(tag_list)))
    params = {f"tag_{i}": tag for i, tag in enumerate(tag_list)}

    cursor = await db.topic_conn(db_name).execute(
        f"""
        SELECT id
          FROM messages
         WHERE deleted_at IS NULL
           AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value IN ({placeholders}))
        """,
        params,
    )
    rows = await cursor.fetchall()
    ids = [r["id"] for r in rows]

    if not ids:
        return BulkDeleteResponse(topic_db=db_name, tags=tag_list, deleted_count=0, deleted_ids=[])

    deleted_at = datetime.now(tz=timezone.utc).isoformat()
    deleted_by = agent["id"]

    for message_id in ids:
        async with db.topic(db_name) as conn:
            await conn.execute(
                """
                UPDATE messages
                   SET deleted_at = :deleted_at,
                       deleted_by = :deleted_by
                 WHERE id = :id
                """,
                {"deleted_at": deleted_at, "deleted_by": deleted_by, "id": message_id},
            )
        async with db.index() as conn:
            await conn.execute(
                """
                UPDATE message_index
                   SET deleted_at = :deleted_at,
                       deleted_by = :deleted_by
                 WHERE id = :id
                """,
                {"deleted_at": deleted_at, "deleted_by": deleted_by, "id": message_id},
            )

    return BulkDeleteResponse(
        topic_db=db_name,
        tags=tag_list,
        deleted_count=len(ids),
        deleted_ids=ids,
    )


@router.delete(
    "/messages/thread/{thread_id}",
    operation_id="delete_message_thread",
    response_model=DeleteThreadResponse,
    summary="Delete all messages in a thread",
    description="Soft-delete all non-deleted messages in the specified thread and topic database.",
)
async def delete_message_thread(
    thread_id: str,
    db_name: Annotated[str, Query(alias="db", description="Topic database slug.")],
    agent: Annotated[dict, Depends(authenticated_agent)],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
) -> DeleteThreadResponse:
    valid_topic_db(db_name, db)
    agent_can_write(agent, db_name)

    cursor = await db.topic_conn(db_name).execute(
        """
        SELECT id
          FROM messages
         WHERE thread_id = :thread_id
           AND deleted_at IS NULL
        """,
        {"thread_id": thread_id},
    )
    rows = await cursor.fetchall()
    ids = [r["id"] for r in rows]

    if not ids:
        return DeleteThreadResponse(
            topic_db=db_name,
            thread_id=thread_id,
            deleted_count=0,
            deleted_ids=[],
        )

    deleted_at = datetime.now(tz=timezone.utc).isoformat()
    deleted_by = agent["id"]

    for message_id in ids:
        async with db.topic(db_name) as conn:
            await conn.execute(
                """
                UPDATE messages
                   SET deleted_at = :deleted_at,
                       deleted_by = :deleted_by
                 WHERE id = :id
                """,
                {"deleted_at": deleted_at, "deleted_by": deleted_by, "id": message_id},
            )
        async with db.index() as conn:
            await conn.execute(
                """
                UPDATE message_index
                   SET deleted_at = :deleted_at,
                       deleted_by = :deleted_by
                 WHERE id = :id
                """,
                {"deleted_at": deleted_at, "deleted_by": deleted_by, "id": message_id},
            )

    return DeleteThreadResponse(
        topic_db=db_name,
        thread_id=thread_id,
        deleted_count=len(ids),
        deleted_ids=ids,
    )
