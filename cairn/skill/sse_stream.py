"""Async SSE stream iterator for the Cairn agent skill.

Wraps httpx-sse's aconnect_sse to yield parsed message summary dicts
from GET /stream.

Usage:
    stream = SSEStream(http_client, url, params={"token": key})
    async for event in stream:
        process(event)     # event is a dict matching MessageSummary

The iterator handles reconnection transparently: if the connection drops,
it waits reconnect_delay seconds and retries, advancing the `since`
cursor to the timestamp of the last successfully received event to avoid
re-delivering messages.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx
from httpx_sse import aconnect_sse

from cairn.skill.exceptions import AuthError, ServerError

logger = logging.getLogger(__name__)

_DEFAULT_RECONNECT_DELAY = 5.0  # seconds between reconnect attempts


class SSEStream:
    """Async iterator that yields parsed events from the /stream endpoint.

    Args:
        http_client:      Open httpx.AsyncClient (auth headers already set).
        url:              Full URL of the SSE endpoint.
        params:           Query parameters dict (must include 'token' for browser-style auth).
        reconnect_delay:  Seconds to wait before reconnecting after a dropped connection.
        max_reconnects:   Maximum reconnect attempts (None = unlimited).
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        url: str,
        params: dict[str, str] | None = None,
        reconnect_delay: float = _DEFAULT_RECONNECT_DELAY,
        max_reconnects: int | None = None,
    ) -> None:
        self._http            = http_client
        self._url             = url
        self._params          = dict(params or {})
        self._reconnect_delay = reconnect_delay
        self._max_reconnects  = max_reconnects
        self._last_timestamp: str | None = None

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._generate()

    async def _generate(self) -> AsyncIterator[dict[str, Any]]:
        reconnects = 0
        while True:
            params = dict(self._params)
            if self._last_timestamp:
                params["since"] = self._last_timestamp

            try:
                async with aconnect_sse(
                    self._http, "GET", self._url, params=params
                ) as event_source:
                    reconnects = 0  # Reset on successful connection.
                    logger.debug("SSE stream connected to %s", self._url)
                    async for sse in event_source.aiter_sse():
                        if not sse.data or sse.data.strip() == "keepalive":
                            continue
                        try:
                            event = json.loads(sse.data)
                        except json.JSONDecodeError:
                            logger.warning("Received non-JSON SSE data: %r", sse.data)
                            continue

                        ts = event.get("timestamp")
                        if ts:
                            self._last_timestamp = ts
                        yield event

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401:
                    raise AuthError("SSE stream: invalid or expired API key.") from exc
                if exc.response.status_code >= 500:
                    raise ServerError(exc.response.status_code, str(exc)) from exc
                logger.warning("SSE HTTP error %s — reconnecting", exc.response.status_code)

            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as exc:
                logger.warning("SSE connection lost (%s) — reconnecting", exc)

            except asyncio.CancelledError:
                logger.debug("SSE stream cancelled")
                return

            # Reconnect logic.
            if self._max_reconnects is not None:
                reconnects += 1
                if reconnects > self._max_reconnects:
                    logger.error("SSE max reconnects (%d) reached.", self._max_reconnects)
                    return

            logger.debug("SSE reconnecting in %.1fs …", self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)
