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

"""Application settings loaded from environment variables or a .env file.

All variables are prefixed with CAIRN_.  See .env.example for documentation.

Usage:
    from cairn.config import get_settings
    settings = get_settings()
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CAIRN_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Storage
    data_dir: Path = Field(
        default=Path("./data"),
        description="Directory where SQLite database files are created.",
    )

    # Server
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000, ge=1, le=65535)
    reload: bool = Field(default=False, description="Enable uvicorn auto-reload (dev only).")

    # Security
    secret_key: str = Field(
        default="change-me-in-production",
        description="Used for any future signed token needs. Must be changed before deployment.",
    )

    # SSE stream
    stream_keepalive_seconds: int = Field(
        default=15,
        description="Interval between SSE keepalive comments to prevent proxy timeouts.",
    )

    # ---------------------------------------------------------------------------
    # GitLab integration (Phase 3)
    # ---------------------------------------------------------------------------
    # Abstract the base URL so self-hosted GitLab CE and gitlab.com are both
    # supported with zero code changes — only this env var differs.

    gitlab_url: str = Field(
        default="http://gitlab",
        description=(
            "Base URL of the GitLab instance. "
            "Self-hosted example: http://gitlab.local  "
            "Cloud example: https://gitlab.com"
        ),
    )
    gitlab_token: str = Field(
        default="",
        description="GitLab personal access token or project access token with read_repository scope.",
    )
    gitlab_project_id: str = Field(
        default="",
        description=(
            "GitLab project ID (numeric, e.g. '42') or namespaced path "
            "(e.g. 'security-team/methodologies').  Both are accepted by the API."
        ),
    )
    gitlab_methodology_dir: str = Field(
        default="methodologies",
        description="Directory in the GitLab repo that contains methodology .yml files.",
    )
    gitlab_webhook_secret: str = Field(
        default="",
        description=(
            "Secret token configured in GitLab project webhook settings. "
            "If set, every incoming webhook is verified against the X-Gitlab-Token header. "
            "Leave empty to disable verification (dev only)."
        ),
    )

    # ---------------------------------------------------------------------------
    # ChromaDB integration (Phase 3)
    # ---------------------------------------------------------------------------

    chroma_host: str = Field(
        default="chromadb",
        description="Hostname of the ChromaDB HTTP server (Docker service name in Compose).",
    )
    chroma_port: int = Field(
        default=8000,
        description="Port of the ChromaDB HTTP server.",
    )
    chroma_collection: str = Field(
        default="methodologies",
        description="ChromaDB collection name for methodology semantic search.",
    )

    # ---------------------------------------------------------------------------
    # Obsidian vault bridge (Phase 4)
    # ---------------------------------------------------------------------------

    vault_path: Path = Field(
        default=Path("/vault"),
        description=(
            "Absolute path to the Obsidian vault directory. "
            "Must be pre-initialised by Obsidian (.obsidian/ folder must exist). "
            "In Docker Compose this is bind-mounted into the container at the same path."
        ),
    )
    vault_collection: str = Field(
        default="vault-notes",
        description="ChromaDB collection name for promoted vault notes (separate from methodologies).",
    )

    # ---------------------------------------------------------------------------
    # Corroboration detection (Phase 4)
    # ---------------------------------------------------------------------------

    corroboration_n: int = Field(
        default=2,
        ge=1,
        description=(
            "Minimum number of distinct agents that must mention the same entity "
            "within the time window to trigger an automatic promotion candidate."
        ),
    )
    corroboration_window_hours: int = Field(
        default=24,
        ge=1,
        description="Time window in hours within which corroboration is detected.",
    )
    promotion_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum confidence score for an agent self-nomination (promote: candidate) "
            "to create a promotion candidate without human intervention."
        ),
    )

    # ---------------------------------------------------------------------------
    # CouchDB integration (Phase 4.4 — Obsidian LiveSync vault sync)
    # ---------------------------------------------------------------------------
    # These vars use NO CAIRN_ prefix — they're shared with docker-compose.yml
    # These use the standard CAIRN_ prefix (e.g. CAIRN_COUCHDB_USER).

    couchdb_url: str = Field(
        default="http://couchdb:5984",
    )
    couchdb_user: str = Field(
        default="",
    )
    couchdb_password: str = Field(
        default="",
    )
    couchdb_database: str = Field(
        default="obsidian-livesync",
    )
    couchdb_enabled: bool = Field(
        default=True,
        description="Set false to disable the CouchDB dual-write entirely.",
    )

    # ---------------------------------------------------------------------------
    # NLP optional features (Phase 4.5)
    # ---------------------------------------------------------------------------

    spacy_enabled: bool = Field(
        default=False,
        description=(
            "Enable optional spaCy sentence-boundary fallback in step extraction. "
            "When false, regex/stdlib heuristics are used only."
        ),
    )

    @field_validator("data_dir", mode="before")
    @classmethod
    def resolve_data_dir(cls, v: str | Path) -> Path:
        return Path(v).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()
