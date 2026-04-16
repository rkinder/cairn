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

"""Corroboration detection background job (Phase 4).

Runs every 15 minutes via APScheduler.  Two detection passes:

1. **Corroboration** — Find messages posted by ≥ N distinct agents within the
   configured time window that mention the same extracted entity (IP, CVE,
   actor, etc.).  For each such entity not already in ``promotion_candidates``,
   create a new ``pending_review`` candidate with trigger=corroboration.

2. **Agent self-nomination** — Find messages with ``promote = 'candidate'``
   and ``confidence ≥ CAIRN_PROMOTION_CONFIDENCE_THRESHOLD`` that do not
   already have a corresponding promotion candidate.  Create one with
   trigger=agent.

Usage (in lifespan)::

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from cairn.jobs.corroboration import run_corroboration_job

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_corroboration_job,
        "interval",
        minutes=15,
        args=[app.state.db, get_settings()],
        id="corroboration",
        replace_existing=True,
    )
    scheduler.start()
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from cairn.config import Settings
from cairn.db.connections import DatabaseManager
from cairn.db.ids import new_id
from cairn.nlp.entity_extractor import extract

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job entry point
# ---------------------------------------------------------------------------

async def run_corroboration_job(db: DatabaseManager, settings: Settings) -> None:
    """Main job function — called by APScheduler every 15 minutes."""
    logger.debug("corroboration job: starting")
    try:
        await _detect_corroboration(db, settings)
        await _detect_self_nominations(db, settings)
    except Exception:
        logger.exception("corroboration job: unhandled error")
    logger.debug("corroboration job: done")


# ---------------------------------------------------------------------------
# Pass 1 — corroboration
# ---------------------------------------------------------------------------

async def _detect_corroboration(db: DatabaseManager, settings: Settings) -> None:
    """Find entities referenced by ≥ N distinct agents within the time window."""
    window_start = (
        datetime.now(timezone.utc) - timedelta(hours=settings.corroboration_window_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = db.index_conn

    # Fetch messages in the window that have a body (from topic DBs via index)
    # We only have envelope data in message_index, so we scan all topics for
    # messages in the window and extract entities from their bodies.
    cursor = await conn.execute(
        """
        SELECT id, topic_db_id, agent_id, tags
        FROM message_index
        WHERE ingested_at >= ?
        ORDER BY ingested_at ASC
        """,
        (window_start,),
    )
    index_rows = await cursor.fetchall()
    if not index_rows:
        return

    # For each message in the index, fetch the body from the topic DB
    # and run entity extraction.  Build: entity → set of (agent_id, msg_id).
    entity_sightings: dict[tuple[str, str], list[tuple[str, str]]] = {}
    # key: (entity_type, entity_value), value: [(agent_id, message_id), ...]

    # Group messages by topic DB id so we can open each connection once
    topic_db_id_map: dict[str, str] = {}  # topic_db_id → slug
    td_cursor = await conn.execute("SELECT id, name FROM topic_databases")
    for row in await td_cursor.fetchall():
        topic_db_id_map[row["id"]] = row["name"]

    for row in index_rows:
        slug = topic_db_id_map.get(row["topic_db_id"])
        if not slug or slug not in db.known_topics():
            continue

        try:
            topic_conn = db.topic_conn(slug)
            body_cursor = await topic_conn.execute(
                "SELECT body, raw_content FROM messages WHERE id = ?",
                (row["id"],),
            )
            msg_row = await body_cursor.fetchone()
        except Exception:
            continue

        if not msg_row:
            continue

        body = msg_row["body"] or ""
        tags_raw = row["tags"]
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except Exception:
            tags = []

        entities = extract(body, tags=tags)
        for entity in entities:
            key = (entity.type, entity.value.lower())
            entity_sightings.setdefault(key, []).append((row["agent_id"], row["id"]))

    # Identify entities seen by ≥ N distinct agents
    n_threshold = settings.corroboration_n

    for (entity_type, entity_value_lower), sightings in entity_sightings.items():
        distinct_agents = {agent_id for agent_id, _ in sightings}
        if len(distinct_agents) < n_threshold:
            continue

        # Use first sighting's original casing — pick the message body entity value
        # (approximation: use the lower value as canonical since we lowered for keys)
        canonical_value = entity_value_lower

        # Check if already in promotion_candidates (pending or promoted)
        existing = await _find_existing_candidate(conn, entity_type, canonical_value)
        if existing:
            continue

        source_ids = list({msg_id for _, msg_id in sightings})
        await _create_candidate(
            conn,
            entity=canonical_value,
            entity_type=entity_type,
            trigger="corroboration",
            confidence=None,
            source_message_ids=source_ids,
        )
        logger.info(
            "corroboration: created candidate for %s '%s' (%d agents, %d messages)",
            entity_type,
            canonical_value,
            len(distinct_agents),
            len(source_ids),
        )


# ---------------------------------------------------------------------------
# Pass 2 — agent self-nominations
# ---------------------------------------------------------------------------

async def _detect_self_nominations(db: DatabaseManager, settings: Settings) -> None:
    """Find high-confidence candidate messages not yet in promotion_candidates."""
    threshold = settings.promotion_confidence_threshold
    conn = db.index_conn

    cursor = await conn.execute(
        """
        SELECT id, agent_id, confidence, tags
        FROM message_index
        WHERE promote = 'candidate'
          AND confidence >= ?
        ORDER BY ingested_at ASC
        """,
        (threshold,),
    )
    rows = await cursor.fetchall()

    for row in rows:
        msg_id    = row["id"]
        agent_id  = row["agent_id"]
        confidence = row["confidence"]

        # Derive a best-effort entity from the message_id itself (placeholder
        # value so the analyst can see it in the queue and fill narrative).
        # A more complete implementation would extract from body.
        entity       = f"msg:{msg_id}"
        entity_type  = "actor"  # fallback; analyst can adjust

        # Check if this message is already referenced in a candidate
        existing_cursor = await conn.execute(
            "SELECT id FROM promotion_candidates WHERE source_message_ids LIKE ?",
            (f"%{msg_id}%",),
        )
        if await existing_cursor.fetchone():
            continue

        await _create_candidate(
            conn,
            entity=entity,
            entity_type=entity_type,
            trigger="agent",
            confidence=confidence,
            source_message_ids=[msg_id],
        )
        logger.info(
            "corroboration: self-nomination candidate for message %s (agent %s, conf %.2f)",
            msg_id,
            agent_id,
            confidence,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _find_existing_candidate(
    conn: aiosqlite.Connection,
    entity_type: str,
    entity: str,
) -> bool:
    """Return True if an active promotion candidate already exists for this entity."""
    cursor = await conn.execute(
        """
        SELECT id FROM promotion_candidates
        WHERE entity_type = ?
          AND lower(entity) = lower(?)
          AND status IN ('pending_review', 'promoted')
        LIMIT 1
        """,
        (entity_type, entity),
    )
    return bool(await cursor.fetchone())


async def _create_candidate(
    conn: aiosqlite.Connection,
    *,
    entity: str,
    entity_type: str,
    trigger: str,
    confidence: float | None,
    source_message_ids: list[str],
) -> None:
    """Insert a new promotion_candidates row and commit."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    candidate_id = new_id()

    await conn.execute(
        """
        INSERT INTO promotion_candidates
            (id, entity, entity_type, trigger, status, confidence,
             source_message_ids, narrative, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending_review', ?, ?, '', ?, ?)
        """,
        (
            candidate_id,
            entity,
            entity_type,
            trigger,
            confidence,
            json.dumps(source_message_ids),
            now,
            now,
        ),
    )
    await conn.commit()
