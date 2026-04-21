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

"""CouchDB client for Obsidian LiveSync vault sync (Phase 4.4).

Writes vault notes to CouchDB in the LiveSync document format so that
Obsidian clients pick them up via the LiveSync plugin without requiring
a local Obsidian instance on the server.

Document format (small files, content < chunk_threshold_bytes):

    {
      "_id":      "cairn/aws/arn_aws_iam_123_role_Admin.md",
      "data":     "---\\ntitle: ...\\n---\\n...",
      "ctime":    1713628320000,
      "mtime":    1713628320000,
      "size":     1234,
      "type":     "plain",
      "children": []
    }

Large files are split into chunk documents (``_id: h:<sha256[:32]>``) with
the parent document holding an empty ``data`` field and the chunk IDs in
``children``.  Cairn-generated notes are expected to stay well under the
250 KB default threshold; chunking is implemented defensively.

All exceptions are caught at the method boundary — callers never see raises.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PutResult:
    """Result of a put_note() call."""
    success: bool
    doc_id: str
    revision: str | None
    error: str | None


class CouchDBVaultClient:
    """Async client for writing LiveSync-format documents to CouchDB."""

    def __init__(
        self,
        *,
        url: str,
        username: str,
        password: str,
        database: str,
        chunk_threshold_bytes: int = 250_000,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = url.rstrip("/")
        self._db = database
        self._threshold = chunk_threshold_bytes
        self._client = http_client or httpx.AsyncClient(
            auth=(username, password),
            timeout=10.0,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        """Return True if CouchDB is reachable and auth succeeds."""
        try:
            resp = await self._client.get(f"{self._base}/{self._db}")
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("CouchDB ping failed: %s", exc)
            return False

    async def put_note(
        self,
        *,
        vault_rel_path: str,
        content: str,
        ctime_ms: int,
        mtime_ms: int,
    ) -> PutResult:
        """Create or update a vault note document in CouchDB.

        Fetches the current ``_rev`` if the document already exists and
        includes it in the PUT to prevent overwriting analyst edits.
        Retries once on 409 conflict.  Returns a PutResult without raising.
        """
        doc_id = vault_rel_path
        content_bytes = content.encode("utf-8")
        size = len(content_bytes)

        try:
            if size >= self._threshold:
                return await self._put_chunked(
                    doc_id=doc_id,
                    content_bytes=content_bytes,
                    size=size,
                    ctime_ms=ctime_ms,
                    mtime_ms=mtime_ms,
                )
            return await self._put_single(
                doc_id=doc_id,
                data=content,
                size=size,
                ctime_ms=ctime_ms,
                mtime_ms=mtime_ms,
            )
        except Exception as exc:
            logger.warning("CouchDB put_note failed for %s: %s", doc_id, exc)
            return PutResult(success=False, doc_id=doc_id, revision=None, error=str(exc))

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _doc_url(self, doc_id: str) -> str:
        """Build the CouchDB document URL, percent-encoding slashes in the ID."""
        encoded = quote(doc_id, safe="")
        return f"{self._base}/{self._db}/{encoded}"

    async def _fetch_rev(self, doc_id: str) -> str | None:
        """Return the current ``_rev`` of a document, or None if it doesn't exist."""
        try:
            resp = await self._client.get(self._doc_url(doc_id))
            if resp.status_code == 200:
                return resp.json().get("_rev")
        except Exception:
            pass
        return None

    async def _put_doc(self, doc_id: str, body: dict) -> httpx.Response:
        return await self._client.put(self._doc_url(doc_id), json=body)

    async def _put_single(
        self,
        *,
        doc_id: str,
        data: str,
        size: int,
        ctime_ms: int,
        mtime_ms: int,
        children: list[str] | None = None,
    ) -> PutResult:
        """PUT a single (non-chunked) document, handling conflict retry."""
        rev = await self._fetch_rev(doc_id)
        body: dict = {
            "_id": doc_id,
            "data": data,
            "ctime": ctime_ms,
            "mtime": mtime_ms,
            "size": size,
            "type": "plain",
            "children": children or [],
        }
        if rev:
            body["_rev"] = rev

        resp = await self._put_doc(doc_id, body)

        if resp.status_code in (200, 201):
            revision = resp.json().get("rev")
            return PutResult(success=True, doc_id=doc_id, revision=revision, error=None)

        if resp.status_code == 409:
            # Conflict — refetch _rev and retry once
            rev = await self._fetch_rev(doc_id)
            if rev:
                body["_rev"] = rev
            resp = await self._put_doc(doc_id, body)
            if resp.status_code in (200, 201):
                revision = resp.json().get("rev")
                return PutResult(success=True, doc_id=doc_id, revision=revision, error=None)
            if resp.status_code == 409:
                logger.warning("CouchDB conflict after retry for %s", doc_id)
                return PutResult(success=False, doc_id=doc_id, revision=None, error="conflict")

        error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        logger.warning("CouchDB PUT failed for %s: %s", doc_id, error)
        return PutResult(success=False, doc_id=doc_id, revision=None, error=error)

    async def _put_chunked(
        self,
        *,
        doc_id: str,
        content_bytes: bytes,
        size: int,
        ctime_ms: int,
        mtime_ms: int,
    ) -> PutResult:
        """Split content into chunks and PUT each before the parent document."""
        chunk_ids: list[str] = []
        offset = 0

        while offset < len(content_bytes):
            chunk = content_bytes[offset : offset + self._threshold]
            chunk_hash = hashlib.sha256(chunk).hexdigest()[:32]
            chunk_id = f"h:{chunk_hash}"
            chunk_data = chunk.decode("utf-8", errors="replace")
            chunk_size = len(chunk)

            chunk_body: dict = {
                "_id": chunk_id,
                "data": chunk_data,
                "ctime": ctime_ms,
                "mtime": mtime_ms,
                "size": chunk_size,
                "type": "plain",
                "children": [],
            }
            chunk_rev = await self._fetch_rev(chunk_id)
            if chunk_rev:
                chunk_body["_rev"] = chunk_rev

            resp = await self._put_doc(chunk_id, chunk_body)
            if resp.status_code not in (200, 201):
                error = f"chunk PUT failed HTTP {resp.status_code}"
                return PutResult(success=False, doc_id=doc_id, revision=None, error=error)

            chunk_ids.append(chunk_id)
            offset += self._threshold

        return await self._put_single(
            doc_id=doc_id,
            data="",
            size=size,
            ctime_ms=ctime_ms,
            mtime_ms=mtime_ms,
            children=chunk_ids,
        )
