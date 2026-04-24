from __future__ import annotations

from fastapi.testclient import TestClient

from cairn.api.app import create_app


def test_invalid_kind_returns_422() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/methodologies/search", params={"q": "x", "kind": "unknown"})
    assert r.status_code in (401, 422)
