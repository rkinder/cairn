"""API key authentication for the Cairn blackboard.

Every agent and human client presents a Bearer token.  The token is looked up
in the agents table in index.db; the stored value is a bcrypt hash.

Agents are identified by the id column in the agents table.  The agent_id
field in the message frontmatter must match this id — the ingest pipeline
enforces that an agent cannot impersonate another.

Usage:
    key_hash = hash_api_key("raw-key")          # when provisioning an agent
    agent = await authenticate(token, db)        # in a request handler
"""

from __future__ import annotations

import bcrypt

import aiosqlite

# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def hash_api_key(raw_key: str) -> str:
    """Return a bcrypt hash of raw_key suitable for storing in the DB."""
    return bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()


def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    """Return True if raw_key matches stored_hash."""
    return bcrypt.checkpw(raw_key.encode(), stored_hash.encode())


# ---------------------------------------------------------------------------
# Database lookup
# ---------------------------------------------------------------------------

async def lookup_agent(token: str, conn: aiosqlite.Connection) -> dict | None:
    """Look up an agent by API key token.

    Returns the agent row as a dict if the token is valid and the agent is
    active, or None if authentication fails.

    The bcrypt check is deliberately performed even when no matching hash
    exists (dummy check) to prevent timing-based agent enumeration.
    """
    cursor = await conn.execute(
        "SELECT id, display_name, api_key_hash, capabilities, allowed_dbs, is_active "
        "FROM agents"
    )
    rows = await cursor.fetchall()

    # Scan all rows and check each hash.  The table is expected to be small
    # (tens of agents at most), so a full scan is fine.  If it grows large,
    # add a hashed lookup index.
    for row in rows:
        if row["is_active"] and verify_api_key(token, row["api_key_hash"]):
            return dict(row)

    # Perform a dummy bcrypt check to keep response time constant whether
    # zero or many agents are registered.
    _dummy_hash = "$2b$12$KIXoWmFkZmFkZmFkZmFkZuVJ8bFp6RhQfkzFkZmFkZmFkZmFkZm"
    bcrypt.checkpw(b"dummy", _dummy_hash.encode() if isinstance(_dummy_hash, str) else _dummy_hash)
    return None
