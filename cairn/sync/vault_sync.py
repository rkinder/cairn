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

"""ChromaDB sync for the Obsidian vault-notes collection (Phase 4).

Separate from :mod:`cairn.sync.chroma_sync` (which handles the methodology
collection) so the two collections can evolve independently.

The embedding document is the note title + a short summary — never the full
markdown body.  This keeps the index lightweight while enabling relevant
semantic retrieval.

Public functions
----------------
get_vault_collection(client)
    Get or create the vault-notes ChromaDB collection.

upsert_vault_note(collection, *, vault_path, title, summary, entity_type,
                  confidence, promoted_at)
    Index or update a promoted vault note.

search_vault_notes(collection, query, n)
    Semantic search over indexed vault notes.
"""

from __future__ import annotations

import logging
from typing import Any

import chromadb

logger = logging.getLogger(__name__)

_HNSW_SPACE = "cosine"


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------

def get_vault_collection(
    client: chromadb.HttpClient,
    collection_name: str = "vault-notes",
) -> chromadb.Collection:
    """Get or create the vault-notes ChromaDB collection.

    Args:
        client:          Initialised ChromaDB HTTP client.
        collection_name: Override the default collection name (useful in tests).

    Returns:
        The ChromaDB Collection object.
    """
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": _HNSW_SPACE},
    )


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_vault_note(
    collection: chromadb.Collection,
    *,
    vault_path: str,
    title: str,
    summary: str,
    entity_type: str,
    confidence: float | None,
    promoted_at: str,
) -> None:
    """Index or update a promoted vault note in ChromaDB.

    The chroma document is ``title + "\\n\\n" + summary`` (never the full
    markdown body) so the index stays compact.

    The ChromaDB document ID is derived from *vault_path* so re-promoting the
    same entity overwrites the previous entry rather than creating a duplicate.

    Args:
        collection:   ChromaDB vault-notes collection.
        vault_path:   Relative path of the note file within the vault.
        title:        Note title (the entity's canonical name).
        summary:      Short human-readable summary of the note content.
        entity_type:  One of: ipv4, ipv6, fqdn, cve, technique, actor.
        confidence:   Promotion confidence score, or None.
        promoted_at:  ISO8601 timestamp of the promotion event.
    """
    doc_id = _path_to_chroma_id(vault_path)
    document = f"{title}\n\n{summary}" if summary else title

    metadata: dict[str, Any] = {
        "vault_path":   vault_path,
        "title":        title,
        "entity_type":  entity_type,
        "promoted_at":  promoted_at,
    }
    if confidence is not None:
        metadata["confidence"] = confidence

    collection.upsert(
        ids=[doc_id],
        documents=[document],
        metadatas=[metadata],
    )
    logger.debug("vault_sync: upserted '%s' (id=%s)", title, doc_id)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_vault_notes(
    collection: chromadb.Collection,
    query: str,
    n: int = 5,
) -> list[dict[str, Any]]:
    """Semantic search over indexed vault notes.

    Args:
        collection: ChromaDB vault-notes collection.
        query:      Natural-language search string.
        n:          Maximum number of results to return.

    Returns:
        List of dicts, each containing:
            vault_path, title, entity_type, confidence, promoted_at, score
        Ordered by descending similarity score.
    """
    try:
        results = collection.query(query_texts=[query], n_results=n)
    except Exception:
        logger.exception("vault_sync: ChromaDB query failed")
        return []

    output: list[dict[str, Any]] = []
    ids        = results.get("ids",       [[]])[0]
    metadatas  = results.get("metadatas", [[]])[0]
    distances  = results.get("distances", [[]])[0]

    for _doc_id, meta, dist in zip(ids, metadatas, distances):
        score = max(0.0, 1.0 - dist)
        output.append(
            {
                "vault_path":  meta.get("vault_path",  ""),
                "title":       meta.get("title",       ""),
                "entity_type": meta.get("entity_type", ""),
                "confidence":  meta.get("confidence"),
                "promoted_at": meta.get("promoted_at", ""),
                "score":       score,
            }
        )

    return output


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _path_to_chroma_id(path: str) -> str:
    """Convert a vault-relative path to a stable ChromaDB document ID."""
    return path.replace("/", "__").replace("\\", "__").replace(".", "_")
