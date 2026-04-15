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

"""Database initialization — create databases from SQL schema files.

Usage:
    from cairn.db.init import init_all, init_db

    # Create all databases under a given data directory:
    await init_all(Path("./data"))

    # Create (or open and verify) a single database:
    await init_db(Path("./data/index.db"), "index")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from cairn.db.ids import new_id

logger = logging.getLogger(__name__)

# Maps domain slug → schema filename.
# Add new topic databases here as they are introduced.
SCHEMA_FILES: dict[str, str] = {
    "index": "index.sql",
    "osint": "osint.sql",
    "vulnerabilities": "vulnerabilities.sql",
}

# Human-readable metadata for auto-registration.
# Extend when adding new topic databases.
_TOPIC_METADATA: dict[str, dict] = {
    "osint": {
        "display_name": "OSINT",
        "description": "Open-source intelligence: entities, relationships, and sources.",
        "domain_tags": '["threat-intel", "ioc", "osint"]',
    },
    "vulnerabilities": {
        "display_name": "Vulnerabilities",
        "description": "CVEs, affected systems, asset exposure, and remediation tracking.",
        "domain_tags": '["vulnerability", "cve", "remediation"]',
    },
}

_SCHEMA_DIR = Path(__file__).parent / "schema"


async def init_db(db_path: Path, domain: str) -> None:
    """Create a database from its schema file if it does not already exist.

    If the database file exists, verifies that _schema_meta is present and
    logs the current schema version. Does not run migrations — that is the
    responsibility of the migration runner (cairn/db/migrations/).

    Args:
        db_path: Absolute or relative path to the .db file.
        domain:  Domain slug (key in SCHEMA_FILES), e.g. 'osint'.

    Raises:
        KeyError: If domain is not registered in SCHEMA_FILES.
        FileNotFoundError: If the schema SQL file is missing.
    """
    schema_file = _SCHEMA_DIR / SCHEMA_FILES[domain]
    if not schema_file.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_file}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    already_exists = db_path.exists()

    async with aiosqlite.connect(db_path) as db:
        if not already_exists:
            logger.info("Creating %s database at %s", domain, db_path)
            ddl = schema_file.read_text(encoding="utf-8")
            await db.executescript(ddl)
            await db.commit()
        else:
            cursor = await db.execute(
                "SELECT value FROM _schema_meta WHERE key = 'schema_version'"
            )
            row = await cursor.fetchone()
            version = row[0] if row else "unknown"
            logger.info(
                "Opened existing %s database (schema_version=%s) at %s",
                domain,
                version,
                db_path,
            )


async def _register_topic_dbs(index_path: Path, data_dir: Path) -> None:
    """Ensure all topic databases are registered in index.db's topic_databases table.

    Uses INSERT OR IGNORE so existing registrations are never overwritten.
    Called by init_all() after all DB files are created.

    The db_path stored in the registry is the bare filename (e.g. 'osint.db'),
    resolved relative to data_dir at runtime.  This keeps the registry
    portable if the data directory is moved.
    """
    now = datetime.now(tz=timezone.utc).isoformat()

    async with aiosqlite.connect(index_path) as idx:
        for slug, meta in _TOPIC_METADATA.items():
            await idx.execute(
                """
                INSERT OR IGNORE INTO topic_databases
                    (id, name, display_name, description, db_path,
                     schema_version, domain_tags, is_active, created_at, updated_at, ext)
                VALUES
                    (:id, :name, :display_name, :description, :db_path,
                     1, :domain_tags, 1, :now, :now, '{}')
                """,
                {
                    "id":           new_id(),
                    "name":         slug,
                    "display_name": meta["display_name"],
                    "description":  meta["description"],
                    "db_path":      f"{slug}.db",
                    "domain_tags":  meta["domain_tags"],
                    "now":          now,
                },
            )
        await idx.commit()
        logger.info("Topic database registry up to date in index.db")


async def init_all(data_dir: Path) -> dict[str, Path]:
    """Initialize all registered databases under data_dir.

    Creates the data directory if it does not exist.  Returns a mapping
    of domain slug → absolute database path for use by the connection pool.

    Steps:
      1. Create index.db first (other registrations depend on it).
      2. Create each topic database file.
      3. Register all topic databases in index.db's topic_databases table
         (INSERT OR IGNORE — safe to call on every startup).

    Args:
        data_dir: Directory where .db files will be created.

    Returns:
        Dict mapping domain slug to resolved Path, e.g.:
        {'index': Path('/data/index.db'), 'osint': Path('/data/osint.db'), ...}
    """
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    # index.db must be created first so _register_topic_dbs can write to it.
    index_path = data_dir / "index.db"
    await init_db(index_path, "index")

    paths: dict[str, Path] = {"index": index_path}
    for domain in SCHEMA_FILES:
        if domain == "index":
            continue
        db_path = data_dir / f"{domain}.db"
        await init_db(db_path, domain)
        paths[domain] = db_path

    await _register_topic_dbs(index_path, data_dir)

    return paths
