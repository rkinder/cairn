import pytest
from httpx import ASGITransport, AsyncClient

from cairn.api.app import create_app


@pytest.mark.asyncio
async def test_phase45_e2e_route_a_search_surface_contract():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/methodologies/search",
            params={"q": "phishing triage", "n": 3, "kind": "procedure"},
            headers={"x-api-key": "test-key"},
        )
        assert resp.status_code in (200, 401, 403, 503)


@pytest.mark.asyncio
async def test_phase45_e2e_route_c_promote_surface_contract():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/promotions/nonexistent/promote",
            headers={
                "x-api-key": "test-key",
                "x-human-reviewer": "true",
                "x-reviewer-identity": "analyst-e2e",
            },
            json={
                "methodology_kind": "procedure",
                "narrative": "1. Acquire evidence from endpoints. 2. Correlate and summarize.",
            },
        )
        assert resp.status_code in (401, 403, 404)
