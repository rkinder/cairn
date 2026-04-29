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

"""Phase 4.2 — IT domain database initialisation tests.

Validates that topic_common.sql and the extended init.py correctly create and
register all five new topic databases without touching existing ones.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from cairn.db.init import SCHEMA_FILES, _TOPIC_METADATA, init_all, init_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IT_SLUGS = ["aws", "azure", "networking", "systems", "pam"]
ALL_TOPIC_SLUGS = ["osint", "vulnerabilities"] + IT_SLUGS


def run(coro):
    """Run a coroutine synchronously (compatible with both pytest-asyncio and plain)."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# SCHEMA_FILES and _TOPIC_METADATA registration
# ---------------------------------------------------------------------------


def test_all_it_slugs_in_schema_files():
    """All five IT slugs are registered in SCHEMA_FILES."""
    for slug in IT_SLUGS:
        assert slug in SCHEMA_FILES, f"Missing slug in SCHEMA_FILES: {slug}"


def test_it_slugs_use_topic_common_sql():
    """IT slugs all point to topic_common.sql."""
    for slug in IT_SLUGS:
        assert SCHEMA_FILES[slug] == "topic_common.sql", (
            f"{slug} should use topic_common.sql, got {SCHEMA_FILES[slug]}"
        )


def test_all_it_slugs_in_topic_metadata():
    """All five IT slugs are registered in _TOPIC_METADATA."""
    for slug in IT_SLUGS:
        assert slug in _TOPIC_METADATA, f"Missing slug in _TOPIC_METADATA: {slug}"


def test_topic_metadata_has_required_keys():
    """Every _TOPIC_METADATA entry has display_name, description, domain_tags."""
    for slug in ALL_TOPIC_SLUGS:
        meta = _TOPIC_METADATA[slug]
        for key in ("display_name", "description", "domain_tags"):
            assert key in meta, f"_TOPIC_METADATA['{slug}'] missing '{key}'"


# ---------------------------------------------------------------------------
# topic_common.sql DDL validity
# ---------------------------------------------------------------------------


def test_topic_common_sql_is_valid_ddl(tmp_path: Path):
    """topic_common.sql can be applied to a blank SQLite database."""
    from cairn.db.init import _SCHEMA_DIR

    schema_file = _SCHEMA_DIR / "topic_common.sql"
    assert schema_file.exists(), "topic_common.sql not found"
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(schema_file.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    assert db_path.exists()


def test_messages_table_created_by_topic_common(tmp_path: Path):
    """topic_common.sql creates a messages table."""
    from cairn.db.init import _SCHEMA_DIR

    schema_file = _SCHEMA_DIR / "topic_common.sql"
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_file.read_text(encoding="utf-8"))
    conn.commit()
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    assert cur.fetchone() is not None, "messages table not created"
    conn.close()


def test_schema_meta_table_created_by_topic_common(tmp_path: Path):
    """topic_common.sql creates a _schema_meta table."""
    from cairn.db.init import _SCHEMA_DIR

    schema_file = _SCHEMA_DIR / "topic_common.sql"
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_file.read_text(encoding="utf-8"))
    conn.commit()
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_meta'"
    )
    assert cur.fetchone() is not None, "_schema_meta table not created"
    conn.close()


def test_topic_common_has_no_hardcoded_inserts(tmp_path: Path):
    """topic_common.sql does not INSERT domain-specific _schema_meta rows."""
    from cairn.db.init import _SCHEMA_DIR

    schema_file = _SCHEMA_DIR / "topic_common.sql"
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_file.read_text(encoding="utf-8"))
    conn.commit()
    cur = conn.execute("SELECT COUNT(*) FROM _schema_meta")
    count = cur.fetchone()[0]
    assert count == 0, (
        f"topic_common.sql should not INSERT any rows; found {count}"
    )
    conn.close()


def test_messages_table_columns_match_osint(tmp_path: Path):
    """Columns in topic_common.sql messages table match osint.sql."""
    from cairn.db.init import _SCHEMA_DIR

    expected_columns = {
        "id", "agent_id", "thread_id", "message_type", "in_reply_to",
        "confidence", "tlp_level", "promote", "tags", "raw_content",
        "frontmatter", "body", "timestamp", "ingested_at",
        "deleted_at", "deleted_by", "ext",
    }
    schema_file = _SCHEMA_DIR / "topic_common.sql"
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_file.read_text(encoding="utf-8"))
    conn.commit()
    cur = conn.execute("PRAGMA table_info(messages)")
    actual_columns = {row[1] for row in cur.fetchall()}
    assert actual_columns == expected_columns, (
        f"Column mismatch. Missing: {expected_columns - actual_columns}, "
        f"Extra: {actual_columns - expected_columns}"
    )
    conn.close()


# ---------------------------------------------------------------------------
# init_all — creates all databases
# ---------------------------------------------------------------------------


def test_init_all_creates_five_new_databases(tmp_path: Path):
    """init_all() creates aws.db, azure.db, networking.db, systems.db, pam.db."""
    run(init_all(tmp_path))
    for slug in IT_SLUGS:
        db_file = tmp_path / f"{slug}.db"
        assert db_file.exists(), f"{slug}.db not created by init_all()"


def test_init_all_creates_all_seven_topic_databases(tmp_path: Path):
    """init_all() creates all seven topic databases."""
    run(init_all(tmp_path))
    for slug in ALL_TOPIC_SLUGS:
        db_file = tmp_path / f"{slug}.db"
        assert db_file.exists(), f"{slug}.db not created by init_all()"


def test_new_databases_contain_messages_table(tmp_path: Path):
    """Each IT .db file has a messages table with the correct columns."""
    run(init_all(tmp_path))
    for slug in IT_SLUGS:
        db_path = tmp_path / f"{slug}.db"
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        assert cur.fetchone() is not None, f"messages table missing in {slug}.db"
        conn.close()


def test_schema_meta_domain_row_inserted(tmp_path: Path):
    """_schema_meta contains ('domain', slug) for each new IT database."""
    run(init_all(tmp_path))
    for slug in IT_SLUGS:
        db_path = tmp_path / f"{slug}.db"
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute(
            "SELECT value FROM _schema_meta WHERE key = 'domain'"
        )
        row = cur.fetchone()
        assert row is not None, f"No domain row in _schema_meta for {slug}.db"
        assert row[0] == slug, f"Expected domain={slug}, got {row[0]}"
        conn.close()


def test_schema_meta_schema_version_row_inserted(tmp_path: Path):
    """_schema_meta contains ('schema_version', '1') for each new IT database."""
    run(init_all(tmp_path))
    for slug in IT_SLUGS:
        db_path = tmp_path / f"{slug}.db"
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute(
            "SELECT value FROM _schema_meta WHERE key = 'schema_version'"
        )
        row = cur.fetchone()
        assert row is not None, f"No schema_version in _schema_meta for {slug}.db"
        assert row[0] == "1", f"Expected schema_version=1 for {slug}.db, got {row[0]}"
        conn.close()


def test_init_all_is_idempotent(tmp_path: Path):
    """Calling init_all() twice raises no errors and creates no duplicate rows."""
    run(init_all(tmp_path))
    run(init_all(tmp_path))  # second call must be a no-op, not an error
    # Verify _schema_meta has exactly 2 rows (schema_version + domain) per IT db
    for slug in IT_SLUGS:
        db_path = tmp_path / f"{slug}.db"
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute("SELECT COUNT(*) FROM _schema_meta")
        count = cur.fetchone()[0]
        assert count == 2, (
            f"Expected 2 _schema_meta rows in {slug}.db after 2 inits, got {count}"
        )
        conn.close()


def test_existing_osint_db_untouched(tmp_path: Path):
    """osint.db schema_version is still stable after init_all() runs twice."""
    run(init_all(tmp_path))
    run(init_all(tmp_path))
    conn = sqlite3.connect(str(tmp_path / "osint.db"))
    cur = conn.execute("SELECT value FROM _schema_meta WHERE key = 'schema_version'")
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "2"
    conn.close()


def test_new_dbs_registered_in_index(tmp_path: Path):
    """All five IT slugs appear in index.db's topic_databases table."""
    run(init_all(tmp_path))
    conn = sqlite3.connect(str(tmp_path / "index.db"))
    cur = conn.execute("SELECT name FROM topic_databases")
    registered = {row[0] for row in cur.fetchall()}
    conn.close()
    for slug in IT_SLUGS:
        assert slug in registered, f"{slug} not registered in index.db topic_databases"
