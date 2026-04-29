import asyncio
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import httpx
import pytest
import pytest_asyncio

from cairn.api.app import create_app
from cairn.api.auth import hash_api_key
from cairn.db.init import init_all


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def data_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("cairn_delete_api_data")
    await init_all(d)
    return d


@pytest_asyncio.fixture(scope="module")
async def agent_keys(data_dir):
    """Create owner, other, and admin agents with API keys."""
    owner_id, owner_key = "delete-owner", "cairn_owner_key_123"
    other_id, other_key = "delete-other", "cairn_other_key_123"
    admin_id, admin_key = "delete-admin", "cairn_admin_key_123"

    now = datetime.now(tz=timezone.utc).isoformat()

    async with aiosqlite.connect(data_dir / "index.db") as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO agents
                (id, display_name, description, api_key_hash,
                 capabilities, allowed_dbs, is_active, created_at, ext)
            VALUES (?, ?, '', ?, '[]', '[]', 1, ?, '{}')
            """,
            (owner_id, "Owner Agent", hash_api_key(owner_key), now),
        )
        await db.execute(
            """
            INSERT OR REPLACE INTO agents
                (id, display_name, description, api_key_hash,
                 capabilities, allowed_dbs, is_active, created_at, ext)
            VALUES (?, ?, '', ?, '[]', '[]', 1, ?, '{}')
            """,
            (other_id, "Other Agent", hash_api_key(other_key), now),
        )
        await db.execute(
            """
            INSERT OR REPLACE INTO agents
                (id, display_name, description, api_key_hash,
                 capabilities, allowed_dbs, is_active, created_at, ext)
            VALUES (?, ?, '', ?, '["admin"]', '[]', 1, ?, '{}')
            """,
            (admin_id, "Admin Agent", hash_api_key(admin_key), now),
        )
        await db.commit()

    return {
        "owner": (owner_id, owner_key),
        "other": (other_id, other_key),
        "admin": (admin_id, admin_key),
    }


@pytest_asyncio.fixture(scope="module")
async def live_app(data_dir, agent_keys):
    import os
    from cairn.config import get_settings

    os.environ["CAIRN_DATA_DIR"] = str(data_dir)
    get_settings.cache_clear()

    app = create_app()

    rx: asyncio.Queue = asyncio.Queue()
    tx: asyncio.Queue = asyncio.Queue()

    await rx.put({"type": "lifespan.startup"})

    async def receive():
        return await rx.get()

    async def send(message):
        await tx.put(message)

    scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}
    lifespan_task = asyncio.create_task(app(scope, receive, send))

    msg = await tx.get()
    if msg["type"] != "lifespan.startup.complete":
        raise RuntimeError(f"App startup failed: {msg}")

    yield app

    await rx.put({"type": "lifespan.shutdown"})
    await tx.get()
    await lifespan_task


@pytest_asyncio.fixture
async def owner_client(live_app, agent_keys):
    _, key = agent_keys["owner"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=live_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {key}"},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def other_client(live_app, agent_keys):
    _, key = agent_keys["other"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=live_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {key}"},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def admin_client(live_app, agent_keys):
    _, key = agent_keys["admin"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=live_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {key}"},
    ) as client:
        yield client


def _raw_message(agent_id: str, tags: list[str], body: str, thread_id: str = "thr-del") -> str:
    return (
        "---\n"
        f"agent_id: {agent_id}\n"
        f"thread_id: {thread_id}\n"
        "message_type: finding\n"
        f"tags: [{', '.join(tags)}]\n"
        f"timestamp: {datetime.now(tz=timezone.utc).isoformat()}\n"
        "---\n"
        f"{body}\n"
    )


async def _post(owner_client: httpx.AsyncClient, agent_id: str, tags: list[str], body: str, thread_id: str = "thr-del"):
    resp = await owner_client.post(
        "/messages?db=osint",
        json={"raw_content": _raw_message(agent_id, tags, body, thread_id=thread_id)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_delete_message_soft_owner_flow(owner_client, other_client, admin_client, agent_keys):
    owner_id, _ = agent_keys["owner"]
    message_id = await _post(owner_client, owner_id, ["del-soft"], "soft delete target")

    resp = await owner_client.delete(f"/messages/{message_id}?db=osint")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["deleted"] is True
    assert data["hard_deleted"] is False
    assert data["deleted_at"] is not None
    assert data["deleted_by"] == owner_id

    hidden = await owner_client.get(f"/messages/{message_id}?db=osint")
    assert hidden.status_code == 404

    admin_visible = await admin_client.get(f"/messages/{message_id}?db=osint&include_deleted=true")
    assert admin_visible.status_code == 200
    d = admin_visible.json()
    assert d["deleted_at"] is not None
    assert d["deleted_by"] == owner_id

    forbidden = await other_client.delete(f"/messages/{message_id}?db=osint")
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_delete_message_hard_admin_only(owner_client, other_client, admin_client, agent_keys):
    owner_id, _ = agent_keys["owner"]
    message_id = await _post(owner_client, owner_id, ["del-hard"], "hard delete target")

    non_admin = await owner_client.delete(f"/messages/{message_id}?db=osint&hard=true")
    assert non_admin.status_code == 403

    admin_del = await admin_client.delete(f"/messages/{message_id}?db=osint&hard=true")
    assert admin_del.status_code == 200
    data = admin_del.json()
    assert data["hard_deleted"] is True

    missing = await admin_client.get(f"/messages/{message_id}?db=osint&include_deleted=true")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_bulk_delete_by_tags(owner_client, admin_client, agent_keys):
    owner_id, _ = agent_keys["owner"]
    m1 = await _post(owner_client, owner_id, ["bulk-x", "common"], "bulk-1", thread_id="thr-bulk")
    m2 = await _post(owner_client, owner_id, ["bulk-y", "common"], "bulk-2", thread_id="thr-bulk")
    _ = await _post(owner_client, owner_id, ["bulk-z"], "bulk-3", thread_id="thr-bulk")

    no_confirm = await owner_client.request(
        "DELETE",
        "/messages",
        params={"db": "osint", "tags": "common", "confirm": "false"},
    )
    assert no_confirm.status_code == 400

    ok = await owner_client.request(
        "DELETE",
        "/messages",
        params={"db": "osint", "tags": "common", "confirm": "true"},
    )
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["deleted_count"] >= 2
    assert m1 in data["deleted_ids"]
    assert m2 in data["deleted_ids"]

    q = await owner_client.get("/messages?db=osint&tags=common")
    assert q.status_code == 200
    for row in q.json():
        assert row["id"] not in {m1, m2}

    q_admin = await admin_client.get("/messages?db=osint&tags=common&include_deleted=true")
    assert q_admin.status_code == 200
    ids = {row["id"] for row in q_admin.json()}
    assert m1 in ids and m2 in ids


@pytest.mark.asyncio
async def test_delete_thread(owner_client, admin_client, agent_keys):
    owner_id, _ = agent_keys["owner"]
    t1 = await _post(owner_client, owner_id, ["thr-a"], "thread-a1", thread_id="thr-delete-me")
    t2 = await _post(owner_client, owner_id, ["thr-b"], "thread-a2", thread_id="thr-delete-me")
    _ = await _post(owner_client, owner_id, ["thr-c"], "thread-other", thread_id="thr-keep")

    resp = await owner_client.delete("/messages/thread/thr-delete-me?db=osint")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["deleted_count"] >= 2
    assert t1 in data["deleted_ids"]
    assert t2 in data["deleted_ids"]

    q = await owner_client.get("/messages?db=osint&thread_id=thr-delete-me")
    assert q.status_code == 200
    assert all(item["id"] not in {t1, t2} for item in q.json())

    q_admin = await admin_client.get("/messages?db=osint&thread_id=thr-delete-me&include_deleted=true")
    assert q_admin.status_code == 200
    ids = {row["id"] for row in q_admin.json()}
    assert t1 in ids and t2 in ids
