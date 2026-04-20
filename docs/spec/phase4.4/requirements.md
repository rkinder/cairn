# Phase 4.4 — CouchDB Vault Sync Requirements

## Overview

When Cairn promotes a finding to the Obsidian vault, it writes a markdown file
to the bind-mounted vault directory on disk. However, Obsidian LiveSync uses
CouchDB as its sync transport — it does not watch the filesystem. Vault notes
written by Cairn are invisible to Obsidian clients unless an Obsidian instance
happens to be running on the same host with the vault directory open.

This phase adds direct CouchDB integration to the vault writer so that
promoted notes are immediately available to all Obsidian clients via LiveSync,
without requiring a local Obsidian instance as an intermediary.

## Problem Statement

Promoted vault notes exist only as files on disk at `/mnt/data/vault/cairn/`.
No Obsidian client is running on the Docker host to pick them up and push them
to CouchDB. Analysts using Obsidian on their laptops never see the promoted
notes unless they manually browse the Cairn web UI. This breaks the design
intent that the Obsidian vault is the curated knowledge base accessible to
the entire team.

---

## User Stories

### US-1: Automatic Vault Sync to Obsidian Clients
**As an** analyst using Obsidian on my laptop  
**I want** promoted vault notes to appear in my Obsidian vault automatically  
**So that** I can browse, search, and link to curated findings without checking the web UI

**Acceptance Criteria:**
- When a finding is promoted, the vault note appears in all connected Obsidian clients within 60 seconds
- Notes include proper YAML frontmatter, wikilinks, and markdown body as they do today
- Existing vault notes (created by Obsidian directly) are not affected

### US-2: Vault Note Updates Sync
**As an** analyst  
**I want** updates to existing vault notes (e.g., new evidence appended) to sync to my Obsidian  
**So that** I always see the latest version of a promoted finding

**Acceptance Criteria:**
- When the vault writer appends evidence to an existing note, the update propagates to Obsidian clients
- CouchDB revision handling prevents conflicts with any local edits

### US-3: Bidirectional Awareness
**As an** analyst  
**I want** to edit a promoted vault note in Obsidian and have it persist  
**So that** I can enrich curated findings with additional context

**Acceptance Criteria:**
- Cairn reads the current CouchDB revision before writing to avoid overwriting analyst edits
- If a conflict occurs, Cairn's write is stored as a CouchDB conflict revision (not silently dropped)

---

## Functional Requirements

### FR-1: CouchDB Document Write on Promotion
WHEN the vault writer produces a markdown note for a promoted finding  
THE system SHALL write the note content to CouchDB in the LiveSync document format  
THE system SHALL continue to write the file to disk as it does today (dual-write)  
IF the CouchDB write fails THEN the disk write SHALL still succeed and the failure SHALL be logged

### FR-2: LiveSync Document Format
WHEN the system writes a document to CouchDB  
THE system SHALL structure the document to match the Obsidian LiveSync internal format:
- `_id`: the vault-relative file path (e.g., `cairn/104.234.32.23.md`)
- `data`: the full markdown content of the note (for small documents) or chunked into children documents (for large documents)
- `children`: array of child document IDs if the content is chunked
- `ctime`: file creation timestamp (milliseconds since epoch)
- `mtime`: file modification timestamp (milliseconds since epoch)
- `size`: content length in bytes
- `type`: `"plain"` for markdown files

IF the document content exceeds the chunk threshold (default: 250KB)  
THEN the system SHALL split the content into chunk documents and reference them via `children`

### FR-3: Document Update with Revision Handling
WHEN the system updates an existing vault note in CouchDB  
THE system SHALL fetch the current `_rev` before writing  
THE system SHALL include the `_rev` in the update to prevent conflicts  
IF the document does not exist THEN the system SHALL create it (no `_rev` needed)  
IF a revision conflict occurs (409) THEN the system SHALL fetch the latest revision and retry once

### FR-4: CouchDB Connection Configuration
WHEN the system starts  
THE system SHALL read CouchDB connection settings from environment variables:
- `COUCHDB_URL` (default: `http://couchdb:5984`)
- `COUCHDB_USER`
- `COUCHDB_PASSWORD`
- `COUCHDB_DATABASE` (default: `obsidian-livesync`)

IF CouchDB is unreachable at startup THEN the system SHALL log a warning and continue (vault sync is degraded, not fatal)

---

## Non-Functional Requirements

### NFR-1: Latency
- CouchDB write SHALL complete within 2 seconds of the vault writer producing the note
- LiveSync propagation to clients is controlled by the LiveSync plugin (typically < 30 seconds)

### NFR-2: Fault Tolerance
- CouchDB unavailability SHALL NOT prevent promotion from completing
- Disk write is the primary store; CouchDB is the sync transport
- Failed CouchDB writes SHALL be logged with enough detail to retry manually

### NFR-3: No LiveSync Plugin Dependency
- The system SHALL write documents in a format compatible with LiveSync without requiring the LiveSync plugin to be running on the server
- The system SHALL not depend on any LiveSync-specific API — only standard CouchDB HTTP API

---

## Technical Constraints

### TC-1: LiveSync Document Format
- LiveSync stores documents in CouchDB using PouchDB conventions
- The `_id` is the vault-relative file path
- Small files store content directly in the `data` field
- Large files are chunked into separate documents with IDs like `h:<hash>` referenced in `children`
- The exact chunk format should be validated against a real LiveSync database by inspecting existing documents in `/_utils`

### TC-2: CouchDB HTTP API
- All operations use the standard CouchDB REST API (`PUT /{db}/{doc_id}`, `GET /{db}/{doc_id}`)
- Authentication via Basic Auth using the configured credentials
- The `httpx` async client (already a dependency) is used for HTTP requests

### TC-3: Dual-Write Strategy
- Both disk and CouchDB writes happen on every promotion
- Disk write happens first (primary), CouchDB second (sync transport)
- This ensures the vault directory remains the source of truth even if CouchDB is down

---

## Success Metrics

### Functional
- Promoted vault notes appear in Obsidian clients within 60 seconds without manual intervention

### Reliability
- CouchDB write success rate > 99% during normal operation
- Zero promotion failures caused by CouchDB unavailability

---

## Dependencies
- CouchDB container (already running in the stack)
- Obsidian LiveSync plugin configured on analyst clients (already done)
- Vault writer (`cairn/vault/writer.py`) — modified to add CouchDB write

## Assumptions
- The LiveSync document format can be reverse-engineered from existing documents in the CouchDB database
- The LiveSync plugin will pick up documents written directly to CouchDB without requiring special headers or metadata beyond what is documented
- Vault notes produced by Cairn are small enough (< 250KB) to avoid chunking in most cases

## Out of Scope
- Reading vault notes from CouchDB back into Cairn (Cairn reads from disk)
- Syncing non-promoted content (only vault writer output goes to CouchDB)
- Handling LiveSync configuration or plugin settings
- Deleting vault notes from CouchDB (notes are append-only in practice)
