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

"""cairn-admin — provisioning CLI for the Cairn blackboard.

Manages agents and topic database registrations directly against index.db,
without requiring the API server to be running.

Usage:
    cairn-admin agent create --id osint-agent-01 --name "OSINT Agent"
    cairn-admin agent list
    cairn-admin agent deactivate osint-agent-01
    cairn-admin agent rotate-key osint-agent-01

    cairn-admin db list
    cairn-admin db register --name network --display-name "Network" --path network.db

The data directory is read from CAIRN_DATA_DIR (default: ./data).
index.db must already exist (run the server once, or cairn-admin init-db, first).

API keys are only shown once at creation or rotation time.
Store them securely — there is no way to retrieve them later.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import bcrypt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    raw = os.environ.get("CAIRN_DATA_DIR", "./data")
    return Path(raw).resolve()


def _index_db() -> Path:
    return _data_dir() / "index.db"


def _open_index() -> sqlite3.Connection:
    path = _index_db()
    if not path.exists():
        print(
            f"[error] index.db not found at {path}\n"
            "Run the server once (or 'cairn-admin init-db') to create it.",
            file=sys.stderr,
        )
        sys.exit(1)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    # Import here to avoid pulling the full package at CLI startup.
    from cairn.db.ids import new_id
    return new_id()


def _generate_api_key() -> tuple[str, str]:
    """Return (raw_key, bcrypt_hash).  The raw key is shown once and discarded."""
    raw = "cairn_" + secrets.token_urlsafe(32)
    hashed = bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()
    return raw, hashed


def _hash_existing_key(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


def _print_table(headers: list[str], rows: list[tuple]) -> None:
    if not rows:
        print("  (none)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


# ---------------------------------------------------------------------------
# agent subcommands
# ---------------------------------------------------------------------------

def agent_create(args: argparse.Namespace) -> None:
    conn = _open_index()

    # Check for duplicate id.
    existing = conn.execute("SELECT id FROM agents WHERE id = ?", (args.id,)).fetchone()
    if existing:
        print(f"[error] Agent '{args.id}' already exists.", file=sys.stderr)
        sys.exit(1)

    raw_key, key_hash = _generate_api_key()
    capabilities = json.dumps([c.strip() for c in args.capabilities.split(",") if c.strip()])
    allowed_dbs  = json.dumps([d.strip() for d in args.allowed_dbs.split(",") if d.strip()])
    now = _now()

    conn.execute(
        """
        INSERT INTO agents
            (id, display_name, description, api_key_hash,
             capabilities, allowed_dbs, is_active, created_at, ext)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, '{}')
        """,
        (
            args.id,
            args.name,
            args.description or "",
            key_hash,
            capabilities,
            allowed_dbs,
            now,
        ),
    )
    conn.commit()
    conn.close()

    print(f"\n  Agent created: {args.id}")
    print(f"  Display name:  {args.name}")
    print(f"  Capabilities:  {capabilities}")
    print(f"  Allowed DBs:   {allowed_dbs or '(all)'}")
    print()
    print("  API key (shown once — store this securely):")
    print(f"\n    {raw_key}\n")


def agent_list(args: argparse.Namespace) -> None:
    conn = _open_index()
    rows = conn.execute(
        "SELECT id, display_name, capabilities, allowed_dbs, is_active, created_at, last_seen_at "
        "FROM agents ORDER BY created_at"
    ).fetchall()
    conn.close()

    print()
    _print_table(
        ["ID", "Name", "Capabilities", "Allowed DBs", "Active", "Created", "Last seen"],
        [
            (
                r["id"],
                r["display_name"],
                r["capabilities"],
                r["allowed_dbs"] or "(all)",
                "yes" if r["is_active"] else "no",
                r["created_at"][:19],
                (r["last_seen_at"] or "never")[:19],
            )
            for r in rows
        ],
    )
    print()


def agent_deactivate(args: argparse.Namespace) -> None:
    conn = _open_index()
    cursor = conn.execute(
        "UPDATE agents SET is_active = 0 WHERE id = ?", (args.id,)
    )
    if cursor.rowcount == 0:
        print(f"[error] Agent '{args.id}' not found.", file=sys.stderr)
        conn.close()
        sys.exit(1)
    conn.commit()
    conn.close()
    print(f"  Agent '{args.id}' deactivated.")


def agent_activate(args: argparse.Namespace) -> None:
    conn = _open_index()
    cursor = conn.execute(
        "UPDATE agents SET is_active = 1 WHERE id = ?", (args.id,)
    )
    if cursor.rowcount == 0:
        print(f"[error] Agent '{args.id}' not found.", file=sys.stderr)
        conn.close()
        sys.exit(1)
    conn.commit()
    conn.close()
    print(f"  Agent '{args.id}' activated.")


def agent_rotate_key(args: argparse.Namespace) -> None:
    conn = _open_index()
    existing = conn.execute(
        "SELECT id FROM agents WHERE id = ?", (args.id,)
    ).fetchone()
    if not existing:
        print(f"[error] Agent '{args.id}' not found.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    raw_key, key_hash = _generate_api_key()
    conn.execute(
        "UPDATE agents SET api_key_hash = ? WHERE id = ?", (key_hash, args.id)
    )
    conn.commit()
    conn.close()

    print(f"\n  API key rotated for agent '{args.id}'.")
    print("  New API key (shown once — store this securely):")
    print(f"\n    {raw_key}\n")


# ---------------------------------------------------------------------------
# db subcommands
# ---------------------------------------------------------------------------

def db_list(args: argparse.Namespace) -> None:
    conn = _open_index()
    rows = conn.execute(
        "SELECT name, display_name, db_path, schema_version, domain_tags, is_active, created_at "
        "FROM topic_databases ORDER BY name"
    ).fetchall()
    conn.close()

    print()
    _print_table(
        ["Slug", "Display name", "Path", "Schema", "Tags", "Active", "Created"],
        [
            (
                r["name"],
                r["display_name"],
                r["db_path"],
                r["schema_version"],
                r["domain_tags"],
                "yes" if r["is_active"] else "no",
                r["created_at"][:19],
            )
            for r in rows
        ],
    )
    print()


def db_register(args: argparse.Namespace) -> None:
    conn = _open_index()
    existing = conn.execute(
        "SELECT name FROM topic_databases WHERE name = ?", (args.name,)
    ).fetchone()
    if existing:
        print(f"[error] Topic database '{args.name}' is already registered.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    tags = json.dumps([t.strip() for t in args.tags.split(",") if t.strip()])
    now = _now()

    conn.execute(
        """
        INSERT INTO topic_databases
            (id, name, display_name, description, db_path,
             schema_version, domain_tags, is_active, created_at, updated_at, ext)
        VALUES (?, ?, ?, ?, ?, 1, ?, 1, ?, ?, '{}')
        """,
        (
            _new_id(),
            args.name,
            args.display_name,
            args.description or "",
            args.path,
            tags,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    print(f"  Registered topic database '{args.name}' → {args.path}")


def db_deactivate(args: argparse.Namespace) -> None:
    conn = _open_index()
    cursor = conn.execute(
        "UPDATE topic_databases SET is_active = 0, updated_at = ? WHERE name = ?",
        (_now(), args.name),
    )
    if cursor.rowcount == 0:
        print(f"[error] Topic database '{args.name}' not found.", file=sys.stderr)
        conn.close()
        sys.exit(1)
    conn.commit()
    conn.close()
    print(f"  Topic database '{args.name}' deactivated.")


# ---------------------------------------------------------------------------
# init-db subcommand
# ---------------------------------------------------------------------------

def init_db_cmd(args: argparse.Namespace) -> None:
    """Create all database files and register topic DBs in index.db."""
    import asyncio
    from cairn.db.init import init_all

    data_dir = _data_dir()
    print(f"  Initialising databases in {data_dir} …")
    paths = asyncio.run(init_all(data_dir))
    for slug, path in paths.items():
        print(f"  ✓  {slug:<20} {path}")
    print()


def migrate_cmd(args: argparse.Namespace) -> None:
    """Apply pending SQL migrations to index.db.

    Migration files live in cairn/db/migrations/ and are named NNN_description.sql
    where NNN is the target schema version (zero-padded, e.g. 001).
    Migrations are applied in filename order; already-applied ones are skipped
    by comparing against _schema_meta.schema_version.
    """
    migrations_dir = Path(__file__).parent / "db" / "migrations"
    if not migrations_dir.exists():
        print("  No migrations directory found — nothing to apply.")
        return

    conn = _open_index()
    row = conn.execute(
        "SELECT value FROM _schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    current_version = int(row["value"]) if row else 1
    conn.close()

    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        print("  No migration files found — nothing to apply.")
        return

    applied = 0
    for mf in migration_files:
        # File name format: NNN_description.sql — NNN is the target version.
        try:
            target_version = int(mf.stem.split("_")[0])
        except ValueError:
            print(f"  [skip] Cannot parse version from filename: {mf.name}")
            continue

        if target_version <= current_version:
            print(f"  [skip] {mf.name} (already at schema_version={current_version})")
            continue

        print(f"  Applying {mf.name} …")

        # Guard: migration 006 renames vault_path → kb_path, but fresh installs
        # (schema_version >= 6) already have kb_path so the rename would fail.
        # Detect this by checking whether the old column exists.
        if mf.stem.startswith("006_"):
            conn = _open_index()
            cols = {r[0] for r in conn.execute(
                "SELECT name FROM pragma_table_info('promotion_candidates')"
            )}
            conn.close()
            if "vault_path" not in cols:
                # vault_path already gone — schema already has kb_path.
                # Bump the version in _schema_meta to skip the migration cleanly.
                conn = _open_index()
                conn.execute(
                    "UPDATE _schema_meta SET value = ? WHERE key = 'schema_version'",
                    (str(target_version),),
                )
                conn.commit()
                conn.close()
                current_version = target_version
                print(f"  [skip] {mf.name} (vault_path absent — schema already at v{target_version})")
                continue

        # Guard: migration 007 adds soft-delete columns to both index.db
        # (message_index) and each topic DB (messages).  The SQL file only
        # handles index.db; topic DBs are separate SQLite files so we
        # migrate them here in Python.
        if mf.stem.startswith("007_"):
            data = _data_dir()
            conn_idx = _open_index()
            rows = conn_idx.execute(
                "SELECT name, db_path FROM topic_databases"
            ).fetchall()
            conn_idx.close()
            for row in rows:
                db_path = data / row["db_path"]
                if not db_path.exists():
                    continue
                tc = sqlite3.connect(db_path)
                try:
                    tables = {r[0] for r in tc.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )}
                    if "messages" in tables:
                        for col in ["deleted_at TEXT", "deleted_by TEXT"]:
                            try:
                                tc.execute(f"ALTER TABLE messages ADD COLUMN {col}")
                            except sqlite3.OperationalError:
                                pass  # column already exists
                        tc.execute(
                            "CREATE INDEX IF NOT EXISTS idx_messages_deleted_at "
                            "ON messages(deleted_at)"
                        )
                        tc.commit()
                        print(f"    ✓  {row['name']}: added soft-delete columns")
                except Exception as exc:
                    print(f"    [error] {row['name']}: {exc}", file=sys.stderr)
                finally:
                    tc.close()

        conn = _open_index()
        try:
            ddl = mf.read_text(encoding="utf-8")
            conn.executescript(ddl)
            conn.commit()
        except Exception as exc:
            print(f"  [error] Migration failed: {exc}", file=sys.stderr)
            conn.close()
            sys.exit(1)
        conn.close()

        # Re-read the version written by the migration itself.
        conn = _open_index()
        row = conn.execute(
            "SELECT value FROM _schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        current_version = int(row["value"]) if row else current_version
        conn.close()

        print(f"  ✓  {mf.name} → schema_version={current_version}")
        applied += 1

    if applied == 0:
        print(f"  Database is up to date (schema_version={current_version}).")
    else:
        print(f"\n  Applied {applied} migration(s). schema_version={current_version}")
    print()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cairn-admin",
        description="Cairn blackboard provisioning CLI.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # -- init-db -------------------------------------------------------------
    p_init = sub.add_parser("init-db", help="Create database files and register topic DBs.")
    p_init.set_defaults(func=init_db_cmd)

    # -- migrate -------------------------------------------------------------
    p_migrate = sub.add_parser(
        "migrate",
        help="Apply pending SQL migrations to index.db (schema version upgrades).",
    )
    p_migrate.set_defaults(func=migrate_cmd)

    # -- agent ---------------------------------------------------------------
    p_agent = sub.add_parser("agent", help="Manage agents.")
    agent_sub = p_agent.add_subparsers(dest="agent_command", metavar="<subcommand>")
    agent_sub.required = True

    # agent create
    p_ac = agent_sub.add_parser("create", help="Create a new agent and generate an API key.")
    p_ac.add_argument("--id",           required=True, help="Unique agent ID (used in message frontmatter).")
    p_ac.add_argument("--name",         required=True, help="Human-readable display name.")
    p_ac.add_argument("--description",  default="",    help="Optional description.")
    p_ac.add_argument("--capabilities", default="",    help="Comma-separated capability tags (e.g. osint,threat-intel).")
    p_ac.add_argument("--allowed-dbs",  default="",    dest="allowed_dbs",
                      help="Comma-separated DB slugs this agent may write to. Empty = all.")
    p_ac.set_defaults(func=agent_create)

    # agent list
    p_al = agent_sub.add_parser("list", help="List all agents.")
    p_al.set_defaults(func=agent_list)

    # agent deactivate
    p_ad = agent_sub.add_parser("deactivate", help="Deactivate an agent (revokes access).")
    p_ad.add_argument("id", help="Agent ID to deactivate.")
    p_ad.set_defaults(func=agent_deactivate)

    # agent activate
    p_aa = agent_sub.add_parser("activate", help="Re-activate a previously deactivated agent.")
    p_aa.add_argument("id", help="Agent ID to activate.")
    p_aa.set_defaults(func=agent_activate)

    # agent rotate-key
    p_ar = agent_sub.add_parser("rotate-key", help="Generate a new API key for an agent.")
    p_ar.add_argument("id", help="Agent ID.")
    p_ar.set_defaults(func=agent_rotate_key)

    # -- db ------------------------------------------------------------------
    p_db = sub.add_parser("db", help="Manage topic database registrations.")
    db_sub = p_db.add_subparsers(dest="db_command", metavar="<subcommand>")
    db_sub.required = True

    # db list
    p_dl = db_sub.add_parser("list", help="List registered topic databases.")
    p_dl.set_defaults(func=db_list)

    # db register
    p_dr = db_sub.add_parser("register", help="Register a new topic database.")
    p_dr.add_argument("--name",         required=True, help="Unique slug (e.g. network).")
    p_dr.add_argument("--display-name", required=True, dest="display_name", help="Human-readable name.")
    p_dr.add_argument("--path",         required=True, help="Filename relative to data dir (e.g. network.db).")
    p_dr.add_argument("--description",  default="",    help="Optional description.")
    p_dr.add_argument("--tags",         default="",    help="Comma-separated domain tags.")
    p_dr.set_defaults(func=db_register)

    # db deactivate
    p_dd = db_sub.add_parser("deactivate", help="Deactivate a topic database.")
    p_dd.add_argument("name", help="DB slug to deactivate.")
    p_dd.set_defaults(func=db_deactivate)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
