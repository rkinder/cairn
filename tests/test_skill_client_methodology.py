import pytest
from httpx import AsyncClient
from unittest.mock import patch, MagicMock, AsyncMock

from cairn.skill.client import BlackboardClient, MethodologyRef

@pytest.mark.asyncio
async def test_skill_client_find_methodology_no_kind():
    client = BlackboardClient(base_url="http://test", api_key="test")
    client._http = AsyncMock()
    client._spec = MagicMock()
    client._spec.resolve_url.return_value = ("GET", "http://test/methodologies/search")
    
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.json.return_value = [
        {"gitlab_path": "path", "commit_sha": "sha", "score": 0.9}
    ]
    client._http.request.return_value = mock_resp
    
    res = await client.find_methodology("query")
    assert len(res) == 1
    assert res[0].kind == "sigma"
    client._http.request.assert_called_with(
        "GET", "http://test/methodologies/search", params={"q": "query", "n": 5}, json=None
    )

@pytest.mark.asyncio
async def test_skill_client_find_methodology_with_kind():
    client = BlackboardClient(base_url="http://test", api_key="test")
    client._http = AsyncMock()
    client._spec = MagicMock()
    client._spec.resolve_url.return_value = ("GET", "http://test/methodologies/search")
    
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.json.return_value = [
        {"gitlab_path": "path", "commit_sha": "sha", "score": 0.9, "kind": "procedure"}
    ]
    client._http.request.return_value = mock_resp
    
    res = await client.find_methodology("query", kind="procedure")
    assert len(res) == 1
    assert res[0].kind == "procedure"
    client._http.request.assert_called_with(
        "GET", "http://test/methodologies/search", params={"q": "query", "n": 5, "kind": "procedure"}, json=None
    )

@pytest.mark.asyncio
async def test_skill_client_find_methodology_parses_tags():
    client = BlackboardClient(base_url="http://test", api_key="test")
    client._http = AsyncMock()
    client._spec = MagicMock()
    client._spec.resolve_url.return_value = ("GET", "http://test/methodologies/search")
    
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.json.return_value = [
        {"gitlab_path": "path", "commit_sha": "sha", "score": 0.9, "tags": ["tag1", "tag2"], "title": "t", "kind": "sigma"}
    ]
    client._http.request.return_value = mock_resp
    
    res = await client.find_methodology("query")
    assert len(res) == 1
    assert res[0].tags == ["tag1", "tag2"]
    assert res[0].title == "t"

@pytest.mark.asyncio
async def test_skill_client_methodology_ref_dataclass():
    ref = MethodologyRef(
        path="p",
        sha="s",
        title="t",
        tags=["a"],
        kind="procedure",
        score=1.0
    )
    assert ref.path == "p"
    assert ref.kind == "procedure"
