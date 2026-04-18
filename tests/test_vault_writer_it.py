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

"""Phase 4.2 — vault writer domain-aware routing tests.

Validates that write_note() correctly routes notes into domain subdirectories
for IT entities and preserves flat cairn/ routing for cybersecurity entities.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cairn.vault.writer import _entity_type_to_tag, write_note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROMOTED_AT = "2026-04-17T10:00:00Z"
SOURCE_IDS = ["msg-001", "msg-002"]


def make_note(vault_root: Path, entity: str, entity_type: str, domain: str | None = None) -> str:
    """Convenience wrapper that calls write_note with minimal required args."""
    return write_note(
        vault_root,
        entity=entity,
        entity_type=entity_type,
        narrative="Test narrative.",
        source_message_ids=SOURCE_IDS,
        confidence=0.9,
        promoted_at=PROMOTED_AT,
        domain=domain,
    )


# ---------------------------------------------------------------------------
# Domain-aware path routing
# ---------------------------------------------------------------------------

class TestDomainAwareRouting:
    def test_aws_entity_writes_to_aws_subdirectory(self, tmp_path: Path):
        vault_rel = make_note(tmp_path, "arn:aws:iam::123:role/Admin", "arn", domain="aws")
        assert vault_rel.startswith("cairn/aws/"), f"Expected cairn/aws/, got {vault_rel}"
        note_file = tmp_path / vault_rel
        assert note_file.exists()

    def test_azure_entity_writes_to_azure_subdirectory(self, tmp_path: Path):
        vault_rel = make_note(tmp_path, "my-rg-001", "azure_resource_group", domain="azure")
        assert vault_rel.startswith("cairn/azure/")
        assert (tmp_path / vault_rel).exists()

    def test_networking_entity_writes_to_networking_subdirectory(self, tmp_path: Path):
        vault_rel = make_note(tmp_path, "10.100.0.0/16", "cidr", domain="networking")
        assert vault_rel.startswith("cairn/networking/")
        assert (tmp_path / vault_rel).exists()

    def test_systems_entity_writes_to_systems_subdirectory(self, tmp_path: Path):
        vault_rel = make_note(tmp_path, "host.corp.local", "fqdn", domain="systems")
        assert vault_rel.startswith("cairn/systems/")
        assert (tmp_path / vault_rel).exists()

    def test_pam_entity_writes_to_pam_subdirectory(self, tmp_path: Path):
        vault_rel = make_note(tmp_path, "AWS-Console-Access", "cyberark_safe", domain="pam")
        assert vault_rel.startswith("cairn/pam/")
        assert (tmp_path / vault_rel).exists()

    def test_cybersecurity_entity_writes_to_cairn_root(self, tmp_path: Path):
        """Entities without domain write to cairn/ root — no subdirectory."""
        vault_rel = make_note(tmp_path, "APT29", "actor", domain=None)
        assert vault_rel.startswith("cairn/")
        # Confirm there is NO extra level after 'cairn/'
        parts = vault_rel.split("/")
        assert len(parts) == 2, f"Expected cairn/<file>.md, got {vault_rel}"
        assert (tmp_path / vault_rel).exists()

    def test_domain_none_unchanged_from_pre_phase42(self, tmp_path: Path):
        """No domain argument → same behaviour as before Phase 4.2."""
        vault_rel = write_note(
            tmp_path,
            entity="CVE-2024-12345",
            entity_type="cve",
            narrative="Critical vuln.",
            source_message_ids=SOURCE_IDS,
            confidence=0.95,
            promoted_at=PROMOTED_AT,
        )
        assert vault_rel == "cairn/CVE-2024-12345.md"
        assert (tmp_path / vault_rel).exists()


# ---------------------------------------------------------------------------
# Directory creation
# ---------------------------------------------------------------------------

class TestDirectoryCreation:
    def test_domain_subdirectory_created_if_missing(self, tmp_path: Path):
        """Domain subdir is created automatically on first write."""
        domain_dir = tmp_path / "cairn" / "aws"
        assert not domain_dir.exists()
        make_note(tmp_path, "my-arn", "arn", domain="aws")
        assert domain_dir.is_dir()

    def test_nested_domain_directory_not_broken_by_second_write(self, tmp_path: Path):
        """Second write to same domain doesn't raise even if dir exists."""
        make_note(tmp_path, "arn1", "arn", domain="aws")
        make_note(tmp_path, "arn2", "arn", domain="aws")
        assert (tmp_path / "cairn" / "aws").is_dir()


# ---------------------------------------------------------------------------
# Note content
# ---------------------------------------------------------------------------

class TestNoteContent:
    def test_note_contains_entity_type_tag(self, tmp_path: Path):
        vault_rel = make_note(tmp_path, "AWS-Console-Access", "cyberark_safe", domain="pam")
        content = (tmp_path / vault_rel).read_text(encoding="utf-8")
        assert "pam-safe" in content

    def test_note_contains_narrative(self, tmp_path: Path):
        vault_rel = write_note(
            tmp_path,
            entity="VLAN 100",
            entity_type="vlan",
            narrative="Segmentation VLAN for DMZ hosts.",
            source_message_ids=SOURCE_IDS,
            confidence=0.8,
            promoted_at=PROMOTED_AT,
            domain="networking",
        )
        content = (tmp_path / vault_rel).read_text(encoding="utf-8")
        assert "Segmentation VLAN for DMZ hosts." in content

    def test_existing_note_updated_in_domain_subdirectory(self, tmp_path: Path):
        """Second write to same entity appends to evidence rather than creating duplicate."""
        make_note(tmp_path, "AWS-Console-Access", "cyberark_safe", domain="pam")
        make_note(tmp_path, "AWS-Console-Access", "cyberark_safe", domain="pam")
        # Only one file should exist
        pam_dir = tmp_path / "cairn" / "pam"
        files = list(pam_dir.glob("*.md"))
        assert len(files) == 1
        # The evidence section should have two entries
        content = files[0].read_text(encoding="utf-8")
        assert content.count("msg-001") >= 2  # once per evidence entry


# ---------------------------------------------------------------------------
# _entity_type_to_tag — extended mapping
# ---------------------------------------------------------------------------

class TestEntityTypeToTag:
    @pytest.mark.parametrize("entity_type,expected_tag", [
        # Existing cybersecurity types (unchanged)
        ("ipv4",      "ip-address"),
        ("ipv6",      "ip-address"),
        ("fqdn",      "hostname"),
        ("cve",       "vulnerability"),
        ("technique", "mitre-attack"),
        ("actor",     "threat-actor"),
        # Phase 4.2 IT domain types
        ("arn",                   "aws-resource"),
        ("aws_account_id",        "aws-account"),
        ("aws_region",            "aws-region"),
        ("azure_subscription_id", "azure-subscription"),
        ("azure_resource_group",  "azure-resource-group"),
        ("cidr",                  "network-range"),
        ("vlan",                  "vlan"),
        ("cyberark_safe",         "pam-safe"),
    ])
    def test_entity_type_maps_correctly(self, entity_type: str, expected_tag: str):
        assert _entity_type_to_tag(entity_type) == expected_tag

    def test_unknown_type_falls_through(self):
        """Unknown entity types return the type string itself (safe fallback)."""
        assert _entity_type_to_tag("future_type") == "future_type"
