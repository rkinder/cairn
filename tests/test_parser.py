# Copyright (C) 2026 Ryan Kinder
#
# This file is part of Cairn.
#
# Cairn is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# Cairn is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for
# more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Cairn. If not, see <https://www.gnu.org/licenses/>.

"""Tests for the YAML frontmatter parser."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cairn.ingest.parser import (
    ParseError,
    build_record,
    parse_frontmatter_yaml,
    parse_message,
    split_frontmatter,
    validate_frontmatter,
)
from cairn.models.message import MessageType, PromoteStatus, TLPLevel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_RAW = """\
---
agent_id: osint-agent-01
timestamp: 2026-04-14T10:32:00Z
message_type: finding
---

Observed named pipe consistent with Cobalt Strike default configuration.
"""

FULL_RAW = """\
---
agent_id: osint-agent-01
timestamp: 2026-04-14T10:32:00Z
message_type: finding
thread_id: apt29-campaign-thread
in_reply_to: 019584a0-1234-7000-8000-000000000001
tags: [lateral-movement, apt29, named-pipes]
confidence: 0.87
tlp_level: amber
promote: candidate
---

Observed named pipe `\\.\\pipe\\msagent_81` on HOST-DELTA consistent with
Cobalt Strike default configuration.
"""

EXT_FIELDS_RAW = """\
---
agent_id: vuln-agent-01
timestamp: 2026-04-14T11:00:00Z
message_type: alert
custom_severity: p1
scanner_run_id: scan-20260414-001
---

Critical finding requiring immediate attention.
"""


# ---------------------------------------------------------------------------
# split_frontmatter
# ---------------------------------------------------------------------------

class TestSplitFrontmatter:
    def test_splits_minimal_message(self):
        fm, body = split_frontmatter(MINIMAL_RAW)
        assert "agent_id: osint-agent-01" in fm
        assert "Observed named pipe" in body

    def test_body_leading_newline_stripped(self):
        _, body = split_frontmatter(MINIMAL_RAW)
        assert not body.startswith("\n")

    def test_splits_full_message(self):
        fm, body = split_frontmatter(FULL_RAW)
        assert "thread_id: apt29-campaign-thread" in fm
        assert "HOST-DELTA" in body

    def test_no_frontmatter_raises(self):
        with pytest.raises(ParseError, match="frontmatter block"):
            split_frontmatter("Just a plain markdown document.")

    def test_unclosed_frontmatter_raises(self):
        with pytest.raises(ParseError):
            split_frontmatter("---\nagent_id: x\n")

    def test_leading_whitespace_allowed(self):
        raw = "  \n---\nagent_id: x\ntimestamp: 2026-01-01T00:00:00Z\nmessage_type: finding\n---\nbody"
        fm, body = split_frontmatter(raw)
        assert "agent_id: x" in fm

    def test_dashes_in_body_not_treated_as_delimiter(self):
        raw = "---\nagent_id: x\ntimestamp: 2026-01-01T00:00:00Z\nmessage_type: finding\n---\nLine one\n---\nLine two\n"
        _, body = split_frontmatter(raw)
        assert "Line one" in body
        assert "---" in body
        assert "Line two" in body


# ---------------------------------------------------------------------------
# parse_frontmatter_yaml
# ---------------------------------------------------------------------------

class TestParseFrontmatterYaml:
    def test_parses_key_value_pairs(self):
        data = parse_frontmatter_yaml("agent_id: x\ntimestamp: 2026-01-01T00:00:00Z\nmessage_type: finding")
        assert data["agent_id"] == "x"

    def test_empty_yaml_returns_empty_dict(self):
        assert parse_frontmatter_yaml("") == {}

    def test_none_yaml_returns_empty_dict(self):
        # yaml.safe_load of whitespace-only returns None
        assert parse_frontmatter_yaml("   ") == {}

    def test_invalid_yaml_raises(self):
        with pytest.raises(ParseError, match="Malformed YAML"):
            parse_frontmatter_yaml("key: [unclosed")

    def test_non_mapping_yaml_raises(self):
        with pytest.raises(ParseError, match="must be a YAML mapping"):
            parse_frontmatter_yaml("- item1\n- item2")


# ---------------------------------------------------------------------------
# validate_frontmatter
# ---------------------------------------------------------------------------

class TestValidateFrontmatter:
    def test_valid_minimal(self):
        fm = validate_frontmatter({
            "agent_id": "agent-01",
            "timestamp": "2026-04-14T10:32:00Z",
            "message_type": "finding",
        })
        assert fm.agent_id == "agent-01"
        assert fm.message_type == MessageType.FINDING
        assert fm.promote == PromoteStatus.NONE
        assert fm.tags == []

    def test_valid_full(self):
        fm = validate_frontmatter({
            "agent_id": "agent-01",
            "timestamp": "2026-04-14T10:32:00Z",
            "message_type": "alert",
            "thread_id": "thread-abc",
            "tags": ["tag1", "tag2"],
            "confidence": 0.9,
            "tlp_level": "red",
            "promote": "candidate",
        })
        assert fm.tlp_level == TLPLevel.RED
        assert fm.confidence == 0.9
        assert fm.tags == ["tag1", "tag2"]

    def test_missing_agent_id_raises(self):
        with pytest.raises(ParseError, match="agent_id"):
            validate_frontmatter({
                "timestamp": "2026-04-14T10:32:00Z",
                "message_type": "finding",
            })

    def test_missing_timestamp_raises(self):
        with pytest.raises(ParseError, match="timestamp"):
            validate_frontmatter({
                "agent_id": "agent-01",
                "message_type": "finding",
            })

    def test_missing_message_type_raises(self):
        with pytest.raises(ParseError, match="message_type"):
            validate_frontmatter({
                "agent_id": "agent-01",
                "timestamp": "2026-04-14T10:32:00Z",
            })

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ParseError):
            validate_frontmatter({
                "agent_id": "a", "timestamp": "2026-01-01T00:00:00Z",
                "message_type": "finding", "confidence": 1.5,
            })

    def test_invalid_tlp_raises(self):
        with pytest.raises(ParseError):
            validate_frontmatter({
                "agent_id": "a", "timestamp": "2026-01-01T00:00:00Z",
                "message_type": "finding", "tlp_level": "purple",
            })

    def test_unknown_keys_land_in_ext(self):
        fm = validate_frontmatter({
            "agent_id": "a", "timestamp": "2026-01-01T00:00:00Z",
            "message_type": "finding",
            "custom_field": "custom_value",
            "nested": {"key": "val"},
        })
        assert fm.ext["custom_field"] == "custom_value"
        assert fm.ext["nested"] == {"key": "val"}

    def test_naive_timestamp_normalised_to_utc(self):
        fm = validate_frontmatter({
            "agent_id": "a",
            "timestamp": datetime(2026, 4, 14, 10, 0, 0),  # naive
            "message_type": "finding",
        })
        assert fm.timestamp.tzinfo == timezone.utc

    def test_tags_as_json_string(self):
        fm = validate_frontmatter({
            "agent_id": "a", "timestamp": "2026-01-01T00:00:00Z",
            "message_type": "finding", "tags": '["a", "b"]',
        })
        assert fm.tags == ["a", "b"]


# ---------------------------------------------------------------------------
# parse_message (integration)
# ---------------------------------------------------------------------------

class TestParseMessage:
    FAKE_ID = "019584a0-0000-7000-8000-000000000001"

    def test_parses_minimal_message(self):
        record = parse_message(MINIMAL_RAW, self.FAKE_ID)
        assert record.id == self.FAKE_ID
        assert record.agent_id == "osint-agent-01"
        assert record.message_type == MessageType.FINDING
        assert record.thread_id is None
        assert record.tags == []
        assert "Observed named pipe" in record.body

    def test_parses_full_message(self):
        record = parse_message(FULL_RAW, self.FAKE_ID)
        assert record.thread_id == "apt29-campaign-thread"
        assert record.confidence == pytest.approx(0.87)
        assert record.tlp_level == TLPLevel.AMBER
        assert record.promote == PromoteStatus.CANDIDATE
        assert "lateral-movement" in record.tags

    def test_ext_fields_preserved(self):
        record = parse_message(EXT_FIELDS_RAW, self.FAKE_ID)
        assert record.ext["custom_severity"] == "p1"
        assert record.ext["scanner_run_id"] == "scan-20260414-001"

    def test_raw_content_preserved_verbatim(self):
        record = parse_message(MINIMAL_RAW, self.FAKE_ID)
        assert record.raw_content == MINIMAL_RAW

    def test_ingested_at_defaults_to_now(self):
        record = parse_message(MINIMAL_RAW, self.FAKE_ID)
        assert record.ingested_at.tzinfo == timezone.utc

    def test_ingested_at_can_be_supplied(self):
        ts = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        record = parse_message(MINIMAL_RAW, self.FAKE_ID, ingested_at=ts)
        assert record.ingested_at == ts

    def test_no_frontmatter_raises(self):
        with pytest.raises(ParseError):
            parse_message("no frontmatter here", self.FAKE_ID)

    def test_to_db_row_has_required_keys(self):
        record = parse_message(MINIMAL_RAW, self.FAKE_ID)
        row = record.to_db_row()
        required = {
            "id", "agent_id", "thread_id", "message_type", "in_reply_to",
            "confidence", "tlp_level", "promote", "tags", "raw_content",
            "frontmatter", "body", "timestamp", "ingested_at", "ext",
        }
        assert required.issubset(row.keys())

    def test_to_index_row_has_required_keys(self):
        record = parse_message(MINIMAL_RAW, self.FAKE_ID)
        row = record.to_index_row(topic_db_id="topic-db-uuid")
        required = {
            "id", "topic_db_id", "agent_id", "thread_id", "message_type",
            "tags", "confidence", "tlp_level", "promote", "timestamp", "ingested_at",
        }
        assert required.issubset(row.keys())
        assert row["topic_db_id"] == "topic-db-uuid"
