# Phase 4.3 — Retroactive Promotion Review Tasks

## Phase 1: Core Scoring Logic

### Task 1.1: Promotion Scorer
**Estimate:** 2 hours  
**Priority:** High

- [x] Create `cairn/jobs/promotion_scorer.py`
- [x] Implement `PromotionScorer.score()` with weighted components
- [x] Confidence component: direct field value × 0.3
- [x] Corroboration component: distinct agent entity overlap × 0.3
- [x] Entity density component: regex entity count × 0.2
- [x] Age component: days since post (with confidence gate) × 0.1
- [x] Tag count component: tag list length × 0.1
- [x] Normalize total to 0.0–1.0 range
- [x] Return both total score and per-component breakdown

**Files to create:**
- `cairn/jobs/promotion_scorer.py`

**Property tests:**
- [x] WHEN confidence is 1.0 and all other inputs are zero THEN score SHALL be 0.3
- [x] WHEN confidence is None THEN confidence component SHALL be 0.0
- [x] WHEN corroboration count is >= 3 THEN corroboration component SHALL be 0.3
- [x] WHEN entity count is >= 5 THEN entity density component SHALL be 0.2
- [x] WHEN message age is >= 30 days and confidence >= 0.5 THEN age component SHALL be 0.1
- [x] WHEN message age is >= 30 days and confidence < 0.5 THEN age component SHALL be 0.0
- [x] WHEN all components are maxed THEN total score SHALL be 1.0

---

## Phase 2: API Endpoint

### Task 2.1: GET /promotions/review Endpoint
**Estimate:** 3 hours  
**Priority:** High

- [x] Add `PromotionCandidate` and `ScoreBreakdown` response models to `promotions.py`
- [x] Implement `GET /promotions/review` route handler
- [x] Query unflagged messages (`promote = 'none'`) from specified topic DB
- [x] Apply SQL-level filters: tags, min_confidence, since date
- [x] Run promotion scorer on each result
- [x] Sort by promotion_score descending
- [x] Apply limit and return

**Files to modify:**
- `cairn/api/routes/promotions.py`

**Property tests:**
- [x] WHEN topic_db is not provided THEN response SHALL be 422
- [x] WHEN topic_db is invalid THEN response SHALL be 404
- [x] WHEN no unflagged messages exist THEN response SHALL be empty list
- [x] WHEN messages exist THEN results SHALL be ordered by promotion_score descending
- [x] WHEN min_confidence=0.8 THEN no message with confidence < 0.8 SHALL appear
- [x] WHEN since=2026-04-15 THEN no message before that date SHALL appear
- [x] WHEN limit=5 THEN at most 5 results SHALL be returned

---

## Phase 3: Skill and Documentation

### Task 3.1: Update Agent Skill
**Estimate:** 1 hour  
**Priority:** Medium

- [x] Add `review_promotable` operation to SKILL.md with curl example
- [x] Add workflow step: "periodically review backlog for promotable findings"
- [x] Document the score breakdown fields so agents can explain their flagging decisions

**Files to modify:**
- `~/.kiro/skills/cairn/SKILL.md` → now in cairn-skill repo
- `cairn-skill-kiro` GitLab repo

### Task 3.2: Update API Operations Reference
**Estimate:** 30 minutes  
**Priority:** Medium

- [x] Add `GET /promotions/review` to `references/api-operations.md`
- [x] Document query parameters, response model, and score breakdown

**Files to modify:**
- `docs/agent-skill/references/api-operations.md`
- `cairn-skill/skills/cairn/references/api-operations.md`

---

## Testing and Quality Assurance

### Task QA.1: Unit Tests for Promotion Scorer
**Estimate:** 1.5 hours  
**Priority:** High

- [x] Test each scoring component in isolation
- [x] Test combined score with known inputs
- [x] Test edge cases: None confidence, 0 entities, 0 corroboration, new message, old message
- [x] Test normalization bounds (never < 0.0, never > 1.0)

**Files to create:**
- `tests/test_promotion_scorer.py`

### Task QA.2: Integration Tests for Review Endpoint
**Estimate:** 1.5 hours  
**Priority:** High

- [x] Post messages with varying quality to a test topic DB
- [x] Call GET /promotions/review and verify ranking order
- [x] Verify filters reduce result set correctly
- [x] Verify promoted messages are excluded from results
- [x] Verify response model matches spec

**Files to create:**
- `tests/test_promotion_review.py`

---

## Risk Mitigation

### High-Risk Tasks
- **Corroboration counting at query time** — may be slow if entity overlap
  query is not indexed. Mitigation: pre-filter to unflagged messages first,
  limit result set, index entity columns.

### Mitigation Strategies
- Start with SQL pre-filtering to minimize the number of messages scored
- If performance is insufficient, add a materialized corroboration count
  column updated by the existing corroboration job

---

## Success Criteria

### Functional
- [x] Agent can discover unflagged messages ranked by promotion likelihood
- [x] Agent can flag individual messages using existing promote endpoint
- [x] Score breakdown is transparent and explainable

### Performance
- [ ] Review query returns within 2 seconds for up to 10,000 messages

### Operational
- [x] No new database tables or migrations required
- [x] No changes to existing promotion pipeline or UI

---

## Estimated Total Effort
**Total:** ~9.5 hours (~1.5 person-days)  
**Critical Path:** Tasks 1.1 → 2.1 → QA.1 → QA.2 (sequential)  
**Parallel Work:** Task 3.1 and 3.2 can run alongside QA tasks