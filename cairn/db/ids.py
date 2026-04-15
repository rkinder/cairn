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
