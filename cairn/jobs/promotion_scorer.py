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

"""Promotion score computation for retroactive review (Phase 4.3).

Computes a 0.0-1.0 promotion likelihood score for unflagged messages based on:
- confidence field (weight 0.3)
- corroboration count (weight 0.3)
- entity density (weight 0.2)
- message age (weight 0.1)
- tag count (weight 0.1)

Usage::

    from cairn.jobs.promotion_scorer import PromotionScorer

    scorer = PromotionScorer()
    score, breakdown = scorer.score(
        message={
            "confidence": 0.85,
            "tags": ["apt29", "lateral-movement", "named-pipes"],
            "timestamp": "2026-03-15T10:00:00Z",
        },
        corroboration_count=2,
        entity_count=4,
    )
    # score = 0.xxxx (0.0-1.0)
    # breakdown = {"confidence_component": 0.255, ...}
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


# Weights as defined in Phase 4.3 spec
WEIGHTS = {
    "confidence": 0.3,
    "corroboration": 0.3,
    "entity_density": 0.2,
    "age": 0.1,
    "tags": 0.1,
}

# Normalization thresholds (per spec)
CORROBORATION_THRESHOLD = 3  # 3+ distinct agents = max score
ENTITY_DENSITY_THRESHOLD = 5  # 5+ entities = max score
AGE_THRESHOLD_DAYS = 30      # 30+ days old = max score
TAG_THRESHOLD = 5            # 5+ tags = max score
AGE_CONFIDENCE_GATE = 0.5    # Only give age credit if confidence >= 0.5


@dataclass(frozen=True)
class ScoreBreakdown:
    """Breakdown of promotion score components."""
    confidence_component: float      # 0.0-0.3
    corroboration_component: float   # 0.0-0.3
    entity_density_component: float  # 0.0-0.2
    age_component: float             # 0.0-0.1
    tag_component: float             # 0.0-0.1


class PromotionScorer:
    """Compute promotion scores for unflagged blackboard messages."""

    def score(
        self,
        message: dict,
        corroboration_count: int,
        entity_count: int,
    ) -> tuple[float, ScoreBreakdown]:
        """Compute promotion score for a message.

        Args:
            message: Dict with keys: confidence (float|None), tags (list), timestamp (str)
            corroboration_count: Number of distinct agents sharing entities
            entity_count: Number of extractable entities in message body

        Returns:
            Tuple of (total_score 0.0-1.0, ScoreBreakdown)
        """
        # Confidence component (0.0-0.3)
        confidence = message.get("confidence")
        confidence_component = 0.0
        if confidence is not None and isinstance(confidence, (int, float)):
            # Clamp to [0, 1] range before applying weight
            clamped_confidence = max(0.0, min(float(confidence), 1.0))
            confidence_component = clamped_confidence * WEIGHTS["confidence"]

        # Corroboration component (0.0-0.3)
        # Normalized: min(count / 3, 1.0) * 0.3
        corroboration_normalized = min(corroboration_count / CORROBORATION_THRESHOLD, 1.0)
        corroboration_component = corroboration_normalized * WEIGHTS["corroboration"]

        # Entity density component (0.0-0.2)
        # Normalized: min(count / 5, 1.0) * 0.2
        entity_density_normalized = min(entity_count / ENTITY_DENSITY_THRESHOLD, 1.0)
        entity_density_component = entity_density_normalized * WEIGHTS["entity_density"]

        # Age component (0.0-0.1)
        # Older confirmed findings have proven durability
        # Only contributes if confidence >= 0.5
        age_component = 0.0
        timestamp = message.get("timestamp")
        if timestamp and confidence is not None and confidence >= AGE_CONFIDENCE_GATE:
            try:
                msg_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                age_days = (now - msg_time).days
                age_normalized = min(age_days / AGE_THRESHOLD_DAYS, 1.0)
                age_component = age_normalized * WEIGHTS["age"]
            except (ValueError, TypeError):
                pass  # Invalid timestamp, no age credit

        # Tag count component (0.0-0.1)
        # Normalized: min(len(tags) / 5, 1.0) * 0.1
        tags = message.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        tag_count = len(tags)
        tag_normalized = min(tag_count / TAG_THRESHOLD, 1.0)
        tag_component = tag_normalized * WEIGHTS["tags"]

        # Total score (0.0-1.0)
        total = (
            confidence_component
            + corroboration_component
            + entity_density_component
            + age_component
            + tag_component
        )
        total = max(0.0, min(total, 1.0))  # Clamp to [0, 1]

        breakdown = ScoreBreakdown(
            confidence_component=round(confidence_component, 4),
            corroboration_component=round(corroboration_component, 4),
            entity_density_component=round(entity_density_component, 4),
            age_component=round(age_component, 4),
            tag_component=round(tag_component, 4),
        )

        return round(total, 4), breakdown