import pytest

from cairn.skill.client import BlackboardClient


class _DummySpec:
    def resolve_url(self, operation_id: str):
        assert operation_id == "search_methodologies"
        return operation_id, "/methodologies/search"


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_find_methodology_without_kind_omits_kind_param(monkeypatch):
    client = BlackboardClient(base_url="http://testserver", api_key="k")
    client._spec = _DummySpec()

    captured = {}

    async def _fake_request(method, url, *, params=None, json=None):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = params
        return _DummyResponse(
            [
                {
                    "gitlab_path": "sigma/a.yml",
                    "commit_sha": "abc",
                    "title": "A",
                    "tags": ["x"],
                    "score": 0.9,
                }
            ]
        )

    monkeypatch.setattr(client, "_request", _fake_request)

    out = await client.find_methodology("query", n=3)

    assert captured["method"] == "GET"
    assert captured["url"] == "/methodologies/search"
    assert captured["params"] == {"q": "query", "n": 3}
    assert len(out) == 1
    assert out[0].kind == "sigma"


@pytest.mark.asyncio
async def test_find_methodology_with_kind_adds_query_param(monkeypatch):
    client = BlackboardClient(base_url="http://testserver", api_key="k")
    client._spec = _DummySpec()

    captured = {}

    async def _fake_request(method, url, *, params=None, json=None):
        captured["params"] = params
        return _DummyResponse(
            [
                {
                    "gitlab_path": "methodologies/procedures/p.yml",
                    "commit_sha": "def",
                    "title": "Proc",
                    "tags": ["triage"],
                    "kind": "procedure",
                    "score": 0.88,
                }
            ]
        )

    monkeypatch.setattr(client, "_request", _fake_request)

    out = await client.find_methodology("query", n=5, kind="procedure")

    assert captured["params"] == {"q": "query", "n": 5, "kind": "procedure"}
    assert out[0].kind == "procedure"


@pytest.mark.asyncio
async def test_find_methodology_maps_kind_from_response(monkeypatch):
    client = BlackboardClient(base_url="http://testserver", api_key="k")
    client._spec = _DummySpec()

    async def _fake_request(method, url, *, params=None, json=None):
        return _DummyResponse(
            [
                {
                    "gitlab_path": "sigma/x.yml",
                    "commit_sha": "123",
                    "title": "Sigma X",
                    "tags": ["sigma"],
                    "kind": "sigma",
                    "score": 0.7,
                }
            ]
        )

    monkeypatch.setattr(client, "_request", _fake_request)

    out = await client.find_methodology("x")

    assert out[0].path == "sigma/x.yml"
    assert out[0].sha == "123"
    assert out[0].title == "Sigma X"
    assert out[0].tags == ["sigma"]
    assert out[0].kind == "sigma"
    assert out[0].score == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_find_methodology_defaults_kind_when_missing(monkeypatch):
    client = BlackboardClient(base_url="http://testserver", api_key="k")
    client._spec = _DummySpec()

    async def _fake_request(method, url, *, params=None, json=None):
        return _DummyResponse(
            [
                {
                    "gitlab_path": "methodologies/foo.yml",
                    "commit_sha": "456",
                    "title": "No Kind",
                    "tags": [],
                    "score": 0.5,
                }
            ]
        )

    monkeypatch.setattr(client, "_request", _fake_request)

    out = await client.find_methodology("foo")
    assert out[0].kind == "sigma"
