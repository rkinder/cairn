# Phase 4.6 Quartz Migration Requirements

## Overview
Phase 4.6 will replace Obsidian with Quartz 4 as the static site generator for the curated human Knowledge Base (KB). This removes the need for human analysts to have the Obsidian Desktop app installed and provides a centralized web-viewable knowledge sharing platform. It also lays the groundwork for the Phase 5 enterprise features.

## Problem Statement
The current Tier 2 Knowledge Base relies on Obsidian, which requires users to sync a local vault via a file-sharing mechanism and use a desktop application to view promoted intelligence. This approach limits accessibility, breaks the web-native flow, and introduces syncing friction across an organization.

## User Stories

### US-1: Web-Native KB Access
**As an** analyst  
**I want** to access promoted intelligence in a web browser  
**So that** I don't need to configure a local Obsidian vault to read reports.

### US-2: Sequential Knowledge Base Compilation
**As a** system administrator  
**I want** Cairn to queue and automatically push promoted notes to a Quartz repository in order  
**So that** the KB website stays continuously up-to-date without concurrent Git lock crashes.

### US-3: Track Project Future Enhancements
**As a** project maintainer  
**I want** a living document detailing Phase 5 and future enhancements  
**So that** the broader enterprise capabilities (PostgreSQL, Multi-agent workflows, TUI) are not lost.

## Functional Requirements

### FR-1: Environment Configuration
- **WHEN** the system is configured, **THE system SHALL** read `CAIRN_QUARTZ_CONTENT_DIR` instead of `CAIRN_VAULT_DIR`.
- **IF** `CAIRN_QUARTZ_CONTENT_DIR` is not provided, **THEN** the system SHALL fall back to a safe local default (e.g., `./cairn_kb_content`).

### FR-2: Markdown Promotion Compatibility
- **WHEN** a finding is promoted, **THE system SHALL** write the Markdown note into the Quartz content directory.
- **WHILE** generating the note, **THE system SHALL** maintain strict compatibility with Quartz-flavored Markdown (Obsidian-style wikilinks and YAML frontmatter).

### FR-3: Sequential GitOps Sync Queue
- **WHEN** a note is successfully written, **THE system SHALL** add a sync request to an internal asynchronous queue.
- **WHILE** the server is running, **THE system SHALL** process the queue sequentially via a single background worker to prevent concurrent execution collisions (Git index locks).

### FR-4: Database Schema Accuracy
- **WHEN** the application starts, **THE system SHALL** apply a migration renaming `vault_path` to `kb_path` in all relevant SQLite tables.

### FR-5: Document Future Enhancements
- **WHEN** the project documentation is generated, **THE system SHALL** include a dedicated `ROADMAP.md` or `PHASE5.md` file tracking future enterprise and UI features.

## Non-Functional Requirements

### NFR-1: Backward Compatibility
- Existing `.md` content previously generated for Obsidian must render perfectly in Quartz 4 without data migration scripts.
- ChromaDB semantic search logic must gracefully handle the transition from `vault_path` to `kb_path` metadata without vector corruption.

## Technical Constraints

### TC-1: Async Event Loop Safety
- The sequential sync worker must use `asyncio.create_subprocess_shell` or `asyncio.create_subprocess_exec` to avoid blocking the FastAPI event loop during Git pushes.
