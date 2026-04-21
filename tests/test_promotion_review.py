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

"""Integration tests for the promotion review endpoint (Phase 4.3).

Tests the promotion scorer with various message data scenarios.
Full endpoint tests require a live DatabaseManager, which is tested
via the API integration tests in test_db_init_it.py style.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from cairn.jobs.promotion_scorer import PromotionScorer


class TestPromotionScorerWithMessageData:
    """Test promotion scorer with realistic message data patterns."""

    def setup_method(self):
        self.scorer = PromotionScorer()

    def test_high_value_finding(self):
        """A high-confidence, old finding with multiple entities scores high."""
        message = {
            "confidence": 0.9,
            "tags": ["apt29", "lateral-movement", "named-pipes", "critical"],
            "timestamp": (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        score, breakdown = self.scorer.score(message, corroboration_count=2, entity_count=5)

        # High confidence = 0.9 * 0.3 = 0.27
        assert breakdown.confidence_component > 0.25
        # Corroboration = 2/3 * 0.3 = 0.2 (capped at 3)
        assert breakdown.corroboration_component > 0.15
        # Entities = 5/5 * 0.2 = 0.2 (capped)
        assert breakdown.entity_density_component == pytest.approx(0.2, rel=0.01)
        # Age = 40/30 * 0.1 = 0.1 (capped, confidence >= 0.5)
        assert breakdown.age_component == pytest.approx(0.1, rel=0.01)
        # Tags = 4/5 * 0.1 = 0.08
        assert breakdown.tag_component > 0.05

        assert score > 0.7  # Should be highly promotable

    def test_low_value_hypothesis(self):
        """A low-confidence hypothesis scores low."""
        message = {
            "confidence": 0.2,
            "tags": ["speculation"],
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert score < 0.1
        assert breakdown.confidence_component < 0.1
        assert breakdown.corroboration_component == 0.0
        assert breakdown.entity_density_component == 0.0
        assert breakdown.age_component == 0.0  # New message
        assert breakdown.tag_component < 0.05

    def test_corroboration_boosts_score(self):
        """Same entity mentioned by different agents boosts the score."""
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Single agent, no corroboration
        message_1 = {"confidence": 0.8, "tags": ["finding"], "timestamp": old_timestamp}
        score_1, _ = self.scorer.score(message_1, corroboration_count=0, entity_count=2)

        # Multiple agents corroborating
        message_2 = {"confidence": 0.8, "tags": ["finding"], "timestamp": old_timestamp}
        score_2, _ = self.scorer.score(message_2, corroboration_count=3, entity_count=2)

        assert score_2 > score_1
        assert score_2 - score_1 > 0.15  # Significant boost from corroboration

    def test_entity_density_matters(self):
        """More entities in the body = higher score."""
        message = {"confidence": 0.7, "tags": ["test"], "timestamp": "2026-04-20T10:00:00Z"}

        score_few, _ = self.scorer.score(message, corroboration_count=0, entity_count=1)
        score_many, _ = self.scorer.score(message, corroboration_count=0, entity_count=5)

        assert score_many > score_few
        assert score_many - score_few > 0.1

    def test_age_credit_with_high_confidence(self):
        """Old messages with high confidence get age credit."""
        old = {"confidence": 0.8, "tags": [], "timestamp": (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        new = {"confidence": 0.8, "tags": [], "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

        score_old, breakdown_old = self.scorer.score(old, corroboration_count=0, entity_count=0)
        score_new, breakdown_new = self.scorer.score(new, corroboration_count=0, entity_count=0)

        assert breakdown_old.age_component > breakdown_new.age_component
        assert score_old > score_new

    def test_age_no_credit_with_low_confidence(self):
        """Old messages with low confidence don't get age credit."""
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")

        message = {"confidence": 0.3, "tags": [], "timestamp": old_timestamp}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.age_component == 0.0  # Should not get age credit

    def test_tags_influence_score(self):
        """More tags = higher tag component."""
        message_few = {"confidence": 0.5, "tags": ["a"], "timestamp": "2026-04-20T10:00:00Z"}
        message_many = {"confidence": 0.5, "tags": ["a", "b", "c", "d", "e"], "timestamp": "2026-04-20T10:00:00Z"}

        _, breakdown_few = self.scorer.score(message_few, corroboration_count=0, entity_count=0)
        _, breakdown_many = self.scorer.score(message_many, corroboration_count=0, entity_count=0)

        assert breakdown_many.tag_component > breakdown_few.tag_component

    def test_real_world_scenario_apt29_finding(self):
        """Simulate a real APT29 finding message."""
        message = {
            "confidence": 0.87,
            "tags": ["apt29", "lateral-movement", "named-pipes", "cobalt-strike"],
            "timestamp": (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        # Corroborated by 2 other agents
        score, breakdown = self.scorer.score(message, corroboration_count=2, entity_count=4)

        # Should be a high-scoring candidate
        assert score > 0.5
        assert breakdown.confidence_component > 0.2
        assert breakdown.corroboration_component > 0.1
        assert breakdown.entity_density_component > 0.1

    def test_real_world_scenario_low_confidence_query(self):
        """Simulate a low-confidence query message."""
        message = {
            "confidence": 0.3,
            "tags": ["query", "investigation"],
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=1)

        # Should score low - not a finding worth promoting
        assert score < 0.2
        assert breakdown.confidence_component < 0.1


class TestPromotionScorerEdgeCases:
    """Edge cases for the promotion scorer."""

    def setup_method(self):
        self.scorer = PromotionScorer()

    def test_message_with_missing_confidence_field(self):
        """Message without confidence field is handled gracefully."""
        message = {"tags": ["test"], "timestamp": "2026-04-20T10:00:00Z"}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.confidence_component == 0.0
        assert 0.0 <= score <= 1.0

    def test_message_with_malformed_timestamp(self):
        """Malformed timestamp doesn't crash the scorer."""
        message = {"confidence": 0.8, "tags": [], "timestamp": "not-a-valid-timestamp"}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.age_component == 0.0
        assert 0.0 <= score <= 1.0

    def test_message_with_string_tags(self):
        """Tags as string instead of list is handled."""
        message = {"confidence": 0.8, "tags": "not-a-list", "timestamp": "2026-04-20T10:00:00Z"}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.tag_component == 0.0
        assert 0.0 <= score <= 1.0

    def test_very_old_message(self):
        """Message older than threshold gets max age credit."""
        message = {
            "confidence": 0.9,
            "tags": [],
            "timestamp": (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.age_component == pytest.approx(0.1, rel=0.01)

    def test_extreme_corroboration_count(self):
        """Very high corroboration count is capped."""
        message = {"confidence": 0.5, "tags": [], "timestamp": "2026-04-20T10:00:00Z"}
        score, breakdown = self.scorer.score(message, corroboration_count=100, entity_count=0)

        assert breakdown.corroboration_component == pytest.approx(0.3, rel=0.01)

    def test_extreme_entity_count(self):
        """Very high entity count is capped."""
        message = {"confidence": 0.5, "tags": [], "timestamp": "2026-04-20T10:00:00Z"}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=100)

        assert breakdown.entity_density_component == pytest.approx(0.2, rel=0.01)

    def test_many_tags(self):
        """Very high tag count is capped."""
        many_tags = [f"tag{i}" for i in range(20)]
        message = {"confidence": 0.5, "tags": many_tags, "timestamp": "2026-04-20T10:00:00Z"}
        score, breakdown = self.scorer.score(message, corroboration_count=0, entity_count=0)

        assert breakdown.tag_component == pytest.approx(0.1, rel=0.01)