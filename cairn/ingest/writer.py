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

"""Database write logic for the ingest pipeline.

Separates the "what to write" (MessageRecord) from the "how to write it"
(SQL + connection management).  Route handlers call write_message(); the
SQL and ordering details stay here.

Write ordering:
  1. Insert into topic DB messages table  (primary store)
  2. Upsert thread into index.db threads  (if message has a thread_id)
  3. Insert into index.db message_index   (cross-domain query layer)

Step 1 is committed before steps 2–3.  If the index writes fail, the message
is still durably stored in the topic DB.  A future reconciliation job can
repair a stale index by scanning topic DB messages that have no matching
message_index row.
"""

from __future__ import annotations

import logging

from cairn.db.connections import DatabaseManager
from cairn.models.message import MessageRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

_INSERT_MESSAGE = """
    INSERT INTO messages (
        id, agent_id, thread_id, message_type, in_reply_to,
        confidence, tlp_level, promote, tags,
        raw_content, frontmatter, body, timestamp, ingested_at, ext
    ) VALUES (
        :id, :agent_id, :thread_id, :message_type, :in_reply_to,
        :confidence, :tlp_level, :promote, :tags,
        :raw_content, :frontmatter, :body, :timestamp, :ingested_at, :ext
    )
"""

_UPSERT_THREAD = """
    INSERT INTO threads (id, created_at, updated_at, ext)
    VALUES (:thread_id, :now, :now, '{}')
    ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
"""

_INSERT_INDEX = """
    INSERT INTO message_index (
        id, topic_db_id, agent_id, thread_id, message_type,
        tags, confidence, tlp_level, promote, timestamp, ingested_at
    ) VALUES (
        :id, :topic_db_id, :agent_id, :thread_id, :message_type,
        :tags, :confidence, :tlp_level, :promote, :timestamp, :ingested_at
    )
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def write_message(
    record: MessageRecord,
    topic_slug: str,
    db: DatabaseManager,
) -> None:
    """Persist a parsed message to the topic DB and the cross-domain index.

    Args:
        record:     Fully resolved MessageRecord from the ingest parser.
        topic_slug: Slug of the target topic database ('osint', etc.).
        db:         Open DatabaseManager from app.state.

    Raises:
        KeyError:  If topic_slug is not a known active topic database.
        aiosqlite.IntegrityError: If a message with the same id already exists.
    """
    topic_id = db.topic_id(topic_slug)

    # 1. Write to topic DB — this is the primary store.
    async with db.topic(topic_slug) as conn:
        await conn.execute(_INSERT_MESSAGE, record.to_db_row())
    logger.debug("Wrote message %s to topic DB '%s'", record.id, topic_slug)

    # 2–3. Write to index.db — best-effort; log failures without re-raising
    #      so that a transient index error never loses a message.
    try:
        async with db.index() as conn:
            if record.thread_id:
                await conn.execute(
                    _UPSERT_THREAD,
                    {"thread_id": record.thread_id, "now": record.ingested_at.isoformat()},
                )
            await conn.execute(_INSERT_INDEX, record.to_index_row(topic_id))
        logger.debug("Indexed message %s in message_index", record.id)
    except Exception:
        logger.exception(
            "Failed to index message %s — message is stored in topic DB '%s' "
            "but will not appear in cross-domain queries until the index is repaired.",
            record.id,
            topic_slug,
        )
