from __future__ import annotations

import pytest
from pydantic import ValidationError

from cairn.models.methodology import ProcedureMethodology


def _valid_payload() -> dict:
    return {
        "title": "Phishing triage",
        "tags": ["phishing", "triage"],
        "steps": [
            "Collect full message headers from the report.",
            "Validate SPF, DKIM, and DMARC alignment status.",
        ],
        "description": "Procedure for initial phishing triage.",
        "references": ["https://example.com/runbook"],
        "author": "soc-analyst",
        "severity": "medium",
    }


def test_valid_model() -> None:
    model = ProcedureMethodology.model_validate(_valid_payload())
    assert model.title == "Phishing triage"


def test_missing_title_fails() -> None:
    payload = _valid_payload()
    payload.pop("title")
    with pytest.raises(ValidationError):
        ProcedureMethodology.model_validate(payload)


def test_empty_steps_fails() -> None:
    payload = _valid_payload()
    payload["steps"] = []
    with pytest.raises(ValidationError):
        ProcedureMethodology.model_validate(payload)


def test_single_step_fails() -> None:
    payload = _valid_payload()
    payload["steps"] = ["Only one step here."]
    with pytest.raises(ValidationError):
        ProcedureMethodology.model_validate(payload)


def test_optional_fields_default() -> None:
    model = ProcedureMethodology.model_validate(
        {
            "title": "Procedure",
            "tags": ["x"],
            "steps": ["Step one is sufficiently long.", "Step two is sufficiently long."],
        }
    )
    assert model.description is None
    assert model.author is None
    assert model.severity is None


def test_references_default_empty() -> None:
    model = ProcedureMethodology.model_validate(
        {
            "title": "Procedure",
            "tags": ["x"],
            "steps": ["Step one is sufficiently long.", "Step two is sufficiently long."],
        }
    )
    assert model.references == []


def test_severity_enum_invalid() -> None:
    payload = _valid_payload()
    payload["severity"] = "urgent"
    with pytest.raises(ValidationError):
        ProcedureMethodology.model_validate(payload)


def test_severity_enum_valid() -> None:
    for sev in ["low", "medium", "high", "critical"]:
        payload = _valid_payload()
        payload["severity"] = sev
        model = ProcedureMethodology.model_validate(payload)
        assert model.severity == sev
