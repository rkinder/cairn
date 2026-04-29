# BUG: .env.example documents the old CAIRN_VAULT_PATH variable

## Summary

`.env.example` — the operator-facing configuration guide — still documents
`CAIRN_VAULT_PATH` as the environment variable for the knowledge base
directory. This variable was renamed to `CAIRN_QUARTZ_CONTENT_DIR` during
the Phase 4.6 refactor. An operator following the quick-start instructions
will set the wrong variable, the application will silently ignore it, and
vault note writes will land in the ephemeral default path (`./kb_content`)
inside the container rather than the mounted host directory.

## Severity

**High** — `.env.example` is the first place an operator looks when
configuring the stack. A misconfigured knowledge base path produces no
startup error and no warning at promotion time — the silent failure means
the operator may not notice until they check the vault directory and find it
empty, potentially after many promotions have been lost.

## Reproduction

```bash
cp .env.example .env
# Follow the documented instructions: set CAIRN_VAULT_PATH to the vault directory
# Start the stack — no error
# Promote a message — API returns 200
# Check the configured vault path — no notes written there
```

## Root Cause

During the Phase 4.6 refactor (commit `9851bf7`), `cairn/config.py` renamed
the settings field and its corresponding environment variable:

```
CAIRN_VAULT_PATH       →   CAIRN_QUARTZ_CONTENT_DIR
```

`.env.example` was not updated. It still contains:

```bash
# Absolute path to the Obsidian vault directory on the host.
CAIRN_VAULT_PATH=/vault
```

With `extra="ignore"` in `pydantic-settings`, the application silently
discards `CAIRN_VAULT_PATH` and uses the `quartz_content_dir` default
(`./kb_content`).

The `CAIRN_VAULT_COLLECTION` variable documented directly below
`CAIRN_VAULT_PATH` remains valid — the `Settings.vault_collection` field
still reads from that variable.

## Files

| File | Change Needed |
|---|---|
| `.env.example` | Replace `CAIRN_VAULT_PATH` with `CAIRN_QUARTZ_CONTENT_DIR`; update the surrounding comment |

## Fix

In `.env.example`, replace the Obsidian vault bridge section with content
that reflects the Phase 4.6 Quartz rename:

```bash
# ---------------------------------------------------------------------------
# Knowledge base (Quartz — Phase 4.6)
# ---------------------------------------------------------------------------
# Absolute path to the Quartz content directory on the host.
# In Docker Compose this is bind-mounted into the container at the same path.
# Promoted notes are written here as Markdown files.
CAIRN_QUARTZ_CONTENT_DIR=/vault

# Command to run after a note is written (e.g. 'npx quartz sync').
# Leave unset to disable automatic sync.
# CAIRN_QUARTZ_SYNC_CMD=npx quartz sync

# ChromaDB collection name for promoted vault notes (separate from methodologies).
# CAIRN_VAULT_COLLECTION=vault-notes
```

Remove the now-stale `CAIRN_CORROBORATION_*` and `CAIRN_PROMOTION_*` section
comments that reference "Obsidian vault" if those have also been superseded by
the Quartz migration, or update them to reference the Quartz knowledge base.

## Related

- Phase 4.6 refactor commit: `9851bf7`
- Bug #004 (migration 006 fails on fresh schema) — same refactor, same commit
- Bug #005 (`docker-compose.yml` env var mismatch) — same refactor, same commit
