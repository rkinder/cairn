# BUG: CouchDB vault sync document format incompatible with LiveSync

## Summary

Cairn's CouchDB vault sync (Phase 4.4) writes documents that Obsidian
LiveSync rejects or cannot process correctly. The document format needs
to be validated against the actual LiveSync source code.

## Severity

**Medium** — Disk writes work correctly. CouchDB sync is the delivery
mechanism to Obsidian clients. Vault notes exist on disk but don't reach
analysts' Obsidian instances.

## Known Issues

### 1. Size field mismatch

LiveSync reported: `File cairn/67.59.1.130.md seems to be corrupted! (6977 != 0)`

- `size` was set to `len(content.encode("utf-8"))` (byte length: 6977)
- `data` string length is 6943 characters
- LiveSync compares `size` against string character count, not byte length
- Multi-byte characters (em dashes, etc.) cause the mismatch
- **Partial fix applied:** changed to `len(content)` (character count)
- **Status:** Fix may have introduced a new issue — documents stopped
  appearing in CouchDB after the change. Needs investigation.

### 2. Document format not validated against LiveSync source

The document structure was reverse-engineered from community blog posts
and GitHub issues, not from the LiveSync plugin source code. Fields that
may be wrong or missing:

- `type`: using `"plain"` — is this correct for markdown files?
- `children`: using `[]` for non-chunked docs — should this be omitted?
- `ctime`/`mtime`: using milliseconds — correct unit?
- Missing fields: LiveSync may expect additional metadata fields
  (e.g., `deleted`, `eden`, or internal versioning fields)

### 3. Chunking format untested

The `_put_chunked` path for large documents (>250KB) has not been tested
in production. The chunk document ID format (`h:<hash>`) and structure
are based on community documentation.

## Investigation Needed

1. **Read the LiveSync source code** — specifically the document
   read/write logic in the plugin:
   - https://github.com/vrtmrz/obsidian-livesync
   - Look for how it reads documents from CouchDB
   - Identify all required fields and their expected types/units

2. **Inspect a real LiveSync document** — create a note in Obsidian,
   let LiveSync sync it to CouchDB, then examine the document:
   ```bash
   curl -s "http://localhost:5984/obsidian-livesync/welcome.md" \
     -u cairn:<password> | python3 -m json.tool
   ```
   Compare field-by-field against what Cairn writes.

3. **Test the size field** — confirm whether LiveSync expects:
   - Character count (`len(string)`)
   - Byte length (`len(string.encode("utf-8"))`)
   - Or something else entirely

## Files

- `cairn/vault/couchdb_sync.py` — the CouchDB client
- `cairn/vault/writer.py` — calls `put_note()` after disk write

## Workaround

Vault notes are written to disk correctly. An Obsidian instance pointed
at the vault directory on the host would pick them up via filesystem
watching (Option A from the original Phase 4.4 requirements).
