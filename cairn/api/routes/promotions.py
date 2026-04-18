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
from typing import Annotated

import chromadb
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from cairn.api.deps import authenticated_agent, get_db_manager
from cairn.config import get_settings
from cairn.db.connections import DatabaseManager
from cairn.nlp.entity_extractor import extract
from cairn.sync.vault_sync import get_vault_collection, upsert_vault_note
from cairn.vault.wikilink_resolver import WikilinkResolver
from cairn.vault.writer import write_note

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


class DismissRequest(BaseModel):
    reason: str | None = Field(
        default=None,
        description="Optional reason for dismissal (stored in narrative).",
    )


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
        SELECT id, entity, entity_type, trigger, status, confidence,
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
        SELECT id, entity, entity_type, trigger, status, confidence,
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

    # Resolve related wikilinks from the entity itself
    resolver      = _get_resolver()
    related_links = _build_related_links(entity, entity_type, source_ids, resolver)

    # Write to vault (Phase 4.2: pass domain for domain-aware subdirectory routing)
    try:
        vault_rel = write_note(
            settings.vault_path,
            entity=entity,
            entity_type=entity_type,
            narrative=narrative,
            source_message_ids=source_ids,
            confidence=confidence,
            promoted_at=now_iso,
            related_links=related_links,
            domain=entity_domain,
        )
    except Exception as exc:
        logger.exception("promote_candidate: vault write failed for %s", candidate_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Vault write failed: {exc}",
        )

    # Register the new note in the wikilink resolver cache
    resolver.register(entity)

    # Sync to ChromaDB vault-notes collection (best-effort)
    try:
        chroma = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        vault_col = get_vault_collection(chroma, settings.vault_collection)
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
        SELECT id, entity, entity_type, trigger, status, confidence,
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
