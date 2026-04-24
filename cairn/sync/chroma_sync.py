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
# FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Cairn. If not, see <https://www.gnu.org/licenses/>.

"""ChromaDB synchronisation helpers for methodology semantic discovery.

Triggered by GitLab push webhooks: fetches updated methodology files from
GitLab (metadata + description only — never full text) and upserts them into
the ChromaDB 'methodologies' collection so agents can discover relevant
methodologies via natural-language queries.

Discovery path:  ChromaDB (semantic search)   → GET /methodologies/search
Retrieval path:  GitLab API (full content)     → GitLabClient.get_file_at_sha()
Execution record: SQLite                       → methodology_executions table

Usage:
    from cairn.sync.chroma_sync import get_collection, upsert_methodology, search_methodologies

    client = chromadb.HttpClient(host="chromadb", port=8000)
    col    = get_collection(client)
    upsert_methodology(col, gitlab_path="...", commit_sha="...", ...)
    results = search_methodologies(col, "lateral movement named pipe", n=10)
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import chromadb
import yaml

from cairn.models.methodology import ProcedureMethodology

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "methodologies"


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------

def get_chroma_client(host: str, port: int) -> chromadb.HttpClient:
    """Return an HTTP client connected to the ChromaDB server."""
    return chromadb.HttpClient(host=host, port=port)


def get_collection(client: chromadb.HttpClient) -> chromadb.Collection:
    """Return the methodologies collection, creating it if absent.

    Uses cosine similarity space so query scores map naturally to [0, 1]
    after the 1 - distance transform applied in search_methodologies().
    """
    return client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_methodology(
    collection: chromadb.Collection,
    *,
    gitlab_path: str,
    commit_sha: str,
    title: str,
    description: str,
    tags: list[str],
    status: str,
) -> None:
    """Upsert a methodology document into the ChromaDB collection.

    The embedding is computed from title + description (what an agent would
    search for).  Full methodology text is NEVER stored here — only the
    gitlab_path + commit_sha are kept as metadata so callers can retrieve
    the exact content on demand from GitLab.

    Args:
        collection:  ChromaDB collection (from get_collection()).
        gitlab_path: Path to the .yml file within the GitLab repository.
        commit_sha:  The commit SHA at which the metadata was read.
        title:       Sigma rule title.
        description: Sigma rule description.
        tags:        Sigma rule tags.
        status:      Sigma rule status (proposed/stable/deprecated…).
    """
    doc_id   = _path_to_chroma_id(gitlab_path)
    document = f"{title}\n\n{description}".strip() or gitlab_path
    metadata: dict[str, Any] = {
        "gitlab_path": gitlab_path,
        "commit_sha":  commit_sha,
        "tags":        ",".join(tags),
        "status":      status,
        "title":       title,
    }
    collection.upsert(
        ids=[doc_id],
        documents=[document],
        metadatas=[metadata],
    )
    logger.info(
        "ChromaDB upsert: %s (sha=%s…)", gitlab_path, commit_sha[:8]
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_methodologies(
    collection: chromadb.Collection,
    query: str,
    n: int = 10,
    where: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Semantic search over the methodology collection.

    Returns ranked results with gitlab_path, commit_sha, title, tags, status,
    and a similarity score in [0, 1] (higher = more similar).

    Full methodology text is never returned — only the path + SHA the caller
    needs to fetch and decide whether to use it.

    Args:
        collection: ChromaDB collection (from get_collection()).
        query:      Natural-language description of the investigation need.
        n:          Maximum number of results.

    Returns:
        List of result dicts, ordered by descending similarity score.
    """
    count = collection.count()
    if count == 0:
        return []

    query_kwargs: dict[str, Any] = {
        "query_texts": [query],
        "n_results": min(n, count),
        "include": ["metadatas", "distances"],
    }
    if where is not None:
        query_kwargs["where"] = where

    results = collection.query(**query_kwargs)

    ids        = results.get("ids",        [[]])[0]
    metadatas  = results.get("metadatas",  [[]])[0]
    distances  = results.get("distances",  [[]])[0]

    output: list[dict[str, Any]] = []
    for _, meta, dist in zip(ids, metadatas, distances):
        # With hnsw:space=cosine, distance = 1 - cosine_similarity ∈ [0, 2].
        # Map to a [0, 1] similarity score: score = 1 - clamp(distance, 0, 1).
        score = round(max(0.0, 1.0 - float(dist)), 4)
        output.append({
            "gitlab_path": meta.get("gitlab_path", ""),
            "commit_sha":  meta.get("commit_sha",  ""),
            "title":       meta.get("title",       ""),
            "tags":        [t for t in meta.get("tags", "").split(",") if t],
            "status":      meta.get("status",      ""),
            "score":       score,
            "kind":        meta.get("kind", "sigma"),
        })
    return output


def sync_procedures(
    collection: chromadb.Collection,
    procedures_dir: Path,
) -> tuple[int, int]:
    if not procedures_dir.exists() or not procedures_dir.is_dir():
        return (0, 0)

    synced = 0
    failed = 0

    for procedure_file in sorted(procedures_dir.rglob("*.procedure.yml")):
        try:
            payload = yaml.safe_load(procedure_file.read_text(encoding="utf-8"))
            model = ProcedureMethodology.model_validate(payload)
            doc_id = _procedure_doc_id(model.title, str(procedure_file))
            description = model.description or ""
            numbered_steps = "\n".join(
                f"{i + 1}. {step}" for i, step in enumerate(model.steps)
            )
            document = f"{model.title}\n\n{description}\n\n{numbered_steps}".strip()
            metadata: dict[str, Any] = {
                "kind": "procedure",
                "title": model.title,
                "tags": ",".join(model.tags),
                "author": model.author or "",
                "severity": model.severity or "",
                "gitlab_path": str(procedure_file),
                "commit_sha": "",
                "status": "",
            }
            collection.upsert(ids=[doc_id], documents=[document], metadatas=[metadata])
            synced += 1
        except Exception as exc:
            failed += 1
            logger.warning("Failed to sync procedure %s: %s", procedure_file, exc)

    logger.info("Synced %d procedures; %d failed", synced, failed)
    return (synced, failed)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _procedure_doc_id(title: str, filepath: str) -> str:
    raw = f"{title}\x00{filepath}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _path_to_chroma_id(path: str) -> str:
    """Derive a stable, URL-safe ChromaDB document ID from a file path."""
    # Replace path separators and dots to produce a valid ChromaDB ID.
    return path.replace("/", "__").replace(".", "_").strip("_")
