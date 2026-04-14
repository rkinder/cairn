"""YAML frontmatter parser for Cairn messages.

Responsibilities:
  - Split raw YAML+markdown content into a frontmatter dict and a body string.
  - Validate the frontmatter against MessageFrontmatter.
  - Construct a MessageRecord ready for database insertion.

The only entry point callers need is parse_message().

Frontmatter format (same as Obsidian):

    ---
    agent_id: osint-agent-01
    timestamp: 2026-04-14T10:32:00Z
    message_type: finding
    tags: [lateral-movement, apt29]
    confidence: 0.87
    ---

    Markdown body follows here.

Rules:
  - The document must begin with '---' (after optional leading whitespace).
  - The opening '---' must be followed by a closing '---' on its own line.
  - Everything after the closing '---' is the body (leading newline stripped).
  - YAML inside the frontmatter block is parsed with safe_load.
  - Unknown YAML keys are preserved in MessageFrontmatter.ext, not rejected.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import yaml
from pydantic import ValidationError

from cairn.models.message import (
    IncomingMessage,
    MessageFrontmatter,
    MessageRecord,
    PromoteStatus,
    TLPLevel,
)

# Matches the YAML frontmatter block at the start of a document.
# Group 1 → raw YAML text between the delimiters.
# Group 2 → everything after the closing '---' (the body).
_FRONTMATTER_RE = re.compile(
    r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)",
    re.DOTALL,
)


class ParseError(ValueError):
    """Raised when a message cannot be parsed or fails validation."""


def split_frontmatter(raw_content: str) -> tuple[str, str]:
    """Split raw YAML+markdown into (frontmatter_text, body_text).

    Args:
        raw_content: Full message string as posted by the agent.

    Returns:
        Tuple of (frontmatter_yaml_string, body_markdown_string).

    Raises:
        ParseError: If the document does not contain a valid frontmatter block.
    """
    match = _FRONTMATTER_RE.match(raw_content)
    if not match:
        raise ParseError(
            "Message must begin with a YAML frontmatter block delimited by '---'. "
            "Ensure the opening '---' is at the start of the document and the "
            "closing '---' appears on its own line."
        )
    frontmatter_text = match.group(1)
    body = match.group(2).lstrip("\n")
    return frontmatter_text, body


def parse_frontmatter_yaml(frontmatter_text: str) -> dict[str, Any]:
    """Parse the YAML text inside the frontmatter delimiters.

    Args:
        frontmatter_text: Raw YAML string (without the '---' delimiters).

    Returns:
        Dict of parsed YAML values.

    Raises:
        ParseError: If the YAML is malformed.
    """
    try:
        data = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise ParseError(f"Malformed YAML in frontmatter: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ParseError(
            f"Frontmatter must be a YAML mapping, got {type(data).__name__}."
        )
    return data


def validate_frontmatter(data: dict[str, Any]) -> MessageFrontmatter:
    """Validate a parsed frontmatter dict against the MessageFrontmatter schema.

    Args:
        data: Dict from parse_frontmatter_yaml().

    Returns:
        Validated MessageFrontmatter instance.

    Raises:
        ParseError: Wraps Pydantic ValidationError with a readable message.
    """
    try:
        return MessageFrontmatter.model_validate(data)
    except ValidationError as exc:
        # Flatten Pydantic errors into a single readable string.
        problems = "; ".join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        )
        raise ParseError(f"Frontmatter validation failed — {problems}") from exc


def build_record(
    *,
    message_id: str,
    frontmatter: MessageFrontmatter,
    raw_content: str,
    body: str,
    ingested_at: datetime | None = None,
) -> MessageRecord:
    """Assemble a MessageRecord from its constituent parts.

    Args:
        message_id:  Server-assigned UUID v7.
        frontmatter: Validated MessageFrontmatter.
        raw_content: Original full YAML+markdown string.
        body:        Markdown body with frontmatter stripped.
        ingested_at: Server timestamp; defaults to utcnow() if not provided.

    Returns:
        MessageRecord ready for to_db_row() / to_index_row().
    """
    if ingested_at is None:
        ingested_at = datetime.now(tz=timezone.utc)

    return MessageRecord(
        id=message_id,
        ingested_at=ingested_at,
        agent_id=frontmatter.agent_id,
        thread_id=frontmatter.thread_id,
        message_type=frontmatter.message_type,
        in_reply_to=frontmatter.in_reply_to,
        confidence=frontmatter.confidence,
        tlp_level=frontmatter.tlp_level,
        promote=frontmatter.promote,
        tags=frontmatter.tags,
        timestamp=frontmatter.timestamp,
        raw_content=raw_content,
        # Store the complete frontmatter dict (including ext fields) so that
        # consumers can reconstruct the original envelope from the DB row.
        # mode="json" ensures all values are JSON-serializable (e.g. datetime → str).
        frontmatter=frontmatter.model_dump(mode="json"),
        body=body,
        ext=frontmatter.ext,
    )


def parse_message(
    raw_content: str,
    message_id: str,
    ingested_at: datetime | None = None,
) -> MessageRecord:
    """Parse and validate a raw YAML+markdown message.

    This is the main entry point for the ingest pipeline.  It performs all
    steps in sequence: structural split → YAML parse → schema validation →
    record assembly.

    Args:
        raw_content: Full message string as posted by an agent.
        message_id:  UUID v7 assigned by the server before calling this.
        ingested_at: Server timestamp; defaults to utcnow() if not provided.

    Returns:
        MessageRecord ready for database insertion.

    Raises:
        ParseError: If any parsing or validation step fails.  The error
                    message is safe to return to the agent as an API error.
    """
    # Validate that raw_content at least starts with '---' before doing work.
    try:
        IncomingMessage(raw_content=raw_content)
    except Exception as exc:
        raise ParseError(str(exc)) from exc

    frontmatter_text, body = split_frontmatter(raw_content)
    data = parse_frontmatter_yaml(frontmatter_text)
    frontmatter = validate_frontmatter(data)

    return build_record(
        message_id=message_id,
        frontmatter=frontmatter,
        raw_content=raw_content,
        body=body,
        ingested_at=ingested_at,
    )
