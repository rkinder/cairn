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

"""Vault search endpoint (Phase 4).

GET /vault/search?q=<query>&n=<int>

Semantic search over the ChromaDB vault-notes collection.  Returns ranked
vault note references — vault_path, title, entity_type, confidence, score.
Full note content is never returned; fetch the file from the vault path when
you need the narrative.
"""

from __future__ import annotations

import logging

import chromadb
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from cairn.api.deps import authenticated_agent, get_db_manager
from cairn.config import get_settings
from cairn.db.connections import DatabaseManager
from cairn.sync.vault_sync import get_vault_collection, search_vault_notes

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vault", tags=["vault"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class VaultNoteResult(BaseModel):
    vault_path:  str
    title:       str
    entity_type: str
    confidence:  float | None
    promoted_at: str
    score:       float = 0.0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "/search",
    operation_id="search_vault_notes",
    response_model=list[VaultNoteResult],
    summary="Semantic search over promoted vault notes",
)
async def search_vault(
    q: str = Query(description="Natural-language search query"),
    n: int = Query(default=5, ge=1, le=50, description="Maximum results to return"),
    _agent: dict = Depends(authenticated_agent),
    _db: DatabaseManager = Depends(get_db_manager),
) -> list[VaultNoteResult]:
    """Query the ChromaDB vault-notes collection.

    Returns ranked results ordered by descending similarity score.  Only the
    vault path and metadata are returned; the full note lives in the vault
    directory itself.

    Returns 503 if ChromaDB is unreachable.
    """
    settings = get_settings()
    try:
        client     = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        collection = get_vault_collection(client, settings.vault_collection)
        results    = search_vault_notes(collection, q, n)
    except Exception as exc:
        logger.warning("vault/search: ChromaDB unavailable — %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ChromaDB vault-notes collection is unreachable.",
        )

    return [
        VaultNoteResult(
            vault_path=item["vault_path"],
            title=item["title"],
            entity_type=item["entity_type"],
            confidence=item.get("confidence"),
            promoted_at=item["promoted_at"],
            score=item["score"],
        )
        for item in results
    ]
