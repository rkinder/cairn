# BUG: docker-compose.yml passes CAIRN_VAULT_PATH but app reads CAIRN_QUARTZ_CONTENT_DIR

## Summary

The Phase 4.6 refactor renamed the application config field from `vault_path`
to `quartz_content_dir` and its corresponding environment variable from
`CAIRN_VAULT_PATH` to `CAIRN_QUARTZ_CONTENT_DIR`. The `docker-compose.yml`
was not updated to match. As a result, the `cairn-api` container starts with
`quartz_content_dir` silently defaulting to `./kb_content` (relative to the
container working directory) rather than the intended `/vault` bind mount.
Any notes written to the knowledge base are lost on container restart and the
`/vault` bind mount serves no purpose.

## Severity

**High** — The knowledge base bridge is silently non-functional in the Docker
deployment. No error is raised at startup. Promotions appear to succeed
(the API returns 200) but vault notes are written to an ephemeral path inside
the container that is not persisted and not reachable by any downstream
consumer.

## Reproduction

```bash
cp .env.example .env
# Fill in CAIRN_GITLAB_TOKEN, CAIRN_GITLAB_PROJECT_ID, CAIRN_VAULT_PATH=/path/to/vault
docker compose up -d
docker compose exec cairn-api cairn-admin init-db
docker compose exec cairn-api cairn-admin agent create --id test-agent --name "Test"
# Post a message and promote it via the UI...
# Then inspect the vault bind mount — no notes appear
ls /path/to/vault/cairn/   # empty or missing
# Check inside the container instead
docker compose exec cairn-api find /app/kb_content -name "*.md" 2>/dev/null
# Notes are here, not in /vault
```

**Expected:** Promoted notes written to `/vault/cairn/` (the bind-mounted
host directory).

**Actual:** Promoted notes written to `/app/kb_content/` (ephemeral, inside
the container). Lost on `docker compose down` or image rebuild.

## Root Cause

During the Phase 4.6 refactor (commit `9851bf7`), the `Settings` model in
`cairn/config.py` was updated:

```python
# Before (Phase 4.x):
vault_path: Path = Field(default=Path("/vault"), ...)

# After (Phase 4.6):
quartz_content_dir: Path = Field(default=Path("./kb_content"), ...)
```

With `env_prefix="CAIRN_"`, this field reads from `CAIRN_QUARTZ_CONTENT_DIR`.
The `docker-compose.yml` was not updated and still sets the old variable:

```yaml
# docker-compose.yml (current — wrong)
environment:
  CAIRN_VAULT_PATH: /vault          # ← ignored by Settings; no such field
  CAIRN_VAULT_COLLECTION: ...       # ← this one is still valid

# Also still wrong — the bind mount uses the old variable:
volumes:
  - ${CAIRN_VAULT_PATH:-/vault}:/vault   # mounts correctly but the app never reads /vault
```

`CAIRN_VAULT_PATH` is not a recognised `Settings` field (pydantic-settings
with `extra="ignore"` silently discards unknown variables). The app starts
without error and uses the default `./kb_content`.

## Files

| File | Change Needed |
|---|---|
| `docker-compose.yml` | Replace `CAIRN_VAULT_PATH: /vault` with `CAIRN_QUARTZ_CONTENT_DIR: /vault` in the `cairn-api` environment block |
| `docker-compose.yml` | Update the bind mount variable reference from `CAIRN_VAULT_PATH` to `CAIRN_QUARTZ_CONTENT_DIR` |

## Fix

In `docker-compose.yml`, under the `cairn-api` service:

```yaml
# Environment block — replace:
CAIRN_VAULT_PATH: /vault

# With:
CAIRN_QUARTZ_CONTENT_DIR: /vault
```

Update the volumes bind mount to use the new variable so the `.env` file
remains the single source of truth:

```yaml
# volumes — replace:
- ${CAIRN_VAULT_PATH:-/vault}:/vault

# With:
- ${CAIRN_QUARTZ_CONTENT_DIR:-/vault}:/vault
```

The `CAIRN_VAULT_COLLECTION` environment variable in the same block is still
valid (`Settings.vault_collection`) and does not need to change.

## Related

- Phase 4.6 refactor commit: `9851bf7`
- Bug #004 (migration 006 fails on fresh schema) — same refactor, same commit
- Bug #006 (`.env.example` documents old variable name) — same refactor, same commit
