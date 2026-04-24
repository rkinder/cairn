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

"""Methodology endpoints.

GET  /methodologies/search              — Semantic search via ChromaDB
POST /methodologies/executions          — Record that an agent ran a methodology
GET  /methodologies/executions/{id}     — Retrieve an execution record
PATCH /methodologies/executions/{id}/status — State machine transitions

Validation state machine
    proposed → peer_reviewed   (any authenticated agent)
    peer_reviewed → proposed   (any authenticated agent, reverts review)
    peer_reviewed → validated  (human only: X-Human-Reviewer: true + X-Reviewer-Identity)
    any → deprecated           (human only: same headers)

Design constraint: methodology text is NEVER stored in this API or the
database.  Only gitlab_path + commit_sha are persisted; content lives in
GitLab and is fetched on demand via the GitLab API.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from cairn.api.deps import authenticated_agent, get_broadcaster, get_db_manager
from cairn.config import get_settings
from cairn.db.connections import DatabaseManager
from cairn.db.ids import new_id
from cairn.api.broadcast import MessageBroadcaster

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/methodologies", tags=["methodologies"])


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

# Maps current_status → set of reachable next statuses for any agent.
_AGENT_TRANSITIONS: dict[str, set[str]] = {
    "proposed":      {"peer_reviewed"},
    "peer_reviewed": {"proposed"},
    "validated":     set(),
    "deprecated":    set(),
}

# Additional transitions that require human reviewer headers.
_HUMAN_TRANSITIONS: dict[str, set[str]] = {
    "proposed":      {"deprecated"},
    "peer_reviewed": {"validated", "deprecated"},
    "validated":     {"deprecated"},
    "deprecated":    set(),
}

# The complete set of statuses that require a human reviewer regardless of from-state.
_HUMAN_ONLY_TO: frozenset[str] = frozenset({"validated", "deprecated"})

_ALL_STATUSES: frozenset[str] = frozenset(
    {"proposed", "peer_reviewed", "validated", "deprecated"}
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class MethodologySearchResult(BaseModel):
    gitlab_path: str
    commit_sha:  str
    title:       str
    tags:        list[str]
    status:      str
    kind:        str = Field(default="sigma")
    score:       float = Field(..., ge=0.0, le=1.0, description="Similarity score [0, 1].")


class SubmitMethodologyRequest(BaseModel):
    path: str = Field(..., description="File path within the methodology repo (e.g. sigma/discovery/rule.yml)")
    content: str = Field(..., description="Raw YAML content of the methodology file")
    commit_message: str = Field("", description="Git commit message. Auto-generated if empty.")
    branch: str = Field("main", description="Target branch in the methodology repo.")


class SubmitMethodologyResponse(BaseModel):
    path: str
    commit_sha: str
    action: str
    agent_id: str
    announcement_id: str | None = None


class CreateExecutionRequest(BaseModel):
    methodology_id:    str   = Field(..., description="Logical ID from the Sigma 'name' field or derived from path.")
    gitlab_path:       str   = Field(..., description="Path to the .yml file within the GitLab repo.")
    commit_sha:        str   = Field(..., description="Exact commit SHA that was executed.")
    parent_version:    str | None = Field(None, description="Commit SHA of the parent methodology version (lineage).")
    result_message_ids: list[str] = Field(default_factory=list, description="Blackboard message IDs produced by this run.")


class ExecutionResponse(BaseModel):
    id:                 str
    methodology_id:     str
    gitlab_path:        str
    commit_sha:         str
    status:             str
    parent_version:     str | None
    agent_id:           str
    result_message_ids: list[str]
    reviewer_id:        str | None
    notes:              str
    created_at:         str
    updated_at:         str


class StatusUpdateRequest(BaseModel):
    status: str = Field(
        ...,
        description="Target status: proposed | peer_reviewed | validated | deprecated",
    )
    notes: str = Field(
        default="",
        description="Optional notes explaining this status transition.",
    )


# ---------------------------------------------------------------------------
# POST /methodologies — submit a new methodology to GitLab
# ---------------------------------------------------------------------------

@router.post(
    "",
    operation_id="submit_methodology",
    status_code=status.HTTP_201_CREATED,
    response_model=SubmitMethodologyResponse,
    summary="Submit a methodology to GitLab",
    description=(
        "Validates the submitted YAML as a Sigma rule (if path starts with sigma/), "
        "commits it to the methodology GitLab repo, and posts a blackboard "
        "announcement so all agents are notified."
    ),
)
async def submit_methodology(
    body: SubmitMethodologyRequest,
    agent: Annotated[dict, Depends(authenticated_agent)],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
    broadcaster: Annotated[MessageBroadcaster, Depends(get_broadcaster)],
) -> SubmitMethodologyResponse:
    from cairn.ingest.sigma import SigmaValidationError, validate_sigma_rule
    from cairn.integrations.gitlab import GitLabClient
    from cairn.ingest.parser import parse_message
    from cairn.ingest.writer import write_message

    # Validate Sigma rules under sigma/ path.
    sigma_meta = None
    if body.path.startswith("sigma/"):
        try:
            sigma_meta = validate_sigma_rule(body.content)
        except SigmaValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Sigma validation failed: {'; '.join(exc.errors)}",
            ) from exc

    # Commit to GitLab.
    commit_msg = body.commit_message or f"Add {body.path} via Cairn API (agent: {agent['id']})"
    author_name = agent.get("display_name", agent["id"])

    try:
        async with GitLabClient.from_settings() as gl:
            result = await gl.create_or_update_file(
                file_path=body.path,
                content=body.content,
                commit_message=commit_msg,
                branch=body.branch,
                author_name=f"{author_name} via Cairn",
                author_email="cairn@noreply",
            )
            commit_sha = await gl.get_latest_commit_sha(body.branch)
    except Exception as exc:
        logger.exception("GitLab commit failed for %s", body.path)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GitLab commit failed: {exc}",
        ) from exc

    # Determine action.
    action = "updated" if result.get("action") == "updated" else "created"

    # Post blackboard announcement.
    announcement_id = None
    title = sigma_meta.get("title", body.path) if sigma_meta else body.path
    description = sigma_meta.get("description", "") if sigma_meta else ""
    rule_tags = sigma_meta.get("tags", []) if sigma_meta else []
    rule_status = sigma_meta.get("status", "experimental") if sigma_meta else "experimental"

    now = datetime.now(tz=timezone.utc).isoformat()
    tags_str = ", ".join(["methodology"] + rule_tags)
    raw_content = (
        f"---\n"
        f"agent_id: {agent['id']}\n"
        f"timestamp: {now}\n"
        f"message_type: methodology_ref\n"
        f"tags: [{tags_str}]\n"
        f"confidence: 1.0\n"
        f"methodology_sha: {commit_sha}\n"
        f"---\n\n"
        f"New methodology {action}: **{title}**\n\n"
        f"Path: `{body.path}`\n"
        f"Status: {rule_status}\n"
        f"Author: {agent['id']}\n"
    )
    if description:
        raw_content += f"\n{description}\n"

    try:
        msg_id = new_id()
        record = parse_message(raw_content, msg_id)
        # Post to first available topic DB (osint as default).
        topic = "osint"
        await write_message(record, topic, db)
        await broadcaster.broadcast({
            "id":           record.id,
            "topic_db":     topic,
            "agent_id":     record.agent_id,
            "thread_id":    record.thread_id,
            "message_type": record.message_type.value,
            "tags":         record.tags,
            "confidence":   record.confidence,
            "timestamp":    record.timestamp.isoformat(),
            "ingested_at":  record.ingested_at.isoformat(),
        })
        announcement_id = record.id
    except Exception:
        logger.exception("Failed to post methodology announcement — commit succeeded")

    logger.info(
        "Methodology %s committed to GitLab by agent '%s' (sha=%s)",
        body.path, agent["id"], commit_sha[:8],
    )

    return SubmitMethodologyResponse(
        path=body.path,
        commit_sha=commit_sha,
        action=action,
        agent_id=agent["id"],
        announcement_id=announcement_id,
    )


# ---------------------------------------------------------------------------
# GET /methodologies/search
# ---------------------------------------------------------------------------

@router.get(
    "/search",
    operation_id="search_methodologies",
    response_model=list[MethodologySearchResult],
    summary="Semantic methodology search",
    description=(
        "Query the ChromaDB methodology collection using natural language. "
        "Returns ranked results with gitlab_path, commit_sha, and metadata. "
        "No methodology text is included in the response — "
        "fetch full content from GitLab using the returned path + sha."
    ),
)
async def search_methodologies_endpoint(
    _agent: Annotated[dict, Depends(authenticated_agent)],
    q: Annotated[str, Query(min_length=1, description="Natural-language search query.")],
    n: Annotated[int, Query(ge=1, le=50, description="Number of results to return.")] = 10,
    kind: Annotated[Literal["sigma", "procedure", "any"] | None, Query(description="Optional methodology kind filter.")] = None,
) -> list[MethodologySearchResult]:
    settings = get_settings()
    try:
        import chromadb
        from cairn.sync.chroma_sync import get_collection, search_methodologies

        client     = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        collection = get_collection(client)
        where = {"kind": kind} if (kind and kind != "any") else None
        results    = search_methodologies(collection, q, n=n, where=where)
    except Exception as exc:
        logger.exception("ChromaDB methodology search failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Methodology search unavailable: {exc}",
        ) from exc

    return [
        MethodologySearchResult(
            gitlab_path=r["gitlab_path"],
            commit_sha=r["commit_sha"],
            title=r["title"],
            tags=r["tags"],
            status=r["status"],
            kind=r.get("kind", "sigma"),
            score=r["score"],
        )
        for r in results
    ]


# ---------------------------------------------------------------------------
# POST /methodologies/executions
# ---------------------------------------------------------------------------

@router.post(
    "/executions",
    operation_id="create_methodology_execution",
    status_code=status.HTTP_201_CREATED,
    response_model=ExecutionResponse,
    summary="Record a methodology execution",
    description=(
        "Called by an agent after running a methodology from the GitLab repo. "
        "Creates a methodology_execution record in index.db with status='proposed'. "
        "The record then progresses through the review state machine via PATCH."
    ),
)
async def create_execution(
    body: CreateExecutionRequest,
    agent: Annotated[dict, Depends(authenticated_agent)],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
) -> ExecutionResponse:
    execution_id = new_id()
    now = datetime.now(tz=timezone.utc).isoformat()
    result_ids_json = json.dumps(body.result_message_ids)

    async with db.index() as conn:
        await conn.execute(
            """
            INSERT INTO methodology_executions
                (id, methodology_id, gitlab_path, commit_sha, status,
                 parent_version, agent_id, result_message_ids,
                 reviewer_id, notes, created_at, updated_at, ext)
            VALUES
                (:id, :methodology_id, :gitlab_path, :commit_sha, 'proposed',
                 :parent_version, :agent_id, :result_message_ids,
                 NULL, '', :now, :now, '{}')
            """,
            {
                "id":                 execution_id,
                "methodology_id":     body.methodology_id,
                "gitlab_path":        body.gitlab_path,
                "commit_sha":         body.commit_sha,
                "parent_version":     body.parent_version,
                "agent_id":           agent["id"],
                "result_message_ids": result_ids_json,
                "now":                now,
            },
        )

    logger.info(
        "Methodology execution %s recorded by agent '%s' (path=%s sha=%s…)",
        execution_id,
        agent["id"],
        body.gitlab_path,
        body.commit_sha[:8],
    )

    return ExecutionResponse(
        id=execution_id,
        methodology_id=body.methodology_id,
        gitlab_path=body.gitlab_path,
        commit_sha=body.commit_sha,
        status="proposed",
        parent_version=body.parent_version,
        agent_id=agent["id"],
        result_message_ids=body.result_message_ids,
        reviewer_id=None,
        notes="",
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# GET /methodologies/executions/{id}
# ---------------------------------------------------------------------------

@router.get(
    "/executions/{execution_id}",
    operation_id="get_methodology_execution",
    response_model=ExecutionResponse,
    summary="Retrieve a methodology execution record",
)
async def get_execution(
    execution_id: str,
    _agent: Annotated[dict, Depends(authenticated_agent)],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
) -> ExecutionResponse:
    cursor = await db.index_conn.execute(
        "SELECT * FROM methodology_executions WHERE id = :id",
        {"id": execution_id},
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Methodology execution '{execution_id}' not found.",
        )
    return _row_to_response(row)


# ---------------------------------------------------------------------------
# PATCH /methodologies/executions/{id}/status
# ---------------------------------------------------------------------------

@router.patch(
    "/executions/{execution_id}/status",
    operation_id="update_methodology_status",
    response_model=ExecutionResponse,
    summary="Advance methodology execution status",
    description=(
        "Transitions the status of a methodology execution record through the "
        "validation state machine:\n\n"
        "```\n"
        "proposed → peer_reviewed   (any authenticated agent)\n"
        "peer_reviewed → proposed   (any authenticated agent)\n"
        "peer_reviewed → validated  (human: X-Human-Reviewer: true + X-Reviewer-Identity)\n"
        "any → deprecated           (human: X-Human-Reviewer: true + X-Reviewer-Identity)\n"
        "```\n\n"
        "Transitions to `validated` or `deprecated` require the "
        "`X-Human-Reviewer: true` and `X-Reviewer-Identity` headers. "
        "Agent tokens alone cannot reach these states."
    ),
)
async def update_status(
    execution_id: str,
    body: StatusUpdateRequest,
    agent: Annotated[dict, Depends(authenticated_agent)],
    db: Annotated[DatabaseManager, Depends(get_db_manager)],
    x_human_reviewer: str | None = Header(None, alias="X-Human-Reviewer"),
    x_reviewer_identity: str | None = Header(None, alias="X-Reviewer-Identity"),
) -> ExecutionResponse:
    target = body.status

    if target not in _ALL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown status '{target}'. "
                f"Valid values: {sorted(_ALL_STATUSES)}"
            ),
        )

    # Fetch the execution record.
    cursor = await db.index_conn.execute(
        "SELECT * FROM methodology_executions WHERE id = :id",
        {"id": execution_id},
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Methodology execution '{execution_id}' not found.",
        )

    current = row["status"]

    # Determine whether this transition is allowed.
    is_human = (x_human_reviewer == "true")
    allowed_by_agent  = target in _AGENT_TRANSITIONS.get(current, set())
    allowed_by_human  = target in _HUMAN_TRANSITIONS.get(current, set())

    if not (allowed_by_agent or (is_human and allowed_by_human)):
        if target in _HUMAN_ONLY_TO and not is_human:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Transition to '{target}' requires a human reviewer. "
                    "Set X-Human-Reviewer: true and X-Reviewer-Identity headers."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid transition: '{current}' → '{target}'. "
                f"Agent-allowed next states from '{current}': "
                f"{sorted(_AGENT_TRANSITIONS.get(current, set()))}. "
                f"Human-allowed next states: "
                f"{sorted(_HUMAN_TRANSITIONS.get(current, set()))}."
            ),
        )

    # Human-only transitions also require the reviewer identity header.
    if is_human and (allowed_by_human and not allowed_by_agent):
        if not x_reviewer_identity:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "X-Reviewer-Identity header is required when "
                    "X-Human-Reviewer: true is set."
                ),
            )

    now = datetime.now(tz=timezone.utc).isoformat()
    reviewer_id = x_reviewer_identity if (is_human and target in _HUMAN_ONLY_TO) else None

    async with db.index() as conn:
        await conn.execute(
            """
            UPDATE methodology_executions
               SET status      = :status,
                   notes       = CASE WHEN :notes != '' THEN :notes ELSE notes END,
                   reviewer_id = COALESCE(:reviewer_id, reviewer_id),
                   updated_at  = :updated_at
             WHERE id = :id
            """,
            {
                "status":      target,
                "notes":       body.notes,
                "reviewer_id": reviewer_id,
                "updated_at":  now,
                "id":          execution_id,
            },
        )

    logger.info(
        "Methodology execution %s: %s → %s (agent=%s%s)",
        execution_id,
        current,
        target,
        agent["id"],
        f", reviewer={reviewer_id}" if reviewer_id else "",
    )

    # Re-read to return the final state.
    cursor = await db.index_conn.execute(
        "SELECT * FROM methodology_executions WHERE id = :id",
        {"id": execution_id},
    )
    updated_row = await cursor.fetchone()
    return _row_to_response(updated_row)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_response(row) -> ExecutionResponse:
    return ExecutionResponse(
        id=row["id"],
        methodology_id=row["methodology_id"],
        gitlab_path=row["gitlab_path"],
        commit_sha=row["commit_sha"],
        status=row["status"],
        parent_version=row["parent_version"],
        agent_id=row["agent_id"],
        result_message_ids=json.loads(row["result_message_ids"] or "[]"),
        reviewer_id=row["reviewer_id"],
        notes=row["notes"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
