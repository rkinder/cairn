"""GET /stream — Server-Sent Events endpoint.

Agents and human UIs subscribe here to receive new messages in real time
without polling.  Each connected client gets its own asyncio Queue via the
MessageBroadcaster.

Protocol:
  - Standard SSE (text/event-stream).
  - Each event is a JSON object with the same fields as MessageSummary.
  - Keepalive comments (': keepalive') are sent every
    settings.stream_keepalive_seconds to prevent proxy timeouts.
  - The client may pass ?since=<ISO8601> to receive only messages posted
    after that timestamp.  Messages already in the DB before the client
    connects are returned as a backfill before live events begin.

Authentication:
  - Same Bearer token as all other endpoints.
  - The token is passed as a query parameter (?token=<key>) because
    EventSource in browsers does not support custom headers.
    Server-side clients (agents) may use the Authorization header instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from sse_starlette.sse import EventSourceResponse

from cairn.api.broadcast import MessageBroadcaster
from cairn.api.deps import get_broadcaster, get_db_manager, stream_authenticated_agent
from cairn.db.connections import DatabaseManager
from cairn.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["stream"])


# ---------------------------------------------------------------------------
# Backfill helper
# ---------------------------------------------------------------------------

async def _backfill(
    db: DatabaseManager,
    since: str | None,
    db_name: str | None,
) -> list[dict]:
    """Return messages from message_index posted after `since`.

    Used to fill in the gap between the client's last known timestamp and
    the moment it connected to the SSE stream.
    """
    where_clauses = []
    params: dict = {}

    if since:
        where_clauses.append("mi.timestamp > :since")
        params["since"] = since

    if db_name:
        where_clauses.append(
            "mi.topic_db_id = (SELECT id FROM topic_databases WHERE name = :db_name)"
        )
        params["db_name"] = db_name

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = f"""
        SELECT
            mi.id, td.name AS topic_db, mi.agent_id, mi.thread_id,
            mi.message_type, mi.tags, mi.confidence, mi.tlp_level,
            mi.promote, mi.timestamp, mi.ingested_at
        FROM message_index mi
        JOIN topic_databases td ON td.id = mi.topic_db_id
        {where_sql}
        ORDER BY mi.timestamp ASC
        LIMIT 500
    """
    params_with_limit = {**params}
    cursor = await db.index_conn.execute(sql, params_with_limit)
    rows = await cursor.fetchall()
    return [
        {
            "id":           row["id"],
            "topic_db":     row["topic_db"],
            "agent_id":     row["agent_id"],
            "thread_id":    row["thread_id"],
            "message_type": row["message_type"],
            "tags":         json.loads(row["tags"]),
            "confidence":   row["confidence"],
            "tlp_level":    row["tlp_level"],
            "promote":      row["promote"],
            "timestamp":    row["timestamp"],
            "ingested_at":  row["ingested_at"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# SSE event generator
# ---------------------------------------------------------------------------

async def _event_generator(
    request: Request,
    db: DatabaseManager,
    broadcaster: MessageBroadcaster,
    since: str | None,
    db_name: str | None,
) -> AsyncIterator[dict]:
    settings = get_settings()
    keepalive_interval = settings.stream_keepalive_seconds

    # Send backfill messages before subscribing so no events are missed.
    for event in await _backfill(db, since, db_name):
        yield {"data": json.dumps(event)}

    async with broadcaster.subscribe() as queue:
        logger.debug("Client connected to SSE stream")
        while True:
            if await request.is_disconnected():
                logger.debug("SSE client disconnected")
                break

            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_interval)
                # If a db_name filter is active, skip events from other DBs.
                if db_name and event.get("topic_db") != db_name:
                    continue
                yield {"data": json.dumps(event)}
            except asyncio.TimeoutError:
                # Send a keepalive comment to prevent proxy/LB timeouts.
                yield {"comment": "keepalive"}


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get(
    "/stream",
    summary="Subscribe to the live message stream",
    description=(
        "SSE endpoint.  Each event is a JSON MessageSummary object.  "
        "Pass `?since=<ISO8601>` to receive a backfill of recent messages "
        "before live events begin.  Pass `?db=<slug>` to filter by topic database."
    ),
    response_class=EventSourceResponse,
)
async def stream_messages(
    request: Request,
    _agent: Annotated[dict, Depends(stream_authenticated_agent)],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
    broadcaster: Annotated[MessageBroadcaster, Depends(get_broadcaster)],
    since: Annotated[str | None, Query(description="ISO8601 timestamp backfill cursor.")] = None,
    db_name: Annotated[str | None, Query(alias="db")] = None,
) -> EventSourceResponse:
    return EventSourceResponse(
        _event_generator(request, db, broadcaster, since, db_name)
    )
