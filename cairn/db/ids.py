"""UUID v7 generation for Cairn database primary keys.

UUID v7 is time-ordered (48-bit millisecond timestamp prefix), which means:
  - Rows inserted later sort after rows inserted earlier with a simple
    lexicographic comparison on the id column.
  - No coordination between hosts is required.
  - Direct drop-in replacement for UUID v4 when migrating to PostgreSQL.

Usage:
    from cairn.db.ids import new_id
    message_id = new_id()
"""

from uuid_extensions import uuid7


def new_id() -> str:
    """Return a new UUID v7 as a lowercase hyphenated string."""
    return str(uuid7())
