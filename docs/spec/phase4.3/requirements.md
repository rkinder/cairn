# Phase 4.3 — Retroactive Promotion Review Requirements

## Overview

Add the ability for agents and analysts to retroactively scan existing
blackboard messages, score them against promotion criteria, and flag
candidates for vault promotion in bulk. Currently, messages can only be
flagged at post time or individually by ID — there is no discovery or
bulk review mechanism for the existing backlog.

## Problem Statement

Investigations have been running through Cairn, producing real findings
with durable value. None have been flagged for promotion because the skill
did not previously instruct agents to do so, and there is no endpoint to
review unflagged messages after the fact. Valuable knowledge is trapped in
the SQLite message feed with no path to the curated Obsidian vault.

---

## User Stories

### US-1: Retroactive Promotion Scan
**As an** agent or analyst  
**I want** to query unflagged messages that may warrant promotion  
**So that** durable knowledge from past investigations reaches the vault

**Acceptance Criteria:**
- Can query messages that have `promote = none` with optional filters (topic_db, tags, date range, confidence threshold)
- Results are ranked by promotion likelihood (confidence score, corroboration count, entity density)
- Returns message summaries with enough context to decide without fetching each individually

### US-2: Promotion Scoring Guidance
**As an** agent  
**I want** the API to return a promotion score for unflagged messages  
**So that** I can prioritize which findings to flag without reading every message body

**Acceptance Criteria:**
- Score is computed from: confidence value, number of corroborating messages, entity count, message age, tag relevance
- Score is returned alongside each message in the review query results
- Score is advisory — the agent or human makes the final flag decision
- Agent flags individual messages using the existing `POST /messages/{id}/promote` endpoint

---

## Functional Requirements

### FR-1: Promotion Review Query
WHEN an authenticated agent sends `GET /promotions/review`  
THE system SHALL return messages where `promote = none` ordered by computed promotion score descending  
THE system SHALL require a `topic_db` parameter to scope the review to a single topic database  
IF `tags` filter is provided THEN only messages matching any of the specified tags SHALL be returned  
IF `min_confidence` filter is provided THEN only messages with confidence >= the threshold SHALL be returned  
IF `since` date filter is provided THEN only messages posted after that date SHALL be returned  
IF `limit` is provided THEN at most that many results SHALL be returned (default: 50, max: 200)

### FR-2: Promotion Score Computation
WHEN the system computes a promotion score for a message  
THE system SHALL calculate a weighted score from:
- `confidence` field value (weight: 0.3) — higher confidence = higher score
- corroboration count (weight: 0.3) — number of other messages referencing the same entities
- entity density (weight: 0.2) — number of extractable entities in the message body
- message age (weight: 0.1) — older confirmed findings have proven durability
- tag count (weight: 0.1) — more tags indicate richer context

THE score SHALL be normalized to a 0.0–1.0 range  
IF any input is missing (e.g., no confidence set) THEN that component SHALL contribute 0 to the score

### FR-3: Agent-Driven Backlog Review
WHEN an agent is instructed to review the backlog for promotable findings  
THE system SHALL support the agent calling `GET /promotions/review` to discover candidates  
THE agent SHALL evaluate each candidate against the promotion criteria in the skill  
THE agent SHALL call `POST /messages/{id}/promote` for each message it determines is promotable using the existing single-message promotion endpoint

---

## Non-Functional Requirements

### NFR-1: Query Performance
- The promotion review query SHALL return results within 2 seconds for databases with up to 10,000 messages
- The promotion score computation SHALL not require additional database tables (computed at query time)

### NFR-2: Existing Endpoint Reuse
- Individual message promotion SHALL use the existing `POST /messages/{id}/promote` endpoint — no new flagging endpoint is needed

---

## Technical Constraints

### TC-1: Database
- Promotion score is computed at query time, not stored — avoids schema changes and stale scores
- The query joins against the cross-domain `message_index` in `index.db` for filtering, then fetches full records from topic DBs as needed

### TC-2: Entity Counting
- Entity density is computed by running the entity extractor regex patterns against the message body at query time
- For performance, entity counting MAY be cached per message after first computation

### TC-3: Corroboration Counting
- Corroboration count uses the existing entity overlap logic from the corroboration job
- Counts messages from distinct agents that share at least one extracted entity with the candidate message

---

## Success Metrics

### Adoption
- Within 2 weeks of deployment, at least 10 existing messages are flagged for promotion via the review endpoint

### Quality
- Promotion score ranking correlates with human analyst judgment — top-scored messages are genuinely the most promotable

---

## Dependencies
- Phase 4 promotion pipeline (existing — `promote` field, promotion queue UI)
- Entity extractor (existing — `cairn/nlp/entity_extractor.py`)
- Corroboration job logic (existing — `cairn/jobs/corroboration.py`)

## Assumptions
- The existing message backlog contains findings with durable value that were not flagged at post time
- Agents will be instructed via the skill to periodically run backlog reviews

## Out of Scope
- Automatic promotion without human review (all flags go to the review queue)
- Modifying the promotion queue UI (existing UI handles candidates regardless of how they were flagged)
- Retroactive entity extraction and storage (entities are extracted at query time for scoring)
