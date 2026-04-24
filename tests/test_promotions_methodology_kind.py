from pydantic import ValidationError

from cairn.api.routes.promotions import PromoteRequest


def test_promote_request_methodology_kind_optional():
    req = PromoteRequest(narrative="x")
    assert req.methodology_kind is None


def test_promote_request_methodology_kind_accepted():
    req = PromoteRequest(narrative="x", methodology_kind="procedure")
    assert req.methodology_kind == "procedure"


def test_promote_request_methodology_kind_sigma_accepted():
    req = PromoteRequest(narrative="x", methodology_kind="sigma")
    assert req.methodology_kind == "sigma"


def test_promote_request_invalid_kind_rejected():
    try:
        PromoteRequest(narrative="x", methodology_kind="unknown")
        assert False, "Expected ValidationError"
    except ValidationError:
        assert True
