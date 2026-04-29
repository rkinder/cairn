# Message Deletion API — Implementation Notes

This document records the implementation status and behavior of the message deletion API feature.

## Implemented Endpoints

### 1) `DELETE /messages/{message_id}?db=<topic>&hard=<bool>`

- Default behavior: **soft delete**
  - Sets:
    - `deleted_at` (ISO8601 timestamp)
    - `deleted_by` (agent id)
- Authorization:
  - Soft delete requires ownership (authoring agent).
  - `hard=true` requires admin capability.
- Hard delete behavior:
  - Removes row from topic `messages`.
  - Removes row from `message_index`.

### 2) `DELETE /messages?db=<topic>&tags=<csv>&confirm=true`

- Bulk soft-delete by tags.
- Safety guard:
  - Requires `confirm=true`.
- Applies soft-delete fields (`deleted_at`, `deleted_by`) to matching active rows.
- Returns deleted ids and count.

### 3) `DELETE /messages/thread/{thread_id}?db=<topic>`

- Soft-deletes all non-deleted messages in the thread.
- Returns deleted ids and count.

---

## Visibility Rules

### `GET /messages`

- Default excludes soft-deleted rows.
- `include_deleted=true` supported for admin visibility use-cases.

### `GET /messages/{message_id}`

- Deleted records hidden by default.
- `include_deleted=true` allows viewing deleted records (admin-gated behavior in route logic).

---

## Schema/Migration Changes

### Migration
- `cairn/db/migrations/007_add_message_deletion_fields.sql`
  - Adds `deleted_at`, `deleted_by` to:
    - topic `messages`
    - `message_index`
  - Adds indexes for deleted timestamp filtering.
  - Bumps schema version to `7`.

### Base schema updates
- `cairn/db/schema/index.sql`
  - schema version `7`
  - `message_index` includes `deleted_at`, `deleted_by`
- `cairn/db/schema/topic_common.sql`
  - `messages` includes `deleted_at`, `deleted_by`
- `cairn/db/schema/osint.sql`
  - schema version `2`
  - `messages` includes `deleted_at`, `deleted_by`

---

## Auth Helper Changes

- `cairn/api/deps.py`
  - Added:
    - `agent_is_admin(agent) -> bool`
    - `require_admin(agent) -> None`

---

## Tests

### New test file
- `tests/test_messages_delete_api.py`
  - Covers:
    - owner soft-delete flow
    - non-owner forbidden soft-delete
    - admin-only hard-delete
    - bulk-by-tag confirm handling
    - thread deletion behavior
    - deleted visibility (`include_deleted`)

### Updated tests
- `tests/test_db_init_it.py`
  - Updated expected message columns to include deletion metadata.
  - Updated expected osint schema version to `2`.

---

## Notes

- This implementation keeps soft-delete as default and uses hard-delete only behind explicit admin authorization.
- Query visibility is designed so normal message listings/details do not expose deleted records unless explicitly requested and authorized.
