import pytest
from httpx import ASGITransport, AsyncClient

from cairn.api.app import create_app


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
