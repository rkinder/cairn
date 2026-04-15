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

"""Integration tests for BlackboardClient against the real FastAPI app.

Uses httpx.AsyncClient with ASGITransport so no server process is needed.
Databases are created in a temp directory per test session.

Note: httpx.ASGITransport does not run the ASGI lifespan, so the
``live_app`` fixture drives the lifespan protocol manually via asyncio
Queues before yielding the initialized app to other fixtures.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from cairn.api.app import create_app
from cairn.api.auth import hash_api_key
from cairn.db.ids import new_id
from cairn.db.init import init_all
from cairn.skill import BlackboardClient
from cairn.skill.exceptions import AuthError, ForbiddenError, NotFoundError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def data_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("cairn_data")
    await init_all(d)
    return d


@pytest_asyncio.fixture(scope="module")
async def agent_key_pair(data_dir) -> tuple[str, str]:
    """Return (agent_id, raw_api_key) and insert the agent into index.db."""
    import aiosqlite
    from datetime import datetime, timezone

    agent_id  = "test-agent-01"
    raw_key   = "cairn_testkey_abc123"
    key_hash  = hash_api_key(raw_key)
    now       = datetime.now(tz=timezone.utc).isoformat()

    async with aiosqlite.connect(data_dir / "index.db") as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO agents
                (id, display_name, description, api_key_hash,
                 capabilities, allowed_dbs, is_active, created_at, ext)
            VALUES (?, ?, '', ?, '[]', '[]', 1, ?, '{}')
            """,
            (agent_id, "Test Agent", key_hash, now),
        )
        await db.commit()
    return agent_id, raw_key


@pytest_asyncio.fixture(scope="module")
async def live_app(data_dir, agent_key_pair):
    """Create the FastAPI app, run its ASGI lifespan, yield the ready app.

    httpx.ASGITransport does not trigger the ASGI lifespan event, so we
    drive the lifespan protocol manually: push ``lifespan.startup`` into the
    receive queue, wait for ``lifespan.startup.complete``, yield, then push
    ``lifespan.shutdown`` and wait for the clean-up to finish.
    """
    import os
    os.environ["CAIRN_DATA_DIR"] = str(data_dir)
    from cairn.config import get_settings
    get_settings.cache_clear()

    app = create_app()

    rx: asyncio.Queue = asyncio.Queue()   # messages the app reads
    tx: asyncio.Queue = asyncio.Queue()   # messages the app writes

    await rx.put({"type": "lifespan.startup"})

    async def receive():
        return await rx.get()

    async def send(message):
        await tx.put(message)

    scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}
    lifespan_task = asyncio.create_task(app(scope, receive, send))

    # Wait for the app to finish its startup.
    msg = await tx.get()
    if msg["type"] != "lifespan.startup.complete":
        raise RuntimeError(f"App startup failed: {msg}")

    yield app

    # Graceful shutdown.
    await rx.put({"type": "lifespan.shutdown"})
    await tx.get()  # lifespan.shutdown.complete
    await lifespan_task


@pytest_asyncio.fixture(scope="module")
async def asgi_client(live_app) -> httpx.AsyncClient:
    """An unauthenticated httpx client wired to the live FastAPI app."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=live_app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest_asyncio.fixture(scope="module")
async def bb(live_app, agent_key_pair, tmp_path_factory) -> BlackboardClient:
    """A BlackboardClient wired to the test app with valid credentials."""
    _, raw_key  = agent_key_pair
    cache_path  = tmp_path_factory.mktemp("spec_cache") / "spec.json"

    client = BlackboardClient(
        base_url="http://testserver",
        api_key=raw_key,
        spec_cache_path=cache_path,
    )
    # Use a separate authenticated client so every request carries the Bearer token.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=live_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {raw_key}"},
    ) as auth_http:
        client._http       = auth_http
        client._spec._http = auth_http
        await client.refresh_spec(force=True)
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPostMessage:
    @pytest_asyncio.fixture
    async def posted(self, bb, agent_key_pair) -> str:
        agent_id, _ = agent_key_pair
        result = await bb.post_message(
            db="osint",
            agent_id=agent_id,
            message_type="finding",
            body="Observed suspicious named pipe on HOST-DELTA.",
            tags=["apt29", "lateral-movement"],
            confidence=0.87,
            tlp_level="amber",
        )
        return result.id

    async def test_returns_message_id(self, bb, agent_key_pair):
        agent_id, _ = agent_key_pair
        result = await bb.post_message(
            db="osint",
            agent_id=agent_id,
            message_type="finding",
            body="Finding body.",
        )
        assert result.id
        assert result.topic_db == "osint"

    async def test_wrong_agent_id_raises_forbidden(self, bb):
        with pytest.raises(ForbiddenError):
            await bb.post_message(
                db="osint",
                agent_id="other-agent",
                message_type="finding",
                body="Impersonation attempt.",
            )

    async def test_unknown_db_raises(self, bb, agent_key_pair):
        agent_id, _ = agent_key_pair
        from cairn.skill.exceptions import SkillError
        with pytest.raises(SkillError):
            await bb.post_message(
                db="nonexistent",
                agent_id=agent_id,
                message_type="finding",
                body="Test.",
            )


class TestQueryMessages:
    async def test_returns_list(self, bb):
        results = await bb.query_messages()
        assert isinstance(results, list)

    async def test_filter_by_db(self, bb):
        results = await bb.query_messages(db="osint")
        assert all(r.topic_db == "osint" for r in results)

    async def test_filter_by_tags(self, bb, agent_key_pair):
        agent_id, _ = agent_key_pair
        await bb.post_message(
            db="osint", agent_id=agent_id, message_type="alert",
            body="Tagged message.", tags=["unique-tag-xyz"],
        )
        results = await bb.query_messages(tags=["unique-tag-xyz"])
        assert len(results) >= 1
        assert all("unique-tag-xyz" in r.tags for r in results)

    async def test_limit_respected(self, bb):
        results = await bb.query_messages(limit=2)
        assert len(results) <= 2


class TestGetMessage:
    async def test_returns_full_record(self, bb, agent_key_pair):
        agent_id, _ = agent_key_pair
        result = await bb.post_message(
            db="osint", agent_id=agent_id, message_type="finding",
            body="Full record test.", tags=["detail-test"],
        )
        detail = await bb.get_message(result.id, db="osint")
        assert detail.id        == result.id
        assert "Full record"    in detail.body
        assert detail.raw_content.startswith("---")

    async def test_nonexistent_message_raises(self, bb):
        with pytest.raises(NotFoundError):
            await bb.get_message("nonexistent-id", db="osint")


class TestFlagForPromotion:
    async def test_sets_candidate_status(self, bb, agent_key_pair):
        agent_id, _ = agent_key_pair
        result = await bb.post_message(
            db="osint", agent_id=agent_id, message_type="finding",
            body="Promotion candidate.", confidence=0.9,
        )
        promote_result = await bb.flag_for_promotion(result.id, db="osint")
        assert promote_result.promote == "candidate"
        assert promote_result.id      == result.id

    async def test_updates_confidence(self, bb, agent_key_pair):
        agent_id, _ = agent_key_pair
        result = await bb.post_message(
            db="osint", agent_id=agent_id, message_type="finding",
            body="Confidence update test.",
        )
        promote_result = await bb.flag_for_promotion(
            result.id, db="osint", confidence=0.95
        )
        assert promote_result.confidence == pytest.approx(0.95)

    async def test_wrong_agent_raises_forbidden(self, bb, live_app, data_dir, tmp_path):
        """An agent that did not author the message cannot flag it."""
        import aiosqlite
        from datetime import datetime, timezone
        from cairn.api.auth import hash_api_key

        # Create a second agent.
        other_id  = "other-agent-02"
        other_key = "cairn_otherkey_xyz"
        now       = datetime.now(tz=timezone.utc).isoformat()
        async with aiosqlite.connect(data_dir / "index.db") as db:
            await db.execute(
                "INSERT OR REPLACE INTO agents "
                "(id, display_name, description, api_key_hash, capabilities, allowed_dbs, is_active, created_at, ext) "
                "VALUES (?, '', '', ?, '[]', '[]', 1, ?, '{}')",
                (other_id, hash_api_key(other_key), now),
            )
            await db.commit()

        # Post a message as the original agent.
        agent_id = "test-agent-01"
        result = await bb.post_message(
            db="osint", agent_id=agent_id, message_type="finding",
            body="Authored by test-agent-01.",
        )

        # Try to flag it as the other agent.
        other_bb = BlackboardClient(
            base_url="http://testserver",
            api_key=other_key,
            spec_cache_path=tmp_path / "other_spec.json",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=live_app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {other_key}"},
        ) as other_http:
            other_bb._http       = other_http
            other_bb._spec._http = other_http
            await other_bb.refresh_spec(force=True)

            with pytest.raises(ForbiddenError):
                await other_bb.flag_for_promotion(result.id, db="osint")


class TestBadAuth:
    async def test_invalid_key_raises_auth_error(self, live_app, tmp_path):
        bad_bb = BlackboardClient(
            base_url="http://testserver",
            api_key="cairn_invalid",
            spec_cache_path=tmp_path / "bad_spec.json",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=live_app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer cairn_invalid"},
        ) as bad_http:
            bad_bb._http       = bad_http
            bad_bb._spec._http = bad_http
            await bad_bb.refresh_spec(force=True)

            with pytest.raises(AuthError):
                await bad_bb.query_messages()
