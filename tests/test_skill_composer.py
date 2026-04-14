"""Tests for cairn.skill.composer."""

from __future__ import annotations

import yaml
import pytest

from cairn.ingest.parser import parse_message
from cairn.skill.composer import compose_message


FAKE_ID = "019584a0-0000-7000-8000-000000000001"


class TestComposeMessage:
    def test_produces_valid_yaml_frontmatter(self):
        raw = compose_message(
            agent_id="test-agent",
            message_type="finding",
            body="Test body.",
        )
        assert raw.startswith("---\n")
        # Find frontmatter block and parse it.
        parts = raw.split("---\n", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["agent_id"]     == "test-agent"
        assert fm["message_type"] == "finding"

    def test_body_present_after_closing_delimiter(self):
        raw = compose_message(
            agent_id="a", message_type="finding", body="Hello world."
        )
        assert "Hello world." in raw
        # Format is: ---\n{frontmatter}\n---\n\n{body}
        # Partition on the closing \n---\n to get everything after it.
        _, _, body_section = raw.partition("\n---\n")
        assert "Hello world." in body_section

    def test_optional_fields_omitted_when_none(self):
        raw = compose_message(
            agent_id="a", message_type="finding", body="b"
        )
        fm = yaml.safe_load(raw.split("---\n", 2)[1])
        assert "thread_id"   not in fm
        assert "in_reply_to" not in fm
        assert "confidence"  not in fm
        assert "tlp_level"   not in fm
        # promote=none is also omitted for cleanliness
        assert "promote"     not in fm

    def test_promote_included_when_non_default(self):
        raw = compose_message(
            agent_id="a", message_type="finding", body="b", promote="candidate"
        )
        fm = yaml.safe_load(raw.split("---\n", 2)[1])
        assert fm["promote"] == "candidate"

    def test_tags_serialised_as_list(self):
        raw = compose_message(
            agent_id="a", message_type="finding", body="b",
            tags=["apt29", "lateral-movement"],
        )
        fm = yaml.safe_load(raw.split("---\n", 2)[1])
        assert fm["tags"] == ["apt29", "lateral-movement"]

    def test_confidence_rounded_to_4dp(self):
        raw = compose_message(
            agent_id="a", message_type="finding", body="b", confidence=0.123456789
        )
        fm = yaml.safe_load(raw.split("---\n", 2)[1])
        assert fm["confidence"] == 0.1235

    def test_extra_frontmatter_fields_included(self):
        raw = compose_message(
            agent_id="a", message_type="alert", body="b",
            scanner_run_id="scan-001", severity="p1",
        )
        fm = yaml.safe_load(raw.split("---\n", 2)[1])
        assert fm["scanner_run_id"] == "scan-001"
        assert fm["severity"]       == "p1"

    def test_round_trip_through_parser(self):
        """compose_message output must be accepted by parse_message."""
        raw = compose_message(
            agent_id="round-trip-agent",
            message_type="hypothesis",
            body="Could be a false positive.",
            thread_id="thread-abc",
            tags=["fp-candidate"],
            confidence=0.4,
            tlp_level="green",
            custom_key="custom_val",
        )
        record = parse_message(raw, FAKE_ID)
        assert record.agent_id     == "round-trip-agent"
        assert record.message_type.value == "hypothesis"
        assert record.thread_id    == "thread-abc"
        assert record.tags         == ["fp-candidate"]
        assert record.confidence   == pytest.approx(0.4)
        assert record.ext.get("custom_key") == "custom_val"
        assert "Could be a false positive." in record.body

    def test_timestamp_string_accepted(self):
        raw = compose_message(
            agent_id="a", message_type="finding", body="b",
            timestamp="2026-04-14T10:00:00Z",
        )
        fm = yaml.safe_load(raw.split("---\n", 2)[1])
        assert "2026-04-14" in str(fm["timestamp"])
