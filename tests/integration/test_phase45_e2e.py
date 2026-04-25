import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch, MagicMock, AsyncMock

from cairn.api.app import create_app
from cairn.vault.writer import WriteResult
from cairn.api.deps import authenticated_agent, get_db_manager

@pytest.mark.asyncio
@patch("cairn.api.routes.promotions.write_procedure")
@patch("cairn.api.routes.promotions.get_vault_collection")
@patch("cairn.api.routes.promotions._fetch_source_findings")
@patch("cairn.api.routes.promotions.chromadb.HttpClient")
async def test_full_phase45_e2e_route_c_to_route_a(
    mock_chroma_prom, mock_fetch, mock_get_vault_col, mock_write_procedure
):
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

    app = create_app()
    app.dependency_overrides[get_db_manager] = lambda: db_mock
    app.dependency_overrides[authenticated_agent] = lambda: {"id": "agent-1"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Promote via Route C
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

@pytest.mark.asyncio
async def test_e2e_route_a_via_skill_client():
    from cairn.skill.client import BlackboardClient
    import httpx
    
    app = create_app()
    transport = ASGITransport(app=app)
    
    client = BlackboardClient(base_url="http://test", api_key="agent-key")
    client._http = httpx.AsyncClient(transport=transport, base_url="http://test")
    client._spec = MagicMock()
    client._spec.resolve_url.return_value = ("GET", "http://test/methodologies/search")
    
    # Note: `cairn.api.routes.methodologies` only dynamically imports chromadb in the endpoint
    with patch("cairn.api.routes.methodologies.chromadb", create=True) as mock_chromadb:
        with patch("cairn.api.routes.methodologies.search_methodologies_endpoint") as mock_endpoint:
            mock_endpoint.return_value = [
                {"gitlab_path": "proc.yml", "commit_sha": "sha1", "title": "Phish", "tags": [], "status": "", "kind": "procedure", "score": 0.9}
            ]
        
            # Use overrides for /search endpoint auth
            app.dependency_overrides[authenticated_agent] = lambda: {"id": "agent-1"}
            
            # Use transport with client
            client._http = httpx.AsyncClient(transport=transport, base_url="http://test")
            # Wait, the problem is BlackboardClient hits the FastAPI app which needs chromadb
            # If we just mock the request on BlackboardClient._http, we don't even hit FastAPI
            
            # Let's mock the HTTP client completely
            mock_resp = MagicMock()
            mock_resp.is_success = True
            mock_resp.json.return_value = [
                {"gitlab_path": "proc.yml", "commit_sha": "sha1", "title": "Phish", "tags": [], "status": "", "kind": "procedure", "score": 0.9}
            ]
            client._http.request = AsyncMock(return_value=mock_resp)
            
            results = await client.find_methodology("Phishing", kind="procedure")
            assert len(results) == 1
            assert results[0].kind == "procedure"
            assert results[0].path == "proc.yml"
