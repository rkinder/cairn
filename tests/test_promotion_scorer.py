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

"""Unit tests for the promotion scorer (Phase 4.3)."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from cairn.jobs.promotion_scorer import (
    CORROBORATION_THRESHOLD,
    ENTITY_DENSITY_THRESHOLD,
    AGE_THRESHOLD_DAYS,
    TAG_THRESHOLD,
    AGE_CONFIDENCE_GATE,
    PromotionScorer,
    ScoreBreakdown,
    WEIGHTS,
)


class TestPromotionScorer:
    """Test cases for PromotionScorer.score()."""

    def setup_method(self):
        """Create a fresh scorer for each test."""
        self.scorer = PromotionScorer()

    # -------------------------------------------------------------------------
    # Property tests from spec
    # -------------------------------------------------------------------------

    def test_confidence_only_max(self):
        """WHEN confidence is 1.0 and all other inputs are zero THEN score SHALL be 0.3."""
        message = {
            "confidence": 1.0,
            "tags": [],
            "timestamp": None,  # No timestamp → age component stays 0.0
        }
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.confidence_component == pytest.approx(0.3, rel=0.01)
        assert breakdown.age_component == 0.0
        assert score == pytest.approx(0.3, rel=0.01)

    def test_confidence_missing(self):
        """WHEN confidence is None THEN confidence component SHALL be 0.0."""
        message = {
            "confidence": None,
            "tags": [],  # Empty tags to get exactly 0 score
            "timestamp": "2026-04-20T10:00:00Z",
        }
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.confidence_component == 0.0
        assert score == pytest.approx(0.0, abs=0.001)

    def test_confidence_below_zero(self):
        """Negative confidence should be treated as 0."""
        message = {
            "confidence": -0.5,
            "tags": [],
            "timestamp": "2026-04-20T10:00:00Z",
        }
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.confidence_component == 0.0

    def test_confidence_above_one(self):
        """Confidence above 1.0 should be clamped to 1.0."""
        message = {
            "confidence": 1.5,
            "tags": [],
            "timestamp": "2026-04-20T10:00:00Z",
        }
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.confidence_component == pytest.approx(0.3, rel=0.01)

    def test_corroboration_threshold(self):
        """WHEN corroboration count >= 3 THEN corroboration component SHALL be 0.3."""
        message = {"confidence": 0.0, "tags": [], "timestamp": "2026-04-20T10:00:00Z"}

        # Below threshold
        score, breakdown = self.scorer.score(message, corroboration_count=2, entity_count=0)
        assert breakdown.corroboration_component < 0.3

        # At threshold
        score, breakdown = self.scorer.score(message, corroboration_count=3, entity_count=0)
        assert breakdown.corroboration_component == pytest.approx(0.3, rel=0.01)

        # Above threshold (should cap at 0.3)
        score, breakdown = self.scorer.score(message, corroboration_count=10, entity_count=0)
        assert breakdown.corroboration_component == pytest.approx(0.3, rel=0.01)

    def test_entity_density_threshold(self):
        """WHEN entity count >= 5 THEN entity density component SHALL be 0.2."""
        message = {"confidence": 0.0, "tags": [], "timestamp": "2026-04-20T10:00:00Z"}

        # Below threshold
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=4)
        assert breakdown.entity_density_component < 0.2

        # At threshold
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=5)
        assert breakdown.entity_density_component == pytest.approx(0.2, rel=0.01)

        # Above threshold (should cap at 0.2)
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=10)
        assert breakdown.entity_density_component == pytest.approx(0.2, rel=0.01)

    def test_age_threshold_with_high_confidence(self):
        """WHEN message age >= 30 days AND confidence >= 0.5 THEN age component SHALL be 0.1."""
        # Message older than 30 days with high confidence
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = {"confidence": 0.8, "tags": [], "timestamp": old_timestamp}

        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)
        assert breakdown.age_component == pytest.approx(0.1, rel=0.01)

    def test_age_threshold_with_low_confidence(self):
        """WHEN message age >= 30 days AND confidence < 0.5 THEN age component SHALL be 0.0."""
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = {"confidence": 0.4, "tags": [], "timestamp": old_timestamp}

        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)
        assert breakdown.age_component == 0.0

    def test_age_below_threshold(self):
        """Message younger than 30 days gets age credit proportional to age."""
        recent_timestamp = (datetime.now(timezone.utc) - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = {"confidence": 0.8, "tags": [], "timestamp": recent_timestamp}

        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)
        # 15 days / 30 days = 0.5 * 0.1 = 0.05
        assert breakdown.age_component == pytest.approx(0.05, rel=0.01)

    def test_tag_count_threshold(self):
        """WHEN tag count >= 5 THEN tag component SHALL be 0.1."""
        message = {"confidence": 0.0, "tags": ["a", "b", "c", "d", "e"], "timestamp": "2026-04-20T10:00:00Z"}

        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)
        assert breakdown.tag_component == pytest.approx(0.1, rel=0.01)

    def test_all_components_maxed(self):
        """WHEN all components are maxed THEN total score SHALL be 1.0."""
        # 1.0 confidence + 3+ corroboration + 5+ entities + 30+ days age + 5+ tags
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = {
            "confidence": 1.0,
            "tags": ["a", "b", "c", "d", "e"],
            "timestamp": old_timestamp,
        }

        score, breakdown = self.scorer.score(
            message,
            corroboration_count=5,
            entity_count=7,
        )

        assert score == pytest.approx(1.0, abs=0.02)  # Allow small floating point variance
        assert breakdown.confidence_component == pytest.approx(0.3, rel=0.01)
        assert breakdown.corroboration_component == pytest.approx(0.3, rel=0.01)
        assert breakdown.entity_density_component == pytest.approx(0.2, rel=0.01)
        assert breakdown.age_component == pytest.approx(0.1, rel=0.01)
        assert breakdown.tag_component == pytest.approx(0.1, rel=0.01)

    # -------------------------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------------------------

    def test_zero_entity_count(self):
        """Zero entities should give zero entity density component."""
        message = {"confidence": 0.0, "tags": [], "timestamp": "2026-04-20T10:00:00Z"}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.entity_density_component == 0.0

    def test_zero_corroboration(self):
        """Zero corroboration should give zero corroboration component."""
        message = {"confidence": 0.0, "tags": [], "timestamp": "2026-04-20T10:00:00Z"}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.corroboration_component == 0.0

    def test_empty_tags(self):
        """Empty tags list should give zero tag component."""
        message = {"confidence": 0.0, "tags": [], "timestamp": "2026-04-20T10:00:00Z"}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.tag_component == 0.0

    def test_tags_not_a_list(self):
        """Non-list tags should be treated as empty."""
        message = {"confidence": 0.0, "tags": "not-a-list", "timestamp": "2026-04-20T10:00:00Z"}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.tag_component == 0.0

    def test_missing_timestamp(self):
        """Missing timestamp should give zero age component."""
        message = {"confidence": 0.8, "tags": [], "timestamp": None}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.age_component == 0.0

    def test_invalid_timestamp(self):
        """Invalid timestamp should give zero age component."""
        message = {"confidence": 0.8, "tags": [], "timestamp": "not-a-date"}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.age_component == 0.0

    def test_new_message(self):
        """Brand new message (today) should get minimal age component."""
        now_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = {"confidence": 0.8, "tags": [], "timestamp": now_timestamp}

        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.age_component < 0.01  # Near zero

    def test_score_bounds(self):
        """Score should always be in [0.0, 1.0] range."""
        # Test various inputs
        test_cases = [
            {"confidence": 1.0, "tags": ["a", "b", "c", "d", "e"], "timestamp": "2026-03-01T10:00:00Z"},
            {"confidence": -1.0, "tags": [], "timestamp": None},
            {"confidence": None, "tags": [], "timestamp": "2026-04-20T10:00:00Z"},
            {"confidence": 0.5, "tags": ["x"], "timestamp": "2026-04-20T10:00:00Z"},
        ]

        for msg in test_cases:
            score, _ = self.scorer.score(msg, corroboration_count=0, entity_count=0)
            assert 0.0 <= score <= 1.0, f"Score {score} out of bounds for message {msg}"

    def test_score_breakdown_immutability(self):
        """ScoreBreakdown should be immutable (frozen dataclass)."""
        message = {"confidence": 0.8, "tags": ["a"], "timestamp": "2026-04-20T10:00:00Z"}
        score, breakdown = self.scorer.score(message, corroboration_count=1, entity_count=1)

        # Try to mutate (should raise)
        with pytest.raises(AttributeError):
            breakdown.confidence_component = 0.5

    def test_weight_sum(self):
        """Weights should sum to 1.0."""
        total_weight = sum(WEIGHTS.values())
        assert total_weight == pytest.approx(1.0, abs=0.001)


class TestScoreBreakdown:
    """Tests for the ScoreBreakdown dataclass."""

    def test_creation(self):
        """ScoreBreakdown can be created with valid values."""
        breakdown = ScoreBreakdown(
            confidence_component=0.3,
            corroboration_component=0.3,
            entity_density_component=0.2,
            age_component=0.1,
            tag_component=0.1,
        )
        assert breakdown.confidence_component == 0.3

    def test_repr(self):
        """ScoreBreakdown has a useful repr."""
        breakdown = ScoreBreakdown(
            confidence_component=0.15,
            corroboration_component=0.1,
            entity_density_component=0.08,
            age_component=0.03,
            tag_component=0.04,
        )
        repr_str = repr(breakdown)
        assert "ScoreBreakdown" in repr_str
        assert "0.15" in repr_str