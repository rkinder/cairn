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

"""Shared pytest fixtures available to all test modules.

These fixtures use function scope so they are compatible with pytest-asyncio's
asyncio_mode = "auto" setting (which creates a fresh event loop per test
function).  Module-scoped fixtures in individual test files run on a different
loop and must NOT be imported across module boundaries — doing so causes tests
to hang indefinitely because the fixture awaits on an orphaned loop that the
driving test's loop never runs.
"""

import pytest_asyncio

from cairn.db.init import init_all
from cairn.api.auth import hash_api_key


@pytest_asyncio.fixture
async def data_dir(tmp_path_factory) -> object:
    """Initialise a fresh Cairn data directory for a single test."""
    d = tmp_path_factory.mktemp("cairn_data")
    await init_all(d)
    return d


@pytest_asyncio.fixture
async def agent_key_pair(data_dir) -> tuple[str, str]:
    """Insert a test agent into index.db and return (agent_id, raw_api_key)."""
    import aiosqlite
    from datetime import datetime, timezone

    agent_id = "test-agent-01"
    raw_key  = "cairn_testkey_abc123"
    key_hash = hash_api_key(raw_key)
    now      = datetime.now(tz=timezone.utc).isoformat()

    async with aiosqlite.connect(data_dir / "index.db") as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO agents
                (id, display_name, description, api_key_hash,
                 capabilities, allowed_dbs, is_active, created_at, ext)
            VALUES (?, ?, '', ?, '[]', '[]', 1, ?, '{}')
            """,
            (agent_id, "Test Agent", key_hash, now),
        )
        await db.commit()
    return agent_id, raw_key
