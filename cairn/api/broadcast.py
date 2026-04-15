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

"""SSE broadcast infrastructure.

MessageBroadcaster holds a set of asyncio Queues, one per connected SSE
subscriber.  When a message is ingested, the ingest pipeline calls
broadcast() to push a summary event to every subscriber.

Subscribers that fall behind (slow consumers) have their queue drained from
the front to prevent unbounded memory growth.  The MAX_QUEUE_SIZE constant
controls how far behind a subscriber may fall before old events are dropped.

Usage:
    # In lifespan:
    broadcaster = MessageBroadcaster()
    app.state.broadcaster = broadcaster

    # After writing a message to the DB:
    await broadcaster.broadcast(event_dict)

    # In the SSE route handler:
    async with broadcaster.subscribe() as queue:
        while True:
            event = await queue.get()
            yield ServerSentEvent(data=json.dumps(event))
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 100  # events per subscriber before oldest are dropped


class MessageBroadcaster:
    """Fan-out broadcaster for SSE subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """Context manager that registers a subscriber queue for the duration.

        Yields the queue the caller should read from.  The queue is
        automatically deregistered when the context exits (e.g. when the
        client disconnects).

        Usage:
            async with broadcaster.subscribe() as queue:
                event = await asyncio.wait_for(queue.get(), timeout=30)
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        async with self._lock:
            self._subscribers.add(queue)
        logger.debug("SSE subscriber connected (%d total)", len(self._subscribers))
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)
            logger.debug("SSE subscriber disconnected (%d remaining)", len(self._subscribers))

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Send event to all connected subscribers.

        If a subscriber's queue is full, the oldest event is dropped to make
        room.  A warning is logged so operators know a consumer is falling
        behind.
        """
        if not self._subscribers:
            return

        async with self._lock:
            targets = list(self._subscribers)

        for queue in targets:
            if queue.full():
                try:
                    queue.get_nowait()
                    logger.warning(
                        "SSE subscriber queue full — dropped oldest event to make room."
                    )
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # race between full check and put; safe to skip

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
