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

    @field_validator("data_dir", mode="before")
    @classmethod
    def resolve_data_dir(cls, v: str | Path) -> Path:
        return Path(v).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()
