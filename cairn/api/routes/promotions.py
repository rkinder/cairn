# Copyright (C) 2026 Ryan Kinder
#
# This file is part of Cairn.
#
# Cairn is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# Cairn is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for
# more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Cairn. If not, see <https://www.gnu.org/licenses/>.

"""Promotion candidate endpoints (Phase 4).

GET  /promotions                  — List promotion candidates (filterable)
GET  /promotions/{id}             — Retrieve a single candidate
POST /promotions/{id}/promote     — Promote to vault (human-only)
POST /promotions/{id}/dismiss     — Dismiss candidate (human-only)

Human-only actions require:
    X-Human-Reviewer: true
    X-Reviewer-Identity: <analyst identifier>

The ``promote`` action writes a note to the Obsidian vault and syncs it to
the ChromaDB vault-notes collection, then marks the candidate ``promoted``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Literal

import chromadb
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from cairn.api.deps import authenticated_agent, get_couchdb_client, get_db_manager
from cairn.config import get_settings
from cairn.db.connections import DatabaseManager
from cairn.jobs.promotion_scorer import PromotionScorer
from cairn.nlp.entity_extractor import extract
from cairn.sync.chroma_sync import _procedure_doc_id
from cairn.sync.vault_sync import get_vault_collection, upsert_vault_note
from cairn.vault.wikilink_resolver import WikilinkResolver
from cairn.vault.writer import WriteResult, write_note, write_procedure
from cairn.nlp.step_extractor import extract_steps

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/promotions", tags=["promotions"])

# Shared resolver (run-duration cache per process)
_resolver_cache: WikilinkResolver | None = None


def _get_resolver() -> WikilinkResolver:
    global _resolver_cache
    settings = get_settings()
    if _resolver_cache is None:
        _resolver_cache = WikilinkResolver(settings.vault_path)
    return _resolver_cache


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class CandidateResponse(BaseModel):
    id: str
    entity: str
    entity_type: str
    topic_db: str | None
    trigger: str
    status: str
    confidence: float | None
    source_message_ids: list[str]
    narrative: str
    reviewer_id: str | None
    vault_path: str | None
    created_at: str
    updated_at: str


class PromoteRequest(BaseModel):
    narrative: str | None = Field(
        default=None,
        description="Optional narrative override for the vault note ## Summary section.",
    )
    methodology_kind: Literal["sigma", "procedure"] | None = Field(
        default=None,
        description="Optional methodology kind. Use 'procedure' to promote a procedural note.",
    )


class DismissRequest(BaseModel):
    reason: str | None = Field(
        default=None,
        description="Optional reason for dismissal (stored in narrative).",
    )


class ScoreBreakdownResponse(BaseModel):
    """Breakdown of promotion score components."""
    confidence_component: float = Field(description="0.0-0.3")
    corroboration_component: float = Field(description="0.0-0.3")
    entity_density_component: float = Field(description="0.0-0.2")
    age_component: float = Field(description="0.0-0.1")
    tag_component: float = Field(description="0.0-0.1")


class PromotionCandidate(BaseModel):
    """An unflagged message ranked by promotion likelihood."""
    id: str = Field(description="Message ID")
    topic_db: str = Field(description="Topic database slug")
    agent_id: str = Field(description="Agent that posted the message")
    message_type: str = Field(description="Message type (finding, hypothesis, etc.)")
    tags: list[str] = Field(default_factory=list, description="Message tags")
    confidence: float | None = Field(default=None, description="Confidence score if set")
    timestamp: str = Field(description="Message timestamp (ISO8601)")
    body: str = Field(description="Message body for agent evaluation")
    promotion_score: float = Field(description="Computed 0.0-1.0 promotion likelihood")
    score_breakdown: ScoreBreakdownResponse = Field(description="Score component breakdown")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "",
    operation_id="list_promotion_candidates",
    response_model=list[CandidateResponse],
    summary="List promotion candidates",
)
async def list_candidates(
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Filter by status: pending_review | promoted | dismissed",
    ),
    entity_type: str | None = Query(default=None, description="Filter by entity type"),
    trigger: str | None = Query(default=None, description="Filter by trigger type"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _agent: dict = Depends(authenticated_agent),
    db: DatabaseManager = Depends(get_db_manager),
) -> list[CandidateResponse]:
    """List promotion candidates, optionally filtered by status, entity_type, or trigger."""
    conditions = []
    params: list = []

    if status_filter:
        conditions.append("status = ?")
        params.append(status_filter)
    if entity_type:
        conditions.append("entity_type = ?")
        params.append(entity_type)
    if trigger:
        conditions.append("trigger = ?")
        params.append(trigger)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])

    cursor = await db.index_conn.execute(
        f"""
        SELECT id, entity, entity_type, topic_db, trigger, status, confidence,
               source_message_ids, narrative, reviewer_id, vault_path,
               created_at, updated_at
        FROM promotion_candidates
        {where}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        params,
    )
    rows = await cursor.fetchall()
    return [_row_to_candidate(row) for row in rows]


# ---------------------------------------------------------------------------
# Retroactive Promotion Review (Phase 4.3)
# ---------------------------------------------------------------------------

@router.get(
    "/review",
    operation_id="review_promotable_messages",
    response_model=list[PromotionCandidate],
    summary="Review unflagged messages for promotion candidacy",
    description="""
Query unflagged messages from a topic database, compute promotion scores,
and return them ranked by likelihood of promotion.

This endpoint helps agents and analysts discover valuable findings in the
backlog that were not previously flagged for promotion.
""",
)
async def review_promotable(
    topic_db: str = Query(
        ...,
        description="Topic database to scan (e.g., osint, vulnerabilities, aws)",
    ),
    tags: str | None = Query(
        default=None,
        description="Comma-separated tag filter (match any)",
    ),
    min_confidence: float | None = Query(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold",
    ),
    since: str | None = Query(
        default=None,
        description="ISO date — only return messages after this date",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of results (default: 50, max: 200)",
    ),
    _agent: dict = Depends(authenticated_agent),
    db: DatabaseManager = Depends(get_db_manager),
) -> list[PromotionCandidate]:
    """Review unflagged messages for promotion candidacy."""
    # Validate topic_db exists
    if topic_db not in db.known_topics():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic database '{topic_db}' not found",
        )

    # Build SQL query for unflagged messages
    conditions = ["promote = 'none'"]
    params: list = []

    if tags:
        # Match any of the provided tags (JSON array in DB)
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            # Use JSON_EACH to match tags in the JSON array
            tag_conditions = " OR ".join(["tags LIKE ?" for _ in tag_list])
            conditions.append(f"({tag_conditions})")
            for tag in tag_list:
                params.append(f'%"{tag}"%')

    if min_confidence is not None:
        conditions.append("confidence >= ?")
        params.append(min_confidence)

    if since:
        conditions.append("timestamp >= ?")
        params.append(since)

    where_clause = "WHERE " + " AND ".join(conditions)

    # Query unflagged messages from the topic DB
    topic_conn = db.topic_conn(topic_db)
    cursor = await topic_conn.execute(
        f"""
        SELECT id, agent_id, message_type, tags, confidence, timestamp, body
        FROM messages
        {where_clause}
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        params + [limit],
    )
    rows = await cursor.fetchall()

    if not rows:
        return []

    # Compute promotion scores
    scorer = PromotionScorer()
    candidates: list[PromotionCandidate] = []

    # Build entity overlap map for corroboration counting
    # Group by (entity_type, entity_value_lower) -> set of agent_ids
    entity_sightings: dict[tuple[str, str], set[str]] = {}

    # Extract entities from all message bodies and count corroboration
    for row in rows:
        body = row["body"] or ""
        tags_json = row["tags"] or "[]"
        try:
            msg_tags = json.loads(tags_json)
        except (json.JSONDecodeError, TypeError):
            msg_tags = []

        entities = extract(body, tags=msg_tags)
        for entity in entities:
            key = (entity.type, entity.value.lower())
            entity_sightings.setdefault(key, set()).add(row["agent_id"])

    # Score each message
    for row in rows:
        body = row["body"] or ""
        tags_json = row["tags"] or "[]"
        try:
            msg_tags = json.loads(tags_json)
        except (json.JSONDecodeError, TypeError):
            msg_tags = []

        # Extract entities for density count
        entities = extract(body, tags=msg_tags)
        entity_count = len(entities)

        # Count corroborating agents (distinct agents sharing at least one entity)
        corroboration_count = 0
        for entity in entities:
            key = (entity.type, entity.value.lower())
            if key in entity_sightings:
                # Exclude this message's own agent from the count
                other_agents = entity_sightings[key] - {row["agent_id"]}
                corroboration_count = max(corroboration_count, len(other_agents))

        # Build message dict for scorer
        message_dict = {
            "confidence": row["confidence"],
            "tags": msg_tags,
            "timestamp": row["timestamp"],
        }

        score, breakdown = scorer.score(message_dict, corroboration_count, entity_count)

        candidates.append(
            PromotionCandidate(
                id=row["id"],
                topic_db=topic_db,
                agent_id=row["agent_id"],
                message_type=row["message_type"],
                tags=msg_tags,
                confidence=row["confidence"],
                timestamp=row["timestamp"],
                body=body,
                promotion_score=score,
                score_breakdown=ScoreBreakdownResponse(
                    confidence_component=breakdown.confidence_component,
                    corroboration_component=breakdown.corroboration_component,
                    entity_density_component=breakdown.entity_density_component,
                    age_component=breakdown.age_component,
                    tag_component=breakdown.tag_component,
                ),
            )
        )

    # Sort by promotion score descending
    candidates.sort(key=lambda x: x.promotion_score, reverse=True)

    return candidates


@router.get(
    "/{candidate_id}",
    operation_id="get_promotion_candidate",
    response_model=CandidateResponse,
    summary="Retrieve a single promotion candidate",
)
async def get_candidate(
    candidate_id: str,
    _agent: dict = Depends(authenticated_agent),
    db: DatabaseManager = Depends(get_db_manager),
) -> CandidateResponse:
    cursor = await db.index_conn.execute(
        """
        SELECT id, entity, entity_type, topic_db, trigger, status, confidence,
               source_message_ids, narrative, reviewer_id, vault_path,
               created_at, updated_at
        FROM promotion_candidates
        WHERE id = ?
        """,
        (candidate_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
    return _row_to_candidate(row)


@router.post(
    "/{candidate_id}/promote",
    operation_id="promote_candidate",
    response_model=CandidateResponse,
    summary="Promote a candidate to the Obsidian vault (human-only)",
)
async def promote_candidate(
    candidate_id: str,
    body: PromoteRequest = ...,
    x_human_reviewer: Annotated[str | None, Header()] = None,
    x_reviewer_identity: Annotated[str | None, Header()] = None,
    _agent: dict = Depends(authenticated_agent),
    db: DatabaseManager = Depends(get_db_manager),
) -> CandidateResponse:
    """Write a vault note and mark the candidate as promoted.

    Requires ``X-Human-Reviewer: true`` and ``X-Reviewer-Identity`` headers.
    """
    _require_human(x_human_reviewer, x_reviewer_identity)
    reviewer_id = x_reviewer_identity

    cursor = await db.index_conn.execute(
        "SELECT * FROM promotion_candidates WHERE id = ?",
        (candidate_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
    if row["status"] != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot promote a candidate in status '{row['status']}'.",
        )

    settings    = get_settings()
    entity      = row["entity"]
    entity_type = row["entity_type"]
    entity_domain = row["entity_domain"]          # Phase 4.2: IT domain hint or None
    source_ids  = json.loads(row["source_message_ids"] or "[]")
    narrative   = (body.narrative or row["narrative"] or "").strip()
    confidence  = row["confidence"]
    now_iso     = _now_iso()

    # Fetch full message bodies from topic DBs (Bug 001 fix)
    source_findings = await _fetch_source_findings(db, source_ids)

    # Resolve related wikilinks from the entity itself
    resolver      = _get_resolver()
    related_links = _build_related_links(entity, entity_type, source_ids, resolver)

    # Write to vault (disk first, then best-effort CouchDB sync — Phase 4.4)
    couchdb_client = get_couchdb_client()
    try:
        if body.methodology_kind == "procedure":
            steps = extract_steps(narrative)
            low_conf = len(steps) < 2
            write_result = await write_procedure(
                settings.vault_path,
                title=entity[:60],
                steps=steps,
                tags=[],
                narrative=narrative,
                source_message_ids=source_ids,
                promoted_at=now_iso,
                author=reviewer_id,
                severity=None,
                low_confidence=low_conf,
                couchdb_client=couchdb_client,
            )
        else:
            write_result = await write_note(
                settings.vault_path,
                entity=entity,
                entity_type=entity_type,
                narrative=narrative,
                source_message_ids=source_ids,
                confidence=confidence,
                promoted_at=now_iso,
                related_links=related_links,
                domain=entity_domain,
                couchdb_client=couchdb_client,
                source_findings=source_findings,
            )
    except Exception as exc:
        logger.exception("promote_candidate: vault write failed for %s", candidate_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Vault write failed: {exc}",
        )

    vault_rel = write_result.vault_rel
    if not write_result.couchdb_synced and couchdb_client is not None:
        logger.warning(
            "promote_candidate: CouchDB sync failed for %s: %s",
            candidate_id,
            write_result.couchdb_error,
        )

    # Register the new note in the wikilink resolver cache
    resolver.register(entity)

    # Sync to ChromaDB vault-notes collection (best-effort)
    try:
        chroma = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        vault_col = get_vault_collection(chroma, settings.vault_collection)
        if body.methodology_kind == "procedure":
            steps = extract_steps(narrative)
            doc_id = _procedure_doc_id(entity[:60], vault_rel)
            document = "\n".join([entity, narrative] + [f"{i+1}. {s}" for i, s in enumerate(steps)])
            metadata = {
                "kind": "procedure",
                "title": entity[:60],
                "tags": "",
                "vault_path": vault_rel,
                "source_message_ids": ",".join(source_ids),
                "promoted_at": now_iso,
            }
            vault_col.upsert(ids=[doc_id], documents=[document], metadatas=[metadata])
        else:
            upsert_vault_note(
                vault_col,
                vault_path=vault_rel,
                title=entity,
                summary=narrative[:500] if narrative else "",
                entity_type=entity_type,
                confidence=confidence,
                promoted_at=now_iso,
            )
    except Exception:
        logger.warning("promote_candidate: ChromaDB sync failed (non-fatal) for %s", candidate_id)

    # Update promotion_candidates record
    async with db.index() as conn:
        await conn.execute(
            """
            UPDATE promotion_candidates
            SET status = 'promoted', vault_path = ?, reviewer_id = ?,
                narrative = ?, updated_at = ?
            WHERE id = ?
            """,
            (vault_rel, reviewer_id, narrative, now_iso, candidate_id),
        )
        # Also update message_index promote status for source messages
        for msg_id in source_ids:
            await conn.execute(
                "UPDATE message_index SET promote = 'promoted' WHERE id = ?",
                (msg_id,),
            )

    return await _fetch_candidate(db, candidate_id)


@router.post(
    "/{candidate_id}/dismiss",
    operation_id="dismiss_candidate",
    response_model=CandidateResponse,
    summary="Dismiss a promotion candidate (human-only)",
)
async def dismiss_candidate(
    candidate_id: str,
    body: DismissRequest = ...,
    x_human_reviewer: Annotated[str | None, Header()] = None,
    x_reviewer_identity: Annotated[str | None, Header()] = None,
    _agent: dict = Depends(authenticated_agent),
    db: DatabaseManager = Depends(get_db_manager),
) -> CandidateResponse:
    """Mark a candidate as dismissed.

    Requires ``X-Human-Reviewer: true`` and ``X-Reviewer-Identity`` headers.
    """
    _require_human(x_human_reviewer, x_reviewer_identity)
    reviewer_id = x_reviewer_identity

    cursor = await db.index_conn.execute(
        "SELECT status FROM promotion_candidates WHERE id = ?",
        (candidate_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
    if row["status"] != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot dismiss a candidate in status '{row['status']}'.",
        )

    now_iso   = _now_iso()
    narrative = body.reason or ""

    async with db.index() as conn:
        await conn.execute(
            """
            UPDATE promotion_candidates
            SET status = 'dismissed', reviewer_id = ?, narrative = ?, updated_at = ?
            WHERE id = ?
            """,
            (reviewer_id, narrative, now_iso, candidate_id),
        )

    return await _fetch_candidate(db, candidate_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_human(x_human_reviewer: str | None, x_reviewer_identity: str | None) -> None:
    if (x_human_reviewer or "").lower() != "true":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires X-Human-Reviewer: true",
        )
    if not x_reviewer_identity:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires X-Reviewer-Identity header to identify the analyst.",
        )


async def _fetch_candidate(db: DatabaseManager, candidate_id: str) -> CandidateResponse:
    cursor = await db.index_conn.execute(
        """
        SELECT id, entity, entity_type, topic_db, trigger, status, confidence,
               source_message_ids, narrative, reviewer_id, vault_path,
               created_at, updated_at
        FROM promotion_candidates WHERE id = ?
        """,
        (candidate_id,),
    )
    row = await cursor.fetchone()
    return _row_to_candidate(row)


def _row_to_candidate(row) -> CandidateResponse:
    source_ids = json.loads(row["source_message_ids"] or "[]")
    return CandidateResponse(
        id=row["id"],
        entity=row["entity"],
        entity_type=row["entity_type"],
        topic_db=row["topic_db"],
        trigger=row["trigger"],
        status=row["status"],
        confidence=row["confidence"],
        source_message_ids=source_ids,
        narrative=row["narrative"] or "",
        reviewer_id=row["reviewer_id"],
        vault_path=row["vault_path"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _build_related_links(
    entity: str,
    entity_type: str,
    source_ids: list[str],
    resolver: WikilinkResolver,
) -> list[str]:
    """Generate a short list of related wikilinks for the ## Related section."""
    links = []
    # Add entity-type tag link
    tag_map = {
        "ipv4": "ip-address", "ipv6": "ip-address", "fqdn": "hostname",
        "cve": "vulnerability", "technique": "mitre-attack", "actor": "threat-actor",
    }
    tag = tag_map.get(entity_type)
    if tag:
        links.append(resolver.resolve(tag))
    return links


async def _fetch_source_findings(db: DatabaseManager, source_ids: list[str]) -> list[dict]:
    """Fetch full message bodies for source message IDs from their topic DBs.

    Queries message_index to find which topic DB each message lives in, then
    fetches the body from that topic DB.  Missing or unreadable messages are
    silently skipped so a single bad reference never blocks a promotion.
    """
    if not source_ids:
        return []

    # Build reverse map: topic_db_id (UUID) → slug using public DatabaseManager API
    topic_id_to_slug = {db.topic_id(slug): slug for slug in db.known_topics()}

    placeholders = ",".join("?" * len(source_ids))
    cursor = await db.index_conn.execute(
        f"SELECT id, topic_db_id, agent_id, timestamp FROM message_index WHERE id IN ({placeholders})",
        source_ids,
    )
    idx_rows = await cursor.fetchall()

    findings: list[dict] = []
    for idx_row in idx_rows:
        slug = topic_id_to_slug.get(idx_row["topic_db_id"])
        if not slug:
            logger.warning("_fetch_source_findings: unknown topic_db_id for message %s", idx_row["id"])
            continue
        try:
            topic_conn = db.topic_conn(slug)
            msg_cursor = await topic_conn.execute(
                "SELECT body FROM messages WHERE id = ?",
                (idx_row["id"],),
            )
            msg_row = await msg_cursor.fetchone()
            if msg_row and msg_row["body"]:
                findings.append({
                    "agent_id": idx_row["agent_id"],
                    "timestamp": idx_row["timestamp"],
                    "body": msg_row["body"],
                })
        except Exception:
            logger.warning("_fetch_source_findings: could not fetch body for message %s", idx_row["id"])

    return findings


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
