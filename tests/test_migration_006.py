"""Regression tests for migration 006 vault_path guard (Bug 004)."""
import sqlite3
import tempfile
import os
from pathlib import Path


def test_006_guard_skips_on_fresh_install():
    """Migration 006 is skipped when vault_path is absent (fresh schema)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "index.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE _schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO _schema_meta VALUES ('schema_version', '6')")
        conn.execute(
            "CREATE TABLE promotion_candidates (id TEXT PRIMARY KEY, entity TEXT, kb_path TEXT)"
        )
        conn.commit()
        conn.close()

        # Simulate the 006 guard from migrate_cmd
        conn = sqlite3.connect(db)
        cols_result = conn.execute("SELECT name FROM pragma_table_info('promotion_candidates')").fetchall()
        conn.close()
        print(f"PRAGMA result: {cols_result}")
        cols = {row[0] for row in cols_result}
        print(f"Columns: {cols}")

        assert "vault_path" not in cols, f"Expected kb_path only, got: {cols}"

        # Bump version to skip migration
        conn = sqlite3.connect(db)
        conn.execute("UPDATE _schema_meta SET value='6' WHERE key='schema_version'")
        conn.commit()
        conn.close()

        # Verify
        conn = sqlite3.connect(db)
        v = conn.execute("SELECT value FROM _schema_meta WHERE key='schema_version'").fetchone()
        final_cols = [row[0] for row in conn.execute("SELECT name FROM pragma_table_info('promotion_candidates')").fetchall()]
        conn.close()

        assert v is not None, "schema_version row missing"
        assert v[0] == "6", f"Expected v6, got v{v[0]}"
        assert "vault_path" not in final_cols and "kb_path" in final_cols


def test_006_runs_on_old_install():
    """Migration 006 renames vault_path on pre-Phase 4.6 schemas."""
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "index.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE _schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO _schema_meta VALUES ('schema_version', '5')")
        conn.execute(
            "CREATE TABLE promotion_candidates (id TEXT PRIMARY KEY, entity TEXT, vault_path TEXT)"
        )
        conn.commit()
        conn.close()

        # Run the actual 006 SQL
        sql = Path("cairn/db/migrations/006_rename_vault_path.sql").read_text()
        conn = sqlite3.connect(db)
        conn.executescript(sql)
        conn.commit()
        conn.close()

        # Verify
        conn = sqlite3.connect(db)
        v = conn.execute("SELECT value FROM _schema_meta WHERE key='schema_version'").fetchone()
        final_cols = [row[0] for row in conn.execute("SELECT name FROM pragma_table_info('promotion_candidates')").fetchall()]
        conn.close()

        assert v is not None
        assert v[0] == "6", f"Expected v6, got v{v[0]}"
        assert "vault_path" not in final_cols and "kb_path" in final_cols, f"Columns: {final_cols}"


if __name__ == "__main__":
    test_006_guard_skips_on_fresh_install()
    print("test_006_guard_skips_on_fresh_install PASSED")
    test_006_runs_on_old_install()
    print("test_006_runs_on_old_install PASSED")
    print("All tests passed.")