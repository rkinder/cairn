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
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

# Maps domain slug → schema filename.
# Add new topic databases here as they are introduced.
SCHEMA_FILES: dict[str, str] = {
    "index": "index.sql",
    "osint": "osint.sql",
    "vulnerabilities": "vulnerabilities.sql",
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
            # Verify the schema metadata table is present.
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


async def init_all(data_dir: Path) -> dict[str, Path]:
    """Initialize all registered databases under data_dir.

    Creates the data directory if it does not exist. Returns a mapping
    of domain slug → absolute database path for use by the connection pool.

    Args:
        data_dir: Directory where .db files will be created.

    Returns:
        Dict mapping domain slug to resolved Path, e.g.:
        {'index': Path('/data/index.db'), 'osint': Path('/data/osint.db'), ...}
    """
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}
    for domain in SCHEMA_FILES:
        db_path = data_dir / f"{domain}.db"
        await init_db(db_path, domain)
        paths[domain] = db_path

    return paths
