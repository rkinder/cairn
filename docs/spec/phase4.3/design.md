# Phase 4.3 — Retroactive Promotion Review Design

## Architecture Overview

One new read-only endpoint (`GET /promotions/review`) that queries unflagged
messages from a topic database, computes a promotion score for each, and
returns them ranked. No new database tables. No new write endpoints — flagging
uses the existing `POST /messages/{id}/promote`.

```
Agent                     Cairn API                    SQLite
  │                          │                           │
  │ GET /promotions/review   │                           │
  │  ?topic_db=osint         │                           │
  │─────────────────────────►│                           │
  │                          │  SELECT unflagged msgs    │
  │                          │──────────────────────────►│
  │                          │◄──────────────────────────│
  │                          │                           │
  │                          │  compute scores           │
  │                          │  (confidence, corr,       │
  │                          │   entities, age, tags)    │
  │                          │                           │
  │  ranked candidates       │                           │
  │◄─────────────────────────│                           │
  │                          │                           │
  │ POST /messages/{id}/     │                           │
  │   promote?db=osint       │                           │
  │─────────────────────────►│  UPDATE promote=candidate │
  │                          │──────────────────────────►│
```

---

## Endpoint

### GET /promotions/review

**Route:** `cairn/api/routes/promotions.py`  
**Auth:** Required

**Query parameters:**

| Parameter | Required | Type | Description |
|---|---|---|---|
| `topic_db` | yes | string | Topic database to scan |
| `tags` | no | string | Comma-separated tag filter (match any) |
| `min_confidence` | no | float | Minimum confidence threshold |
| `since` | no | string | ISO date — only messages after this date |
| `limit` | no | int | Max results (default: 50, max: 200) |

**Response model:**

```python
class PromotionCandidate(BaseModel):
    id: str
    topic_db: str
    agent_id: str
    message_type: str
    tags: list[str]
    confidence: float | None
    timestamp: str
    body: str                    # message body for agent evaluation
    promotion_score: float       # computed 0.0–1.0
    score_breakdown: ScoreBreakdown

class ScoreBreakdown(BaseModel):
    confidence_component: float   # 0.0–0.3
    corroboration_component: float # 0.0–0.3
    entity_density_component: float # 0.0–0.2
    age_component: float          # 0.0–0.1
    tag_component: float          # 0.0–0.1
```

Returning the breakdown lets the agent (and human reviewers) understand
**why** a message scored high or low, not just the final number.

---

## Promotion Score Computation

### Core Class

```python
# cairn/jobs/promotion_scorer.py

class PromotionScorer:
    """Compute promotion scores for unflagged messages."""

    WEIGHTS = {
        "confidence": 0.3,
        "corroboration": 0.3,
        "entity_density": 0.2,
        "age": 0.1,
        "tags": 0.1,
    }

    def score(self, message: dict, corroboration_count: int, entity_count: int) -> tuple[float, dict]:
        """Return (total_score, breakdown_dict)."""
```

### Component Calculations

**Confidence (weight 0.3):**
- Direct use of the `confidence` field value (0.0–1.0)
- Missing confidence → 0.0

**Corroboration (weight 0.3):**
- Count of distinct agents that posted messages sharing at least one
  extracted entity with this message, within the corroboration window
- Normalized: `min(count / 3, 1.0)` — 3+ corroborating agents = max score

**Entity density (weight 0.2):**
- Run entity extractor regex patterns against message body
- Normalized: `min(entity_count / 5, 1.0)` — 5+ entities = max score

**Age (weight 0.1):**
- Older confirmed findings have proven durability
- `min(age_days / 30, 1.0)` — 30+ days old = max score
- Only contributes positively if confidence >= 0.5 (speculative old messages don't get age credit)

**Tag count (weight 0.1):**
- `min(len(tags) / 5, 1.0)` — 5+ tags = max score

### Implementation Notes

- Score is computed at query time, not stored
- Entity extraction runs per-message using the existing `entity_extractor.py`
- Corroboration count reuses logic from `cairn/jobs/corroboration.py`
- For performance, the query pre-filters to `promote = 'none'` and applies
  SQL-level filters (tags, confidence, date) before scoring in Python

---

## Integration Points

### In existing promotions route

```python
# cairn/api/routes/promotions.py — add new endpoint

@router.get(
    "/review",
    operation_id="review_promotable_messages",
    response_model=list[PromotionCandidate],
)
async def review_promotable(
    topic_db: str = Query(...),
    tags: str | None = Query(None),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
    since: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    agent: dict = Depends(authenticated_agent),
    db: DatabaseManager = Depends(get_db_manager),
):
    # 1. Query unflagged messages from topic DB
    # 2. Compute promotion score for each
    # 3. Sort by score descending
    # 4. Return top N
```

### Entity extractor reuse

```python
from cairn.nlp.entity_extractor import extract_entities

entities = extract_entities(message_body)
entity_count = len(entities)
```

### Corroboration count

```python
# Reuse entity overlap query from corroboration job
# Count distinct agent_ids that share entities with this message
```

---

## Files to Create

| File | Purpose |
|---|---|
| `cairn/jobs/promotion_scorer.py` | Score computation logic |

## Files to Modify

| File | Change |
|---|---|
| `cairn/api/routes/promotions.py` | Add `GET /promotions/review` endpoint |

---

## Testing Strategy

### Unit Tests
- Score computation with known inputs → expected outputs
- Each component in isolation (confidence, corroboration, entity, age, tags)
- Edge cases: missing confidence, zero entities, brand new message, very old message

### Integration Tests
- Post several messages with varying quality, query review endpoint, verify ranking order
- Verify SQL filters (tags, min_confidence, since) reduce result set correctly
- Verify already-promoted messages are excluded

---

## Performance Considerations

- Entity extraction is regex-based and fast (~1ms per message)
- Corroboration counting requires a join against message_index — indexed on agent_id and tags
- For databases with >1000 unflagged messages, the SQL pre-filter is critical to avoid scoring everything
- Limit default of 50 keeps response size manageable
