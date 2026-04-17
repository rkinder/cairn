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

"""Sigma rule validation for methodology submissions.

Validates that submitted YAML content is a structurally valid Sigma rule
before committing to GitLab. This mirrors the CI pipeline checks so that
invalid rules are rejected at the API layer rather than failing in CI.
"""

from __future__ import annotations

import re

import yaml


class SigmaValidationError(Exception):
    """Raised when a Sigma rule fails structural validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Sigma validation failed: {'; '.join(errors)}")


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def validate_sigma_rule(content: str) -> dict:
    """Validate YAML content as a Sigma rule.

    Returns the parsed YAML dict on success.
    Raises SigmaValidationError with a list of specific errors on failure.
    """
    errors: list[str] = []

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise SigmaValidationError([f"Invalid YAML: {exc}"]) from exc

    if not isinstance(data, dict):
        raise SigmaValidationError(["Content must be a YAML mapping, not a scalar or list"])

    # Required fields
    for field in ("title", "id", "logsource", "detection"):
        if field not in data:
            errors.append(f"Missing required field: {field}")

    # id must be a UUID
    rule_id = data.get("id", "")
    if rule_id and not _UUID_RE.match(str(rule_id)):
        errors.append(f"id must be a UUID, got: {rule_id}")

    # logsource must be a mapping
    logsource = data.get("logsource")
    if logsource is not None and not isinstance(logsource, dict):
        errors.append("logsource must be a mapping")

    # detection must be a mapping with a 'condition' key
    detection = data.get("detection")
    if detection is not None:
        if not isinstance(detection, dict):
            errors.append("detection must be a mapping")
        elif "condition" not in detection:
            errors.append("detection must contain a 'condition' key")

    if errors:
        raise SigmaValidationError(errors)

    return data
