# BUG: CouchDB service and environment variables orphaned in docker-compose.yml after removal

## Summary

CouchDB support was removed from the application in commit `b828185`
("refactor: Remove CouchDB support, add knowledge base sync worker").
The `cairn/vault/couchdb_sync.py` module and all Python references were
deleted, `cairn/api/deps.py` was emptied of its CouchDB client code, and
`cairn/config.py` has no CouchDB settings fields. However, `docker-compose.yml`
was not updated: the `couchdb` service definition, its named volume, all
CouchDB environment variables on the `cairn-api` service, and a
`depends_on: couchdb: condition: service_healthy` startup dependency remain.
On every `docker compose up`, a CouchDB container starts unnecessarily and
the `cairn-api` container waits for it to pass a health check before starting.

## Severity

**Medium** — The stack starts and the API functions correctly once CouchDB
passes its health check. There is no data loss and no functional regression.
However:

1. **Startup time** — CouchDB's health check has a 10s interval, 5 retries,
   and a 10s start period. The `cairn-api` container cannot start until all
   retries pass, adding up to ~10–60 seconds of unnecessary delay on a cold
   start.
2. **Resource consumption** — CouchDB is an Erlang/OTP application. It
   consumes meaningful memory and CPU even at idle.
3. **Misleading configuration** — Operators reading `docker-compose.yml` or
   the quick-start comment block will believe CouchDB is a required
   dependency and spend time provisioning `COUCHDB_USER`/`COUCHDB_PASSWORD`
   credentials that serve no purpose.
4. **Stale environment variables** — `CAIRN_COUCHDB_ENABLED` is passed to
   `cairn-api` but `config.py` has no `couchdb_enabled` field, so it is
   silently ignored. The other `COUCHDB_*` variables (without the `CAIRN_`
   prefix) are also ignored by pydantic-settings.

## Affected Configuration

In `docker-compose.yml`:

```yaml
# cairn-api service — stale environment variables:
COUCHDB_URL: http://couchdb:5984        # no Settings field; ignored
COUCHDB_USER: ${COUCHDB_USER}           # no Settings field; ignored
COUCHDB_PASSWORD: ${COUCHDB_PASSWORD}   # no Settings field; ignored
COUCHDB_DATABASE: ${COUCHDB_DATABASE:-obsidian-livesync}  # no Settings field; ignored
CAIRN_COUCHDB_ENABLED: ${CAIRN_COUCHDB_ENABLED:-true}    # no Settings field; ignored

# cairn-api service — stale dependency:
depends_on:
  couchdb:
    condition: service_healthy    # blocks API startup until CouchDB is healthy

# Stale service definition:
couchdb:
  image: couchdb:3
  ...

# Stale named volume:
volumes:
  couchdb_data:
    driver: local
```

## Fix

Remove all CouchDB remnants from `docker-compose.yml`:

1. Delete the `couchdb` service definition.
2. Delete `couchdb_data` from the top-level `volumes` block.
3. Remove the five stale CouchDB environment variables from the `cairn-api`
   environment block.
4. Remove `couchdb` from the `cairn-api` `depends_on` block. If no other
   `depends_on` entries remain, remove the `depends_on` key entirely;
   otherwise retain the `chromadb` dependency if ChromaDB startup ordering
   matters.
5. Remove the CouchDB setup instructions from the `couchdb` service comment
   block in the compose file (lines 143–165 approximately).

Also remove the stale `CAIRN_VAULT_PATH` reference in `.env.example` for
CouchDB credentials (`COUCHDB_USER`, `COUCHDB_PASSWORD`, `COUCHDB_DATABASE`,
`CAIRN_COUCHDB_ENABLED`) — these are already superseded by Bug #006.

## Related

- CouchDB removal commit: `b828185`
- Bug #005 (docker-compose env var mismatch) — same compose file, same cleanup pass
- Bug #006 (`.env.example` stale variables) — also documents CouchDB vars that should be removed
