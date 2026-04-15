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

"""Database connection management for Cairn.

DatabaseManager holds one persistent aiosqlite connection per database file.
SQLite WAL mode means multiple readers can proceed concurrently; writes are
serialised per file but never contend across files.

Lifecycle:
    manager = DatabaseManager()
    await manager.open(index_path, topic_paths)
    ...
    await manager.close()

In practice, manager.open() is called inside the FastAPI lifespan handler and
close() is called on shutdown.  The manager is stored on app.state so that
route dependencies can retrieve it.

Usage in route handlers (via dependency injection):
    db: DatabaseManager = Depends(get_db_manager)
    async with db.topic("osint") as conn:
        await conn.execute(...)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Holds open connections to index.db and all active topic databases."""

    def __init__(self) -> None:
        self._index: aiosqlite.Connection | None = None
        # Maps topic DB slug ('osint', 'vulnerabilities') → open connection.
        self._topics: dict[str, aiosqlite.Connection] = {}
        # Maps topic DB slug → UUID in topic_databases table (needed for index writes).
        self._topic_ids: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self, index_path: Path, topic_paths: dict[str, Path]) -> None:
        """Open connections to all databases.

        Args:
            index_path:   Path to index.db.
            topic_paths:  Mapping of slug → path for each topic database.
        """
        self._index = await aiosqlite.connect(index_path)
        self._index.row_factory = aiosqlite.Row
        # Return column names with results.
        await self._index.execute("PRAGMA foreign_keys = ON")

        for slug, path in topic_paths.items():
            conn = await aiosqlite.connect(path)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON")
            self._topics[slug] = conn
            logger.debug("Opened topic database '%s' at %s", slug, path)

        # Cache the UUID for each registered topic DB (needed by the writer
        # when inserting into message_index).
        await self._load_topic_ids()
        logger.info(
            "DatabaseManager opened: index + %d topic DB(s)", len(self._topics)
        )

    async def close(self) -> None:
        """Close all open connections."""
        if self._index:
            await self._index.close()
            self._index = None
        for slug, conn in self._topics.items():
            await conn.close()
            logger.debug("Closed topic database '%s'", slug)
        self._topics.clear()
        self._topic_ids.clear()

    async def _load_topic_ids(self) -> None:
        """Populate _topic_ids from the topic_databases registry in index.db."""
        assert self._index is not None
        cursor = await self._index.execute(
            "SELECT id, name FROM topic_databases WHERE is_active = 1"
        )
        rows = await cursor.fetchall()
        self._topic_ids = {row["name"]: row["id"] for row in rows}

    # ------------------------------------------------------------------
    # Connection accessors
    # ------------------------------------------------------------------

    @property
    def index_conn(self) -> aiosqlite.Connection:
        """Direct access to the index.db connection (use sparingly)."""
        if self._index is None:
            raise RuntimeError("DatabaseManager has not been opened.")
        return self._index

    def topic_conn(self, slug: str) -> aiosqlite.Connection:
        """Direct access to a topic DB connection (use sparingly)."""
        if slug not in self._topics:
            raise KeyError(f"Unknown or inactive topic database: '{slug}'")
        return self._topics[slug]

    def topic_id(self, slug: str) -> str:
        """Return the UUID for a topic DB (used when writing to message_index)."""
        if slug not in self._topic_ids:
            raise KeyError(f"No topic_databases entry found for slug: '{slug}'")
        return self._topic_ids[slug]

    def known_topics(self) -> list[str]:
        """Return slugs of all open topic databases."""
        return list(self._topics.keys())

    # ------------------------------------------------------------------
    # Transaction context managers
    # These are the preferred way to execute writes.
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def index(self) -> AsyncIterator[aiosqlite.Connection]:
        """Context manager for a write transaction against index.db.

        Commits on clean exit, rolls back on exception.

        Usage:
            async with db.index() as conn:
                await conn.execute(SQL, params)
        """
        conn = self.index_conn
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    @asynccontextmanager
    async def topic(self, slug: str) -> AsyncIterator[aiosqlite.Connection]:
        """Context manager for a write transaction against a topic database.

        Commits on clean exit, rolls back on exception.

        Usage:
            async with db.topic("osint") as conn:
                await conn.execute(SQL, params)
        """
        conn = self.topic_conn(slug)
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
