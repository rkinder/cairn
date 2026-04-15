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

"""Pydantic models for the Cairn message envelope.

Every message on the blackboard is a markdown document with a YAML frontmatter
envelope.  These models cover three stages of that lifecycle:

    MessageFrontmatter  — validated, typed representation of the YAML envelope.
                          Known fields are typed; unknown fields are preserved in
                          model_extra and exposed via the .ext property.

    IncomingMessage     — what the API receives from an agent: raw YAML+markdown.

    MessageRecord       — the fully resolved row written to a topic database.
                          All frontmatter fields are flattened; the server-assigned
                          id and ingested_at are added here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerated value types
# These are the *known* valid values.  The schema uses TEXT columns so that
# agents posting non-listed values are not rejected at the DB layer — but the
# API will validate against these enums before accepting a message.
# ---------------------------------------------------------------------------

class MessageType(str, Enum):
    FINDING         = "finding"
    HYPOTHESIS      = "hypothesis"
    QUERY           = "query"
    RESPONSE        = "response"
    ALERT           = "alert"
    METHODOLOGY_REF = "methodology_ref"


class TLPLevel(str, Enum):
    WHITE = "white"
    GREEN = "green"
    AMBER = "amber"
    RED   = "red"


class PromoteStatus(str, Enum):
    NONE      = "none"
    CANDIDATE = "candidate"
    PROMOTED  = "promoted"
    REJECTED  = "rejected"


# ---------------------------------------------------------------------------
# MessageFrontmatter
# Represents the YAML envelope of a posted message.  Unknown keys are
# preserved in model_extra (accessible via .ext) rather than rejected,
# which is the primary extensibility mechanism for new agent types that
# add domain-specific envelope fields before they are standardised.
# ---------------------------------------------------------------------------

class MessageFrontmatter(BaseModel):
    model_config = ConfigDict(
        extra="allow",          # unknown keys land in model_extra → .ext
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    # Required fields — the ingest pipeline rejects messages missing these.
    agent_id:     str
    timestamp:    datetime
    message_type: MessageType

    # Optional envelope fields
    thread_id:   str | None = None
    in_reply_to: str | None = None   # message ID this is replying to
    tags:        list[str]  = Field(default_factory=list)
    confidence:  float | None = Field(None, ge=0.0, le=1.0)
    tlp_level:   TLPLevel | None = None
    promote:     PromoteStatus = PromoteStatus.NONE

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalise_timestamp(cls, v: Any) -> Any:
        """Accept naive datetimes and treat them as UTC."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @field_validator("tags", mode="before")
    @classmethod
    def coerce_tags(cls, v: Any) -> Any:
        """Accept a JSON string as well as an actual list."""
        if isinstance(v, str):
            return json.loads(v)
        return v

    @property
    def ext(self) -> dict[str, Any]:
        """Extra frontmatter fields not in the standard envelope.

        These are preserved verbatim and stored in the messages.ext JSON
        column so that downstream consumers can access them without schema
        changes.
        """
        return dict(self.model_extra or {})

    def to_db_row(self) -> dict[str, Any]:
        """Return a flat dict suitable for insertion into a messages table row.

        Known envelope fields are returned under their column names.
        The timestamp is serialised to an ISO8601 string.
        tags and ext are serialised to JSON strings.
        """
        return {
            "agent_id":     self.agent_id,
            "thread_id":    self.thread_id,
            "message_type": self.message_type.value,
            "in_reply_to":  self.in_reply_to,
            "confidence":   self.confidence,
            "tlp_level":    self.tlp_level.value if self.tlp_level else None,
            "promote":      self.promote.value,
            "tags":         json.dumps(self.tags),
            "timestamp":    self.timestamp.isoformat(),
            "ext":          json.dumps(self.ext),
        }


# ---------------------------------------------------------------------------
# IncomingMessage
# The payload the API receives from an agent.  At this stage it is just the
# raw YAML+markdown string.  The ingest pipeline parses it into a
# MessageFrontmatter and a body, then builds a MessageRecord.
# ---------------------------------------------------------------------------

class IncomingMessage(BaseModel):
    # Do NOT strip whitespace — raw_content must be preserved exactly as posted.
    model_config = ConfigDict()

    raw_content: str = Field(
        ...,
        description="Full YAML frontmatter + markdown body, exactly as the agent composed it.",
    )

    @field_validator("raw_content")
    @classmethod
    def must_have_frontmatter(cls, v: str) -> str:
        if not v.lstrip().startswith("---"):
            raise ValueError(
                "Message must begin with a YAML frontmatter block (starting with '---')."
            )
        return v


# ---------------------------------------------------------------------------
# MessageRecord
# The fully resolved row that is written to the topic database.
# Built by the ingest pipeline after parsing and validating an IncomingMessage.
# ---------------------------------------------------------------------------

class MessageRecord(BaseModel):
    # Do NOT strip whitespace globally — raw_content and body must be preserved exactly.
    model_config = ConfigDict()

    # Server-assigned fields
    id:          str       # UUID v7
    ingested_at: datetime  # UTC, server-set on receipt

    # From the parsed frontmatter
    agent_id:     str
    thread_id:    str | None
    message_type: MessageType
    in_reply_to:  str | None
    confidence:   float | None
    tlp_level:    TLPLevel | None
    promote:      PromoteStatus
    tags:         list[str]
    timestamp:    datetime

    # Full artifact
    raw_content: str   # original YAML+markdown, preserved verbatim
    frontmatter: dict[str, Any]  # complete parsed frontmatter as a dict
    body:        str   # markdown body, frontmatter stripped

    # Extension fields from frontmatter not in the standard envelope
    ext: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def ensure_utc(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for field in ("ingested_at", "timestamp"):
                v = data.get(field)
                if isinstance(v, datetime) and v.tzinfo is None:
                    data[field] = v.replace(tzinfo=timezone.utc)
        return data

    def to_db_row(self) -> dict[str, Any]:
        """Flat dict for insertion into a messages table row."""
        return {
            "id":           self.id,
            "agent_id":     self.agent_id,
            "thread_id":    self.thread_id,
            "message_type": self.message_type.value,
            "in_reply_to":  self.in_reply_to,
            "confidence":   self.confidence,
            "tlp_level":    self.tlp_level.value if self.tlp_level else None,
            "promote":      self.promote.value,
            "tags":         json.dumps(self.tags),
            "raw_content":  self.raw_content,
            "frontmatter":  json.dumps(self.frontmatter),
            "body":         self.body,
            "timestamp":    self.timestamp.isoformat(),
            "ingested_at":  self.ingested_at.isoformat(),
            "ext":          json.dumps(self.ext),
        }

    def to_index_row(self, topic_db_id: str) -> dict[str, Any]:
        """Flat dict for insertion into message_index in index.db."""
        return {
            "id":           self.id,
            "topic_db_id":  topic_db_id,
            "agent_id":     self.agent_id,
            "thread_id":    self.thread_id,
            "message_type": self.message_type.value,
            "tags":         json.dumps(self.tags),
            "confidence":   self.confidence,
            "tlp_level":    self.tlp_level.value if self.tlp_level else None,
            "promote":      self.promote.value,
            "timestamp":    self.timestamp.isoformat(),
            "ingested_at":  self.ingested_at.isoformat(),
        }
