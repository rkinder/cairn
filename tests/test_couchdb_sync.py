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

"""Unit tests for CouchDBVaultClient (Phase 4.4).

All tests use httpx.MockTransport — no real CouchDB required.
"""

from __future__ import annotations

import json
from collections import deque

import httpx
import pytest

from cairn.vault.couchdb_sync import CouchDBVaultClient, PutResult


# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------

class MockTransport(httpx.AsyncBaseTransport):
    """Queue-based async transport: return responses in FIFO order.

    Captures every request so tests can assert on method, URL, and body.
    """

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._queue: deque[httpx.Response] = deque(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._queue:
            return self._queue.popleft()
        return httpx.Response(500, json={"error": "no more mock responses"})


def _make_client(responses: list[httpx.Response]) -> tuple[CouchDBVaultClient, MockTransport]:
    transport = MockTransport(responses)
    http = httpx.AsyncClient(
        auth=("cairn", "secret"),
        transport=transport,
    )
    client = CouchDBVaultClient(
        url="http://couchdb:5984",
        username="cairn",
        password="secret",
        database="obsidian-livesync",
        http_client=http,
    )
    return client, transport


CTIME = 1713628320000
MTIME = 1713628320001
DOC_ID = "cairn/APT29.md"
CONTENT = "---\ntitle: APT29\n---\nSample note."


# ---------------------------------------------------------------------------
# Task 1.1 — client skeleton / ping
# ---------------------------------------------------------------------------

class TestPing:
    async def test_ping_returns_true_on_200(self):
        client, _ = _make_client([httpx.Response(200, json={"db_name": "obsidian-livesync"})])
        assert await client.ping() is True

    async def test_ping_returns_false_on_401(self):
        client, _ = _make_client([httpx.Response(401, json={"error": "unauthorized"})])
        assert await client.ping() is False

    async def test_ping_returns_false_on_500(self):
        client, _ = _make_client([httpx.Response(500, json={"error": "internal"})])
        assert await client.ping() is False

    async def test_ping_returns_false_on_connection_error(self):
        transport = MockTransport([])

        async def _raise(request):
            raise httpx.ConnectError("refused")
        transport.handle_async_request = _raise  # type: ignore[method-assign]

        http = httpx.AsyncClient(auth=("u", "p"), transport=transport)
        client = CouchDBVaultClient(
            url="http://localhost:9999",
            username="u",
            password="p",
            database="db",
            http_client=http,
        )
        assert await client.ping() is False

    async def test_close_closes_client(self):
        client, _ = _make_client([])
        await client.close()
        # Subsequent call to the underlying client should raise (already closed)
        with pytest.raises(RuntimeError):
            await client._client.get("http://anything")


# ---------------------------------------------------------------------------
# Task 1.2 — put_note() create path (new document, no existing _rev)
# ---------------------------------------------------------------------------

class TestPutNoteCreate:
    async def test_create_body_contains_required_fields(self):
        """PUT body SHALL include data, ctime, mtime, size, type, children."""
        client, transport = _make_client([
            httpx.Response(404, json={"error": "not_found"}),   # GET pre-fetch
            httpx.Response(201, json={"ok": True, "id": DOC_ID, "rev": "1-abc"}),  # PUT
        ])
        result = await client.put_note(
            vault_rel_path=DOC_ID,
            content=CONTENT,
            ctime_ms=CTIME,
            mtime_ms=MTIME,
        )
        assert result.success is True

        # Inspect the PUT request body
        put_req = transport.requests[1]
        body = json.loads(put_req.content)
        assert body["data"] == CONTENT
        assert body["ctime"] == CTIME
        assert body["mtime"] == MTIME
        assert body["size"] == len(CONTENT.encode("utf-8"))
        assert body["type"] == "plain"
        assert body["children"] == []

    async def test_create_sets_id_verbatim(self):
        """_id in the PUT body SHALL equal vault_rel_path unchanged."""
        client, transport = _make_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(201, json={"ok": True, "id": DOC_ID, "rev": "1-abc"}),
        ])
        await client.put_note(vault_rel_path=DOC_ID, content=CONTENT, ctime_ms=CTIME, mtime_ms=MTIME)
        put_body = json.loads(transport.requests[1].content)
        assert put_body["_id"] == DOC_ID

    async def test_create_no_rev_in_body_when_not_found(self):
        """New document PUT SHALL NOT include _rev."""
        client, transport = _make_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(201, json={"ok": True, "id": DOC_ID, "rev": "1-abc"}),
        ])
        await client.put_note(vault_rel_path=DOC_ID, content=CONTENT, ctime_ms=CTIME, mtime_ms=MTIME)
        put_body = json.loads(transport.requests[1].content)
        assert "_rev" not in put_body

    async def test_create_success_returns_revision(self):
        client, _ = _make_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(201, json={"ok": True, "id": DOC_ID, "rev": "1-abc123"}),
        ])
        result = await client.put_note(vault_rel_path=DOC_ID, content=CONTENT, ctime_ms=CTIME, mtime_ms=MTIME)
        assert result.success is True
        assert result.revision == "1-abc123"
        assert result.error is None

    async def test_put_5xx_returns_failure(self):
        client, _ = _make_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(503, text="Service Unavailable"),
        ])
        result = await client.put_note(vault_rel_path=DOC_ID, content=CONTENT, ctime_ms=CTIME, mtime_ms=MTIME)
        assert result.success is False
        assert "503" in result.error

    async def test_connection_error_returns_failure(self):
        transport = MockTransport([])
        call_count = 0

        async def _raise(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(404, json={"error": "not_found"})
            raise httpx.ConnectError("connection refused")

        transport.handle_async_request = _raise  # type: ignore[method-assign]
        http = httpx.AsyncClient(auth=("u", "p"), transport=transport)
        client = CouchDBVaultClient(
            url="http://localhost:9999", username="u", password="p",
            database="db", http_client=http,
        )
        result = await client.put_note(vault_rel_path="note.md", content="x", ctime_ms=0, mtime_ms=0)
        assert result.success is False
        assert result.error is not None


# ---------------------------------------------------------------------------
# Task 1.3 — put_note() update and conflict path
# ---------------------------------------------------------------------------

class TestPutNoteUpdate:
    async def test_update_includes_fetched_rev(self):
        """PUT body SHALL include _rev fetched from existing document."""
        client, transport = _make_client([
            httpx.Response(200, json={"_id": DOC_ID, "_rev": "1-existing"}),  # GET
            httpx.Response(201, json={"ok": True, "id": DOC_ID, "rev": "2-new"}),  # PUT
        ])
        result = await client.put_note(vault_rel_path=DOC_ID, content=CONTENT, ctime_ms=CTIME, mtime_ms=MTIME)
        assert result.success is True
        put_body = json.loads(transport.requests[1].content)
        assert put_body["_rev"] == "1-existing"

    async def test_conflict_refetches_rev_and_retries(self):
        """On 409, client SHALL refetch _rev and retry once."""
        client, transport = _make_client([
            httpx.Response(200, json={"_id": DOC_ID, "_rev": "1-stale"}),   # initial GET
            httpx.Response(409, json={"error": "conflict"}),                 # first PUT
            httpx.Response(200, json={"_id": DOC_ID, "_rev": "2-fresh"}),   # refetch GET
            httpx.Response(201, json={"ok": True, "id": DOC_ID, "rev": "3-ok"}),  # retry PUT
        ])
        result = await client.put_note(vault_rel_path=DOC_ID, content=CONTENT, ctime_ms=CTIME, mtime_ms=MTIME)
        assert result.success is True
        assert len(transport.requests) == 4

        retry_body = json.loads(transport.requests[3].content)
        assert retry_body["_rev"] == "2-fresh"

    async def test_double_conflict_returns_failure(self):
        """Two 409s in a row SHALL return PutResult.success=False, error='conflict'."""
        client, _ = _make_client([
            httpx.Response(200, json={"_id": DOC_ID, "_rev": "1-a"}),
            httpx.Response(409, json={"error": "conflict"}),
            httpx.Response(200, json={"_id": DOC_ID, "_rev": "2-b"}),
            httpx.Response(409, json={"error": "conflict"}),
        ])
        result = await client.put_note(vault_rel_path=DOC_ID, content=CONTENT, ctime_ms=CTIME, mtime_ms=MTIME)
        assert result.success is False
        assert result.error == "conflict"


# ---------------------------------------------------------------------------
# Task 1.4 — chunking for large documents
# ---------------------------------------------------------------------------

class TestChunking:
    def _make_chunked_client(self, threshold: int, n_chunks: int):
        """Build a client and mock transport for a chunked write scenario."""
        responses = []
        # For each chunk: one GET (404) + one PUT (201)
        for _ in range(n_chunks):
            responses.append(httpx.Response(404, json={"error": "not_found"}))
            responses.append(httpx.Response(201, json={"ok": True, "id": "h:chunk", "rev": "1-x"}))
        # Parent document: one GET (404) + one PUT (201)
        responses.append(httpx.Response(404, json={"error": "not_found"}))
        responses.append(httpx.Response(201, json={"ok": True, "id": "parent", "rev": "1-y"}))

        transport = MockTransport(responses)
        http = httpx.AsyncClient(auth=("u", "p"), transport=transport)
        client = CouchDBVaultClient(
            url="http://couchdb:5984",
            username="u",
            password="p",
            database="db",
            chunk_threshold_bytes=threshold,
            http_client=http,
        )
        return client, transport

    async def test_small_content_no_chunks(self):
        """Content below threshold SHALL result in a single PUT (no chunk docs)."""
        client, transport = _make_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(201, json={"ok": True, "id": DOC_ID, "rev": "1-z"}),
        ])
        await client.put_note(vault_rel_path=DOC_ID, content="small", ctime_ms=CTIME, mtime_ms=MTIME)
        # Only 2 requests: one GET + one PUT
        assert len(transport.requests) == 2

    async def test_large_content_produces_chunks(self):
        """Content >= threshold SHALL produce N chunk PUTs before the parent PUT."""
        threshold = 10
        content = "a" * 25  # 3 chunks of 10, 10, 5 bytes
        n_chunks = 3
        client, transport = self._make_chunked_client(threshold, n_chunks)
        result = await client.put_note(
            vault_rel_path="big.md", content=content, ctime_ms=CTIME, mtime_ms=MTIME
        )
        assert result.success is True
        # 3 chunks × (GET + PUT) + parent (GET + PUT) = 8 requests
        assert len(transport.requests) == 8

    async def test_parent_has_children_list(self):
        """Parent document children SHALL list chunk IDs in order."""
        threshold = 10
        content = "b" * 20  # 2 chunks
        n_chunks = 2
        client, transport = self._make_chunked_client(threshold, n_chunks)
        await client.put_note(
            vault_rel_path="chunked.md", content=content, ctime_ms=CTIME, mtime_ms=MTIME
        )
        parent_put = transport.requests[-1]
        parent_body = json.loads(parent_put.content)
        assert len(parent_body["children"]) == 2
        assert all(c.startswith("h:") for c in parent_body["children"])

    async def test_parent_data_is_empty_when_chunked(self):
        """Chunked parent document SHALL have data=''."""
        threshold = 10
        content = "c" * 20
        n_chunks = 2
        client, transport = self._make_chunked_client(threshold, n_chunks)
        await client.put_note(
            vault_rel_path="chunked.md", content=content, ctime_ms=CTIME, mtime_ms=MTIME
        )
        parent_body = json.loads(transport.requests[-1].content)
        assert parent_body["data"] == ""

    async def test_chunk_put_failure_aborts_parent(self):
        """If a chunk PUT fails, the parent PUT SHALL NOT be attempted."""
        transport = MockTransport([
            httpx.Response(404, json={"error": "not_found"}),   # chunk GET
            httpx.Response(503, text="unavailable"),            # chunk PUT fails
        ])
        http = httpx.AsyncClient(auth=("u", "p"), transport=transport)
        client = CouchDBVaultClient(
            url="http://couchdb:5984", username="u", password="p",
            database="db", chunk_threshold_bytes=5, http_client=http,
        )
        result = await client.put_note(
            vault_rel_path="big.md", content="123456", ctime_ms=CTIME, mtime_ms=MTIME
        )
        assert result.success is False
        # Only chunk GET + chunk PUT = 2 requests; no parent request
        assert len(transport.requests) == 2


# ---------------------------------------------------------------------------
# Document format — matches LiveSync fixture
# ---------------------------------------------------------------------------

class TestLiveSyncFormat:
    async def test_document_matches_fixture_envelope(self):
        """Constructed document fields SHALL match the livesync_sample.json envelope."""
        import json as json_mod
        from pathlib import Path

        fixture_path = Path(__file__).parent / "fixtures" / "livesync_sample.json"
        fixture = json_mod.loads(fixture_path.read_text())

        client, transport = _make_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(201, json={"ok": True, "id": fixture["_id"], "rev": "1-x"}),
        ])
        await client.put_note(
            vault_rel_path=fixture["_id"],
            content=fixture["data"],
            ctime_ms=fixture["ctime"],
            mtime_ms=fixture["mtime"],
        )

        put_body = json.loads(transport.requests[1].content)
        for field in ("_id", "data", "ctime", "mtime", "size", "type", "children"):
            assert field in put_body, f"Missing field: {field}"
        assert put_body["type"] == "plain"
        assert isinstance(put_body["children"], list)
        assert isinstance(put_body["ctime"], int)
        assert isinstance(put_body["mtime"], int)
