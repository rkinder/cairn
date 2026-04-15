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

"""YAML frontmatter + markdown message composer.

The inverse of cairn.ingest.parser: takes structured kwargs and produces
the raw YAML+markdown string that POST /messages expects.

Usage:
    from cairn.skill.composer import compose_message

    raw = compose_message(
        agent_id="osint-agent-01",
        message_type="finding",
        body="Observed named pipe consistent with Cobalt Strike.",
        thread_id="apt29-thread",
        tags=["apt29", "lateral-movement"],
        confidence=0.87,
        tlp_level="amber",
    )
    # raw is a string starting with '---\n...'
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import yaml


# Fields written in this order in the frontmatter (known fields first,
# extension fields appended after).
_KNOWN_FIELD_ORDER = [
    "agent_id",
    "timestamp",
    "message_type",
    "thread_id",
    "in_reply_to",
    "tags",
    "confidence",
    "tlp_level",
    "promote",
]


def compose_message(
    *,
    agent_id: str,
    message_type: str,
    body: str,
    timestamp: datetime | str | None = None,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
    tags: list[str] | None = None,
    confidence: float | None = None,
    tlp_level: str | None = None,
    promote: str = "none",
    **extra_frontmatter: Any,
) -> str:
    """Return a YAML frontmatter + markdown body string.

    Args:
        agent_id:         Agent ID as registered in index.db.
        message_type:     One of the MessageType enum values.
        body:             Markdown body text.
        timestamp:        Agent-supplied datetime (defaults to utcnow).
        thread_id:        Optional thread identifier.
        in_reply_to:      Optional message ID this replies to.
        tags:             List of tag strings.
        confidence:       Float 0.0–1.0.
        tlp_level:        'white' | 'green' | 'amber' | 'red'.
        promote:          PromoteStatus value (default 'none').
        **extra_frontmatter: Any additional fields to include in the envelope.
                             These land in the server's ext column.

    Returns:
        A string of the form::

            ---
            agent_id: osint-agent-01
            timestamp: 2026-04-14T10:32:00+00:00
            message_type: finding
            ...
            ---

            Markdown body here.
    """
    if timestamp is None:
        timestamp = datetime.now(tz=timezone.utc)

    if isinstance(timestamp, datetime):
        ts_str = timestamp.isoformat()
    else:
        ts_str = str(timestamp)

    # Build an ordered dict with known fields first.
    fm: dict[str, Any] = {"agent_id": agent_id, "timestamp": ts_str, "message_type": message_type}

    if thread_id is not None:
        fm["thread_id"] = thread_id
    if in_reply_to is not None:
        fm["in_reply_to"] = in_reply_to
    if tags:
        fm["tags"] = list(tags)
    if confidence is not None:
        fm["confidence"] = round(float(confidence), 4)
    if tlp_level is not None:
        fm["tlp_level"] = tlp_level
    if promote != "none":
        fm["promote"] = promote

    # Append extension fields after known fields.
    fm.update(extra_frontmatter)

    # Serialise to YAML.  sort_keys=False preserves insertion order.
    # default_flow_style=False forces block style for readability.
    frontmatter_yaml = yaml.dump(
        fm,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).rstrip("\n")

    # Ensure body starts on a new line after the closing delimiter.
    body_text = body if body.startswith("\n") else "\n" + body

    return f"---\n{frontmatter_yaml}\n---\n{body_text}"
