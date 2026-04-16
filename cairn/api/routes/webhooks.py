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

"""Webhook handlers for external service integrations.

POST /webhooks/gitlab — Receives GitLab push events and triggers the
ChromaDB methodology sync job.

Setup in GitLab:
  Project → Settings → Webhooks → URL: http://<cairn-host>/webhooks/gitlab
  Events: Push events
  Secret token: set CAIRN_GITLAB_WEBHOOK_SECRET to the same value
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from cairn.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# POST /webhooks/gitlab
# ---------------------------------------------------------------------------

@router.post(
    "/gitlab",
    operation_id="gitlab_webhook",
    status_code=status.HTTP_202_ACCEPTED,
    summary="GitLab push webhook",
    description=(
        "Receives GitLab push events.  On each push, fetches updated methodology "
        "files from the configured repository directory and upserts their metadata "
        "into ChromaDB for semantic discovery.  Responds immediately with 202 and "
        "processes the sync in the background so GitLab does not time out waiting."
    ),
)
async def gitlab_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_gitlab_token: str | None = Header(None, alias="X-Gitlab-Token"),
    x_gitlab_event: str | None = Header(None, alias="X-Gitlab-Event"),
) -> dict:
    settings = get_settings()

    # Verify the shared secret token if one is configured.
    if settings.gitlab_webhook_secret:
        if x_gitlab_token != settings.gitlab_webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook secret token.",
            )

    payload = await request.json()

    # GitLab sends the event type in both the header and the payload.
    event = x_gitlab_event or payload.get("event_name", "")

    if event not in ("push", "Push Hook"):
        logger.debug("Ignoring non-push GitLab webhook event: %s", event)
        return {"status": "ignored", "event": event}

    background_tasks.add_task(_sync_push_event, payload)
    return {"status": "accepted", "event": event}


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def _sync_push_event(payload: dict) -> None:
    """Sync changed methodology files to ChromaDB after a GitLab push.

    Called as a BackgroundTask so the HTTP response is returned immediately.
    Any exceptions are logged but do not affect the 202 response already sent.
    """
    settings = get_settings()

    # 'after' is the HEAD SHA after the push.  All-zeros means branch deletion.
    after_sha: str = payload.get("after", "")
    if not after_sha or after_sha == "0" * 40:
        logger.info("Skipping ChromaDB sync: branch deleted or empty push")
        return

    methodology_dir = settings.gitlab_methodology_dir

    # Collect unique .yml paths that were added or modified in this push.
    changed: set[str] = set()
    for commit in payload.get("commits", []):
        for path in commit.get("added", []) + commit.get("modified", []):
            if path.startswith(methodology_dir) and path.endswith(".yml"):
                changed.add(path)

    if not changed:
        logger.debug("No methodology files changed in this push (sha=%s…)", after_sha[:8])
        return

    logger.info(
        "Syncing %d methodology file(s) to ChromaDB (sha=%s…)",
        len(changed),
        after_sha[:8],
    )

    try:
        import chromadb
        from cairn.integrations.gitlab import GitLabClient
        from cairn.sync.chroma_sync import get_collection, upsert_methodology

        chroma_client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
        )
        collection = get_collection(chroma_client)

        async with GitLabClient.from_settings() as gl:
            for path in changed:
                try:
                    mf = await gl.fetch_methodology(path, after_sha)
                    upsert_methodology(
                        collection,
                        gitlab_path=mf.path,
                        commit_sha=mf.commit_sha,
                        title=mf.title,
                        description=mf.description,
                        tags=mf.tags,
                        status=mf.status,
                    )
                except Exception:
                    logger.exception("Failed to sync methodology file: %s", path)

    except Exception:
        logger.exception("ChromaDB sync failed for push sha=%s…", after_sha[:8])
