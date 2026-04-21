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

"""Integration tests for vault writer CouchDB dual-write (Phase 4.4).

Tests that write_note() correctly:
- writes to disk regardless of CouchDB availability
- calls put_note() when a client is provided
- populates WriteResult accurately in both success and failure cases
- skips CouchDB entirely when no client is provided

No real CouchDB is needed; all CouchDB HTTP calls are intercepted by
MockTransport from test_couchdb_sync.py.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cairn.vault.couchdb_sync import CouchDBVaultClient, PutResult
from cairn.vault.writer import WriteResult, write_note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROMOTED_AT = "2026-04-20T10:00:00Z"
SOURCE_IDS = ["msg-abc", "msg-def"]


class MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._queue: deque[httpx.Response] = deque(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._queue.popleft() if self._queue else httpx.Response(500, json={"error": "exhausted"})


def _make_couchdb_client(responses: list[httpx.Response]) -> tuple[CouchDBVaultClient, MockTransport]:
    transport = MockTransport(responses)
    http = httpx.AsyncClient(auth=("cairn", "secret"), transport=transport)
    client = CouchDBVaultClient(
        url="http://couchdb:5984",
        username="cairn",
        password="secret",
        database="obsidian-livesync",
        http_client=http,
    )
    return client, transport


async def _write(
    vault_root: Path,
    entity: str = "APT29",
    entity_type: str = "actor",
    couchdb_client: CouchDBVaultClient | None = None,
) -> WriteResult:
    return await write_note(
        vault_root,
        entity=entity,
        entity_type=entity_type,
        narrative="Test finding.",
        source_message_ids=SOURCE_IDS,
        confidence=0.85,
        promoted_at=PROMOTED_AT,
        couchdb_client=couchdb_client,
    )


# ---------------------------------------------------------------------------
# No CouchDB client — disk-only path
# ---------------------------------------------------------------------------

class TestNoCouchDB:
    async def test_disk_file_created(self, tmp_path: Path):
        result = await _write(tmp_path)
        assert (tmp_path / result.vault_rel).exists()

    async def test_couchdb_synced_false(self, tmp_path: Path):
        result = await _write(tmp_path)
        assert result.couchdb_synced is False

    async def test_couchdb_error_none(self, tmp_path: Path):
        result = await _write(tmp_path)
        assert result.couchdb_error is None

    async def test_returns_write_result(self, tmp_path: Path):
        result = await _write(tmp_path)
        assert isinstance(result, WriteResult)
        assert result.vault_rel == "cairn/APT29.md"


# ---------------------------------------------------------------------------
# CouchDB available — dual-write success
# ---------------------------------------------------------------------------

class TestCouchDBSuccess:
    async def test_disk_file_and_couchdb_synced(self, tmp_path: Path):
        client, transport = _make_couchdb_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(201, json={"ok": True, "id": "cairn/APT29.md", "rev": "1-x"}),
        ])
        result = await _write(tmp_path, couchdb_client=client)
        assert (tmp_path / result.vault_rel).exists()
        assert result.couchdb_synced is True
        assert result.couchdb_error is None

    async def test_couchdb_received_put(self, tmp_path: Path):
        client, transport = _make_couchdb_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(201, json={"ok": True, "id": "cairn/APT29.md", "rev": "1-x"}),
        ])
        await _write(tmp_path, couchdb_client=client)
        put_requests = [r for r in transport.requests if r.method == "PUT"]
        assert len(put_requests) == 1


# ---------------------------------------------------------------------------
# CouchDB unavailable — disk write still succeeds
# ---------------------------------------------------------------------------

class TestCouchDBUnavailable:
    async def test_disk_file_present_when_couchdb_down(self, tmp_path: Path):
        client, _ = _make_couchdb_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(503, text="Service Unavailable"),
        ])
        result = await _write(tmp_path, couchdb_client=client)
        assert (tmp_path / result.vault_rel).exists()

    async def test_couchdb_synced_false_on_failure(self, tmp_path: Path):
        client, _ = _make_couchdb_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(503, text="Service Unavailable"),
        ])
        result = await _write(tmp_path, couchdb_client=client)
        assert result.couchdb_synced is False

    async def test_couchdb_error_populated_on_failure(self, tmp_path: Path):
        client, _ = _make_couchdb_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(503, text="Service Unavailable"),
        ])
        result = await _write(tmp_path, couchdb_client=client)
        assert result.couchdb_error is not None
        assert "503" in result.couchdb_error

    async def test_no_exception_raised_on_couchdb_failure(self, tmp_path: Path):
        client, _ = _make_couchdb_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(500, json={"error": "internal_server_error"}),
        ])
        # Should not raise
        result = await _write(tmp_path, couchdb_client=client)
        assert isinstance(result, WriteResult)


# ---------------------------------------------------------------------------
# Second promotion — CouchDB update carries _rev
# ---------------------------------------------------------------------------

class TestCouchDBUpdate:
    async def test_second_promotion_sends_rev(self, tmp_path: Path):
        """Second write to same entity should include _rev from first write."""
        # First write: new doc
        client, transport = _make_couchdb_client([
            httpx.Response(404, json={"error": "not_found"}),
            httpx.Response(201, json={"ok": True, "id": "cairn/APT29.md", "rev": "1-first"}),
            # Second write: doc exists with rev from first write
            httpx.Response(200, json={"_id": "cairn/APT29.md", "_rev": "1-first"}),
            httpx.Response(201, json={"ok": True, "id": "cairn/APT29.md", "rev": "2-second"}),
        ])
        await _write(tmp_path, couchdb_client=client)
        result2 = await _write(tmp_path, couchdb_client=client)

        assert result2.couchdb_synced is True
        # Second PUT should include _rev
        put_requests = [r for r in transport.requests if r.method == "PUT"]
        assert len(put_requests) == 2
        second_put_body = json.loads(put_requests[1].content)
        assert second_put_body.get("_rev") == "1-first"
