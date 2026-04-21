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

"""FastAPI dependency functions shared across route handlers."""

from __future__ import annotations

import json

from fastapi import Depends, HTTPException, Query, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from cairn.api.auth import lookup_agent
from cairn.api.broadcast import MessageBroadcaster
from cairn.config import get_settings
from cairn.db.connections import DatabaseManager
from cairn.vault.couchdb_sync import CouchDBVaultClient

_bearer        = HTTPBearer(auto_error=True)
_bearer_optional = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Infrastructure dependencies
# ---------------------------------------------------------------------------

def get_db_manager(request: Request) -> DatabaseManager:
    """Return the DatabaseManager stored on app.state."""
    return request.app.state.db


def get_broadcaster(request: Request) -> MessageBroadcaster:
    """Return the SSE broadcaster stored on app.state."""
    return request.app.state.broadcaster


# ---------------------------------------------------------------------------
# Authentication dependency
# ---------------------------------------------------------------------------

async def authenticated_agent(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
    db: DatabaseManager = Depends(get_db_manager),
) -> dict:
    """Validate the Bearer token and return the agent record.

    Raises HTTP 401 if the token is missing, invalid, or the agent is inactive.
    """
    agent = await lookup_agent(credentials.credentials, db.index_conn)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return agent


# ---------------------------------------------------------------------------
# Topic database validation dependency
# ---------------------------------------------------------------------------

def valid_topic_db(
    db_name: str,
    db: DatabaseManager = Depends(get_db_manager),
) -> str:
    """Validate that db_name is a known active topic database.

    Raises HTTP 404 if the slug is not registered.
    Returns the slug unchanged on success.
    """
    if db_name not in db.known_topics():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic database '{db_name}' not found or inactive. "
                   f"Known databases: {db.known_topics()}",
        )
    return db_name


async def stream_authenticated_agent(
    db: DatabaseManager = Depends(get_db_manager),
    token: str | None = Query(None, description="API key for EventSource clients that cannot set headers."),
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_optional),
) -> dict:
    """Authenticate an SSE subscriber via Bearer header or ?token= query param.

    Browser EventSource does not support custom headers, so the UI passes the
    API key as a query parameter.  Server-side agents should use the
    Authorization header.
    """
    raw_key = (credentials.credentials if credentials else None) or token
    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: provide a Bearer token or ?token= parameter.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    agent = await lookup_agent(raw_key, db.index_conn)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return agent


# ---------------------------------------------------------------------------
# CouchDB client singleton (Phase 4.4)
# ---------------------------------------------------------------------------

_couchdb_client: CouchDBVaultClient | None = None


def get_couchdb_client() -> CouchDBVaultClient | None:
    """Return the shared CouchDB client, or None if disabled or unconfigured."""
    global _couchdb_client
    settings = get_settings()
    if not settings.couchdb_enabled or not settings.couchdb_user:
        return None
    if _couchdb_client is None:
        _couchdb_client = CouchDBVaultClient(
            url=settings.couchdb_url,
            username=settings.couchdb_user,
            password=settings.couchdb_password,
            database=settings.couchdb_database,
        )
    return _couchdb_client


def _reset_couchdb_client() -> None:
    """Reset the singleton — for use in tests only."""
    global _couchdb_client
    _couchdb_client = None


def agent_can_write(agent: dict, db_name: str) -> None:
    """Raise HTTP 403 if the agent is not allowed to write to db_name.

    An empty allowed_dbs list means the agent may write to all databases.
    """
    allowed = json.loads(agent.get("allowed_dbs", "[]"))
    if allowed and db_name not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Agent '{agent['id']}' is not authorised to write to '{db_name}'.",
        )
