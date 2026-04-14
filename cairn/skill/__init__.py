"""Cairn agent skill — client library for the blackboard.

Agents import BlackboardClient as their sole entry point.

    from cairn.skill import BlackboardClient
"""

from cairn.skill.client import (
    BlackboardClient,
    MessageDetail,
    MessageSummary,
    PostMessageResult,
    PromoteResult,
)
from cairn.skill.composer import compose_message
from cairn.skill.exceptions import (
    AuthError,
    ForbiddenError,
    NotFoundError,
    ServerError,
    SkillError,
    SpecError,
    SpecOutdatedError,
    ValidationError,
)

__all__ = [
    "BlackboardClient",
    "MessageDetail",
    "MessageSummary",
    "PostMessageResult",
    "PromoteResult",
    "compose_message",
    "AuthError",
    "ForbiddenError",
    "NotFoundError",
    "ServerError",
    "SkillError",
    "SpecError",
    "SpecOutdatedError",
    "ValidationError",
]
