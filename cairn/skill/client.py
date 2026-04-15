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

"""BlackboardClient — the agent skill for the Cairn blackboard.

Agents import this class and use it as their sole entry point to the
blackboard.  All endpoint paths are resolved from the cached OpenAPI spec,
so the client self-updates as the server evolves without code changes.

Usage:
    from cairn.skill import BlackboardClient

    async with BlackboardClient(
        base_url="http://localhost:8000",
        api_key="cairn_...",
    ) as bb:
        msg_id = await bb.post_message(
            db="osint",
            agent_id="my-agent-01",
            message_type="finding",
            body="Observed suspicious named pipe on HOST-DELTA.",
            tags=["apt29", "lateral-movement"],
            confidence=0.87,
        )

        results = await bb.query_messages(tags=["apt29"], limit=10)

        await bb.flag_for_promotion(msg_id, db="osint", confidence=0.91)

        async for event in bb.subscribe(db="osint"):
            print(event)
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from cairn.skill.composer import compose_message
from cairn.skill.exceptions import (
    AuthError,
    ForbiddenError,
    NotFoundError,
    ServerError,
    SkillError,
    ValidationError,
)
from cairn.skill.spec_cache import SpecCache
from cairn.skill.sse_stream import SSEStream

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return-value dataclasses
# (plain dataclasses — no Pydantic dependency for agents using the skill)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class PostMessageResult:
    id:          str
    ingested_at: str
    topic_db:    str


@dataclasses.dataclass
class MessageSummary:
    id:           str
    topic_db:     str
    agent_id:     str
    thread_id:    str | None
    message_type: str
    tags:         list[str]
    confidence:   float | None
    tlp_level:    str | None
    promote:      str
    timestamp:    str
    ingested_at:  str


@dataclasses.dataclass
class MessageDetail(MessageSummary):
    in_reply_to: str | None
    body:        str
    raw_content: str
    frontmatter: dict
    ext:         dict


@dataclasses.dataclass
class PromoteResult:
    id:         str
    promote:    str
    confidence: float | None
    updated_at: str


# ---------------------------------------------------------------------------
# BlackboardClient
# ---------------------------------------------------------------------------

class BlackboardClient:
    """Async context manager providing the full blackboard skill interface.

    Args:
        base_url:         Base URL of the Cairn server (no trailing slash).
        api_key:          Bearer API key for this agent.
        spec_cache_path:  Override the default spec cache file location.
        spec_ttl_seconds: How long a cached spec is considered fresh (default 1 hour).
        timeout:          HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        spec_cache_path: Path | str | None = None,
        spec_ttl_seconds: int = 3600,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key  = api_key
        self._timeout  = timeout

        cache_path = (
            Path(spec_cache_path)
            if spec_cache_path
            else SpecCache.default_cache_path(self._base_url)
        )
        self._http: httpx.AsyncClient | None = None
        self._spec  = SpecCache(
            base_url=self._base_url,
            cache_path=cache_path,
            ttl_seconds=spec_ttl_seconds,
        )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "BlackboardClient":
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
        )
        self._spec._http = self._http
        await self.refresh_spec()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # Spec management
    # ------------------------------------------------------------------

    async def refresh_spec(self, *, force: bool = False) -> bool:
        """Fetch and cache /api/spec.json if stale or forced.

        Returns True if the spec was updated, False if the cache was still valid.
        Raises SpecOutdatedError if the server version exceeds the skill version.
        """
        old_hash = self._spec.spec_hash
        await self._spec.get_spec(force=force)
        return self._spec.spec_hash != old_hash

    # ------------------------------------------------------------------
    # Post
    # ------------------------------------------------------------------

    async def post_message(
        self,
        *,
        db: str,
        agent_id: str,
        message_type: str,
        body: str,
        timestamp: datetime | str | None = None,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        tags: list[str] | None = None,
        confidence: float | None = None,
        tlp_level: str | None = None,
        promote: str = "none",
        **extra_frontmatter: Any,
    ) -> PostMessageResult:
        """Compose and POST a YAML+markdown message to the blackboard.

        Returns a PostMessageResult with the server-assigned message ID.
        """
        raw_content = compose_message(
            agent_id=agent_id,
            message_type=message_type,
            body=body,
            timestamp=timestamp,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
            tags=tags,
            confidence=confidence,
            tlp_level=tlp_level,
            promote=promote,
            **extra_frontmatter,
        )

        _, url = self._spec.resolve_url("post_message")
        response = await self._request("POST", url, json={"raw_content": raw_content}, params={"db": db})
        data = response.json()
        return PostMessageResult(
            id=data["id"],
            ingested_at=data["ingested_at"],
            topic_db=data["topic_db"],
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def query_messages(
        self,
        *,
        db: str | None = None,
        since: datetime | str | None = None,
        tags: list[str] | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
        message_type: str | None = None,
        promote: str | None = None,
        tlp_level: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MessageSummary]:
        """Query messages across topic databases via the message_index.

        Returns a list of MessageSummary objects (envelope only, no body).
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if db:           params["db"]           = db
        if since:        params["since"]        = since.isoformat() if isinstance(since, datetime) else since
        if tags:         params["tags"]         = ",".join(tags)
        if thread_id:    params["thread_id"]    = thread_id
        if agent_id:     params["agent_id"]     = agent_id
        if message_type: params["message_type"] = message_type
        if promote:      params["promote"]      = promote
        if tlp_level:    params["tlp_level"]    = tlp_level

        _, url = self._spec.resolve_url("query_messages")
        response = await self._request("GET", url, params=params)
        return [_parse_summary(item) for item in response.json()]

    async def get_message(self, message_id: str, *, db: str) -> MessageDetail:
        """Retrieve the full record for a single message including body."""
        _, url_template = self._spec.resolve_url("get_message")
        url = url_template.replace("{message_id}", message_id)
        response = await self._request("GET", url, params={"db": db})
        data = response.json()
        return MessageDetail(
            id=data["id"],
            topic_db=data["topic_db"],
            agent_id=data["agent_id"],
            thread_id=data.get("thread_id"),
            message_type=data["message_type"],
            tags=data.get("tags", []),
            confidence=data.get("confidence"),
            tlp_level=data.get("tlp_level"),
            promote=data["promote"],
            timestamp=data["timestamp"],
            ingested_at=data["ingested_at"],
            in_reply_to=data.get("in_reply_to"),
            body=data.get("body", ""),
            raw_content=data.get("raw_content", ""),
            frontmatter=data.get("frontmatter", {}),
            ext=data.get("ext", {}),
        )

    # ------------------------------------------------------------------
    # SSE subscription
    # ------------------------------------------------------------------

    def subscribe(
        self,
        *,
        since: datetime | str | None = None,
        db: str | None = None,
        reconnect_delay: float = 5.0,
        max_reconnects: int | None = None,
    ) -> SSEStream:
        """Return an async iterator over live SSE events.

        The iterator reconnects automatically on dropped connections.

        Usage::

            async for event in bb.subscribe(db="osint"):
                print(event["message_type"], event["agent_id"])
        """
        _, url = self._spec.resolve_url("subscribe_stream")
        params: dict[str, str] = {"token": self._api_key}
        if since:
            params["since"] = since.isoformat() if isinstance(since, datetime) else since
        if db:
            params["db"] = db

        assert self._http is not None, "Client not opened — use async with BlackboardClient(...)"
        return SSEStream(
            http_client=self._http,
            url=url,
            params=params,
            reconnect_delay=reconnect_delay,
            max_reconnects=max_reconnects,
        )

    # ------------------------------------------------------------------
    # Promotion
    # ------------------------------------------------------------------

    async def flag_for_promotion(
        self,
        message_id: str,
        *,
        db: str,
        confidence: float | None = None,
    ) -> PromoteResult:
        """Set promote=candidate on a message (agent self-nomination).

        Only the authoring agent may flag their own messages.
        Returns a PromoteResult with the updated status.
        """
        _, url_template = self._spec.resolve_url("flag_for_promotion")
        url = url_template.replace("{message_id}", message_id)

        body: dict[str, Any] = {"promote": "candidate"}
        if confidence is not None:
            body["confidence"] = confidence

        response = await self._request("PATCH", url, json=body, params={"db": db})
        data = response.json()
        return PromoteResult(
            id=data["id"],
            promote=data["promote"],
            confidence=data.get("confidence"),
            updated_at=data["updated_at"],
        )

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """Execute an HTTP request and raise typed skill exceptions on error."""
        assert self._http is not None, "Client not opened — use async with BlackboardClient(...)"
        response = await self._http.request(method, url, params=params, json=json)
        _raise_for_status(response)
        return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_summary(data: dict[str, Any]) -> MessageSummary:
    return MessageSummary(
        id=data["id"],
        topic_db=data["topic_db"],
        agent_id=data["agent_id"],
        thread_id=data.get("thread_id"),
        message_type=data["message_type"],
        tags=data.get("tags", []),
        confidence=data.get("confidence"),
        tlp_level=data.get("tlp_level"),
        promote=data["promote"],
        timestamp=data["timestamp"],
        ingested_at=data["ingested_at"],
    )


def _raise_for_status(response: httpx.Response) -> None:
    """Convert HTTP error responses to typed SkillErrors."""
    if response.is_success:
        return
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text

    code = response.status_code
    if code == 401:
        raise AuthError(f"Authentication failed: {detail}")
    if code == 403:
        raise ForbiddenError(f"Forbidden: {detail}")
    if code == 404:
        raise NotFoundError(f"Not found: {detail}")
    if code == 422:
        raise ValidationError(f"Validation error: {detail}")
    if code >= 500:
        raise ServerError(code, detail)
    raise SkillError(f"HTTP {code}: {detail}")
