"""Typed exception hierarchy for the Cairn agent skill.

Callers can catch the specific exception they care about without inspecting
HTTP status codes directly.
"""

from __future__ import annotations


class SkillError(Exception):
    """Base class for all skill errors."""


class AuthError(SkillError):
    """API key is missing, invalid, or the agent has been deactivated."""


class NotFoundError(SkillError):
    """The requested message or resource does not exist."""


class ForbiddenError(SkillError):
    """The authenticated agent is not allowed to perform this action."""


class ValidationError(SkillError):
    """The server rejected the request due to a validation failure."""


class SpecError(SkillError):
    """The OpenAPI spec could not be fetched, parsed, or resolved."""


class SpecOutdatedError(SkillError):
    """The server spec version is higher than the installed skill version.

    The agent's orchestration layer should decide whether to abort or proceed.
    """

    def __init__(self, skill_version: str, server_version: str) -> None:
        self.skill_version  = skill_version
        self.server_version = server_version
        super().__init__(
            f"Server spec version '{server_version}' is newer than skill "
            f"version '{skill_version}'. Consider upgrading cairn."
        )


class ServerError(SkillError):
    """The server returned an unexpected 5xx response."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(f"Server error {status_code}: {detail}")
