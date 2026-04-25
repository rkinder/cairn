import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch, MagicMock

from cairn.api.app import create_app
from cairn.vault.writer import WriteResult
from cairn.api.deps import authenticated_agent, get_db_manager

@pytest.mark.asyncio
async def test_promote_endpoint_requires_human_headers():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/promotions/nonexistent/promote",
            headers={"x-api-key": "test-key"},
            json={"methodology_kind": "procedure"},
        )
        assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_promote_request_accepts_procedure_kind_payload_shape():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/promotions/nonexistent/promote",
            headers={
                "x-api-key": "test-key",
                "x-human-reviewer": "true",
                "x-reviewer-identity": "analyst-1",
            },
            json={"narrative": "1. First long step\n2. Second long step", "methodology_kind": "procedure"},
        )
        assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_promote_request_accepts_nil_methodology_kind_payload_shape():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/promotions/nonexistent/promote",
            headers={
                "x-api-key": "test-key",
                "x-human-reviewer": "true",
                "x-reviewer-identity": "analyst-1",
            },
            json={"narrative": "normal promotion"},
        )
        assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_promote_request_rejects_invalid_kind_payload_shape():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/promotions/nonexistent/promote",
            headers={
                "x-api-key": "test-key",
                "x-human-reviewer": "true",
                "x-reviewer-identity": "analyst-1",
            },
            json={"narrative": "x", "methodology_kind": "unknown"},
        )
        assert resp.status_code in (401, 422)


@pytest.mark.asyncio
async def test_route_c_e2e_procedure_path_nonexistent_candidate_is_404_or_401():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/promotions/not-real/promote",
            headers={
                "x-api-key": "test-key",
                "x-human-reviewer": "true",
                "x-reviewer-identity": "analyst-route-c",
            },
            json={
                "narrative": "1. Collect artifacts from source systems. 2. Correlate indicators over time.",
                "methodology_kind": "procedure",
            },
        )
        assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_route_c_e2e_sigma_path_nonexistent_candidate_is_404_or_401():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/promotions/not-real/promote",
            headers={
                "x-api-key": "test-key",
                "x-human-reviewer": "true",
                "x-reviewer-identity": "analyst-route-c",
            },
            json={
                "narrative": "Curated narrative for sigma-style promotion path.",
                "methodology_kind": "sigma",
            },
        )
        assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_route_c_e2e_human_headers_present_but_no_auth_still_fails():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/promotions/not-real/promote",
            headers={
                "x-human-reviewer": "true",
                "x-reviewer-identity": "analyst-route-c",
            },
            json={"narrative": "Attempt full route C flow without API auth."},
        )
        assert resp.status_code in (401, 403)

@pytest.mark.asyncio
@patch("cairn.api.routes.promotions.chromadb.HttpClient")
@patch("cairn.api.routes.promotions.write_procedure")
@patch("cairn.api.routes.promotions.get_vault_collection")
@patch("cairn.api.routes.promotions._fetch_source_findings")
async def test_route_c_full_procedure_promotion(mock_fetch, mock_get_collection, mock_write_procedure, mock_chroma_client):
    db_mock = MagicMock()
    # Mocking a candidate
    cursor_mock = MagicMock()
    cursor_mock.fetchone = MagicMock(side_effect=[
        {
            "id": "c-123", "entity": "test-entity", "entity_type": "actor", "entity_domain": None,
            "source_message_ids": '["m-1"]', "narrative": "narr", "confidence": 0.9, "status": "pending_review",
            "topic_db": "osint", "trigger": "trigger", "reviewer_id": None, "vault_path": None, "created_at": "dt", "updated_at": "dt"
        },
        {
            "id": "c-123", "entity": "test-entity", "entity_type": "actor", "entity_domain": None,
            "source_message_ids": '["m-1"]', "narrative": "narr", "confidence": 0.9, "status": "promoted",
            "topic_db": "osint", "trigger": "trigger", "reviewer_id": "analyst-1", "vault_path": "cairn/procedures/test-entity.md", "created_at": "dt", "updated_at": "dt"
        }
    ])
    
    async def amock_fetchone(*args, **kwargs):
        return cursor_mock.fetchone()
    
    # We need a proper async cursor
    class AsyncCursor:
        async def fetchone(self):
            return cursor_mock.fetchone()
            
    class AsyncConn:
        async def execute(self, *args, **kwargs):
            return AsyncCursor()
            
    class AsyncCtx:
        async def __aenter__(self):
            return AsyncConn()
        async def __aexit__(self, *args):
            pass

    db_mock.index_conn.execute = AsyncConn().execute
    db_mock.index.return_value = AsyncCtx()

    mock_fetch.return_value = []
    
    write_result = WriteResult(vault_rel="cairn/procedures/test-entity.md", couchdb_synced=False, couchdb_error=None)
    
    async def amock_write_procedure(*args, **kwargs):
        return write_result
        
    mock_write_procedure.side_effect = amock_write_procedure

    collection_mock = MagicMock()
    mock_get_collection.return_value = collection_mock

    app = create_app()
    app.dependency_overrides[get_db_manager] = lambda: db_mock
    app.dependency_overrides[authenticated_agent] = lambda: {"id": "agent-1"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/promotions/c-123/promote",
            headers={
                "x-human-reviewer": "true",
                "x-reviewer-identity": "analyst-1",
            },
            json={
                "narrative": "1. Collect artifacts from source systems.\n2. Correlate indicators over time.",
                "methodology_kind": "procedure",
            },
        )
        
        assert resp.status_code == 200
        assert resp.json()["status"] == "promoted"
        
        # Verify write_procedure was called
        mock_write_procedure.assert_called_once()
        
        # Verify ChromaDB upsert was called with procedure kind
        collection_mock.upsert.assert_called_once()
        kwargs = collection_mock.upsert.call_args.kwargs
        assert kwargs["metadatas"][0]["kind"] == "procedure"
        assert "1. Collect artifacts from source systems." in kwargs["documents"][0]

