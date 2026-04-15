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
