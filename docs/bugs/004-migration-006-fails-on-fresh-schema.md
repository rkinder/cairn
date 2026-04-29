# BUG: Migration 006 fails on a fresh database install

## Summary

Running `cairn-admin migrate` against a freshly initialised database fails
with a SQLite error. Migration `006_rename_vault_path.sql` attempts to rename
a column (`vault_path → kb_path`) that does not exist on a fresh install
because `index.sql` — the base schema used by `init-db` — already uses
`kb_path` and was updated as part of the same Phase 4.6 refactor that
introduced the migration.

The migration was written to fix an existing upgraded database but was never
guarded against running on a schema that already incorporates the rename.

## Severity

**High** — Blocks the documented quick-start workflow on any fresh stack.
`cairn-admin migrate` is a required step after `init-db`; if it fails the
database is left in an inconsistent state and the API will not start cleanly.

## Reproduction

```bash
# Fresh stack — follow the quick-start in docker-compose.yml
docker compose exec cairn-api cairn-admin init-db
docker compose exec cairn-api cairn-admin migrate
```

**Expected:** All migrations apply cleanly, database is at `schema_version=6`.

**Actual:** Migration `006_rename_vault_path.sql` raises:

```
sqlite3.OperationalError: no such column: "vault_path"
```

The `migrate` command exits non-zero, leaving `schema_version` at `5` and
the migration state inconsistent.

## Root Cause

Three compounding problems exist in `006_rename_vault_path.sql`:

### Problem 1: Column doesn't exist on a fresh install

`index.sql` was updated to use `kb_path` during the Phase 4.6 refactor
(commit `9851bf7`). `init-db` creates the database from `index.sql` directly,
so `vault_path` never exists on a fresh install. When `migrate` runs, it sees
`schema_version=5 < target=6` and executes the migration, which immediately
fails.

### Problem 2: No transaction wrapper

Every other migration (`001` through `005`) wraps its DDL in `BEGIN`/`COMMIT`.
Migration `006` does not. If any future statement were added after the
`ALTER TABLE`, a failure mid-migration would leave the database partially
modified with no rollback path.

### Problem 3: No `schema_version` bump

The migration never updates `_schema_meta` to set `schema_version=6`. The
`migrate_cmd` runner re-reads the version from the database after each
migration. Because `006` doesn't bump the version, the runner reads `5` again
and reports `schema_version=5` even if the migration ran "successfully". On
the next `cairn-admin migrate` run the runner will evaluate `6 > 5` again and
attempt to re-execute `006` — which will fail on an upgraded database where
`kb_path` already exists (the rename already happened).

## Files

| File | Issue |
|---|---|
| `cairn/db/migrations/006_rename_vault_path.sql` | Missing `IF EXISTS` guard, missing `BEGIN`/`COMMIT`, missing `schema_version` bump |
| `cairn/db/schema/index.sql` | Already uses `kb_path` — base schema is ahead of the migration |

## Fix

### Option A: Delete the migration (recommended for this branch)

Since `index.sql` already has `kb_path` and this branch (`spec/phase4.6`) has
not shipped to production, there is no existing deployed database that needs
the rename. Remove `006_rename_vault_path.sql` entirely.

Update `index.sql` to reflect the current state as `schema_version=6`
(incrementing from 5 to signal the rename as part of the base schema history),
and document the version bump in the schema header comment:

```sql
-- v5 → v6: renamed vault_path to kb_path in promotion_candidates (Phase 4.6).
```

### Option B: Guard the migration with a schema check (if upgrade path must be preserved)

If there are existing staging databases using the old `vault_path` column name
that need to be migrated, keep the file but make it safe to run on both old
and new schemas. SQLite does not support `ALTER TABLE ... RENAME COLUMN IF EXISTS`,
so the guard must be done at the runner level or by checking `PRAGMA table_info`:

```sql
BEGIN;

-- Only rename if vault_path still exists (skip on fresh installs).
-- Run the migration conditionally via cairn-admin migrate logic,
-- or use a Python migration runner instead of raw executescript.

ALTER TABLE promotion_candidates RENAME COLUMN vault_path TO kb_path;

UPDATE _schema_meta SET value = '6' WHERE key = 'schema_version';

COMMIT;
```

The `migrate_cmd` in `cairn/manage.py` would need to be extended to check
`PRAGMA table_info(promotion_candidates)` before executing the migration file,
or the migration file itself converted to a Python callable.

## Related

- Phase 4.6 refactor commit: `9851bf7` (renamed `vault_path` → `kb_path` in schema)
- Bug #005 (`docker-compose.yml` env var mismatch) — same refactor, same commit
- Bug #006 (`.env.example` documents old variable name) — same refactor, same commit
