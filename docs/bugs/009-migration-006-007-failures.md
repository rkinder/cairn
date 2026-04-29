# BUG: Migration 006 index error + Migration 007 fails on topic DB tables

## Summary

Two migration bugs discovered during April 29 deployment:

### Bug A: Migration 006 — `r[1]` IndexError

`manage.py` line ~385 uses `SELECT name FROM pragma_table_info(...)` which
returns single-column rows, but indexes with `r[1]` instead of `r[0]`.

**Error:** `IndexError: tuple index out of range`

**Fix:** Changed `r[1]` to `r[0]`.

### Bug B: Migration 007 — `ALTER TABLE messages` against index.db

Migration 007 runs `ALTER TABLE messages ADD COLUMN deleted_at` against
`index.db`, but the `messages` table only exists in topic databases
(osint.db, vulnerabilities.db, etc.). The `message_index` table is in
index.db.

**Error:** `sqlite3.OperationalError: no such table: messages`

**Impact:** All API endpoints referencing `deleted_at` return 500 until
the columns are manually added. This broke all client access to the
blackboard.

**Fix:**
1. Split 007.sql to only ALTER `message_index` in index.db
2. Added a `007_` guard in `migrate_cmd` that iterates topic databases
   and adds `deleted_at`/`deleted_by` columns to each `messages` table
