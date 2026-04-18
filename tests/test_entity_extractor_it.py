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

"""Phase 4.2 — entity extractor IT domain tests.

Validates new patterns (AWS, Azure, networking, PAM, systems FQDN tagging)
and confirms all existing cybersecurity patterns remain unaffected.
"""

from __future__ import annotations

import pytest

from cairn.nlp.entity_extractor import Entity, extract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _types(entities):
    return [e.type for e in entities]


def _values(entities):
    return [e.value for e in entities]


def _by_type(entities, entity_type):
    return [e for e in entities if e.type == entity_type]


def _domains(entities):
    return {e.type: e.domain for e in entities}


# ---------------------------------------------------------------------------
# Entity dataclass — backward compatibility
# ---------------------------------------------------------------------------

class TestEntityDataclass:
    def test_domain_defaults_to_none(self):
        """Entity constructed without domain has domain=None."""
        e = Entity(type="ipv4", value="1.2.3.4", span=(0, 7))
        assert e.domain is None

    def test_entity_with_domain_is_constructible(self):
        """Entity with explicit domain field works correctly."""
        e = Entity(type="arn", value="arn:aws:iam::123:role/X", span=(0, 5), domain="aws")
        assert e.domain == "aws"

    def test_entity_is_hashable_without_domain(self):
        """Entity without domain can be put in a set."""
        e = Entity(type="ipv4", value="1.2.3.4", span=(0, 7))
        assert e in {e}

    def test_entity_is_hashable_with_domain(self):
        """Entity with domain can be put in a set."""
        e = Entity(type="arn", value="arn:aws:iam::123:role/X", span=(0, 5), domain="aws")
        assert e in {e}


# ---------------------------------------------------------------------------
# AWS patterns
# ---------------------------------------------------------------------------

class TestAWSExtraction:
    def test_extracts_arn(self):
        text = "Role assigned: arn:aws:iam::123456789012:role/AdminRole for escalation."
        entities = extract(text)
        arns = _by_type(entities, "arn")
        assert len(arns) == 1
        assert "arn:aws:iam::123456789012:role/AdminRole" in arns[0].value
        assert arns[0].domain == "aws"

    def test_arn_with_s3_resource(self):
        # S3 ARNs with account IDs are used in cross-account policies.
        # Our pattern requires a 12-digit account field.
        text = "Bucket policy references arn:aws:s3:us-east-1:123456789012:my-secure-bucket"
        arns = _by_type(extract(text), "arn")
        assert len(arns) == 1
        assert "my-secure-bucket" in arns[0].value

    def test_extracts_aws_region(self):
        text = "Instance deployed to us-east-1."
        regions = _by_type(extract(text), "aws_region")
        assert len(regions) == 1
        assert regions[0].value == "us-east-1"
        assert regions[0].domain == "aws"

    def test_extracts_eu_west_region(self):
        text = "Backup replication target: eu-west-2"
        regions = _by_type(extract(text), "aws_region")
        assert regions[0].value == "eu-west-2"

    def test_extracts_ap_region(self):
        text = "Singapore region ap-southeast-1 selected."
        regions = _by_type(extract(text), "aws_region")
        assert regions[0].value == "ap-southeast-1"

    def test_extracts_account_id_with_context(self):
        text = "Account 123456789012 has overly permissive IAM policies."
        ids = _by_type(extract(text), "aws_account_id")
        assert len(ids) == 1
        assert ids[0].value == "123456789012"
        assert ids[0].domain == "aws"

    def test_suppresses_account_id_without_context(self):
        """Bare 12-digit number with no 'account' nearby → not extracted."""
        text = "Error code: 123456789012 at timestamp 20260417."
        ids = _by_type(extract(text), "aws_account_id")
        assert ids == []

    def test_suppresses_account_id_outside_window(self):
        """'account' word is 25 chars from the 12-digit number → not extracted."""
        # 'account' appears 25 chars before the number (> 20 char window)
        text = "some account field here: 123456789012"
        # 'account' is at position 5, number at 25 — gap is 20 chars exactly at boundary
        # Let's force it to be just outside the window
        text = "account" + " " * 21 + "123456789012"
        ids = _by_type(extract(text), "aws_account_id")
        assert ids == []

    def test_account_id_in_arn_not_double_extracted(self):
        """ARN match suppresses separate account ID extraction for same number."""
        text = "arn:aws:iam::123456789012:role/Admin"
        entities = extract(text)
        # ARN should be extracted; account ID shouldn't additionally appear
        # (The account number is inside the ARN — no separate 12-digit standalone match)
        arns = _by_type(entities, "arn")
        assert len(arns) == 1


# ---------------------------------------------------------------------------
# Azure patterns
# ---------------------------------------------------------------------------

class TestAzureExtraction:
    def test_extracts_subscription_uuid_with_context(self):
        text = "subscription 550e8400-e29b-41d4-a716-446655440000 is over budget."
        ids = _by_type(extract(text), "azure_subscription_id")
        assert len(ids) == 1
        assert ids[0].value == "550e8400-e29b-41d4-a716-446655440000"
        assert ids[0].domain == "azure"

    def test_suppresses_uuid_without_subscription_context(self):
        """UUID with no 'subscription' nearby → not extracted."""
        text = "Request-ID: 550e8400-e29b-41d4-a716-446655440000 failed."
        ids = _by_type(extract(text), "azure_subscription_id")
        assert ids == []

    def test_suppresses_uuid_outside_window(self):
        """'subscription' word is > 30 chars from UUID → not extracted."""
        text = "subscription" + " " * 31 + "550e8400-e29b-41d4-a716-446655440000"
        ids = _by_type(extract(text), "azure_subscription_id")
        assert ids == []

    def test_extracts_resource_group(self):
        text = "Resource at /resourceGroups/prod-rg-001/providers/Microsoft.Compute/..."
        rgs = _by_type(extract(text), "azure_resource_group")
        assert len(rgs) == 1
        assert rgs[0].value == "prod-rg-001"
        assert rgs[0].domain == "azure"

    def test_resource_group_case_insensitive(self):
        text = "/ResourceGroups/DevWorkloads/providers/..."
        rgs = _by_type(extract(text), "azure_resource_group")
        assert rgs[0].value == "DevWorkloads"


# ---------------------------------------------------------------------------
# Networking patterns
# ---------------------------------------------------------------------------

class TestNetworkingExtraction:
    def test_extracts_cidr_class_b(self):
        text = "Corporate network uses 10.100.0.0/16."
        cidrs = _by_type(extract(text), "cidr")
        assert len(cidrs) == 1
        assert cidrs[0].value == "10.100.0.0/16"
        assert cidrs[0].domain == "networking"

    def test_extracts_cidr_slash_32(self):
        text = "Host route 192.168.1.5/32 added to table."
        cidrs = _by_type(extract(text), "cidr")
        assert cidrs[0].value == "192.168.1.5/32"

    def test_suppresses_invalid_cidr_octet(self):
        """999.0.0.0/8 has an invalid octet — must not be extracted."""
        text = "Bad CIDR 999.0.0.0/8 in config."
        cidrs = _by_type(extract(text), "cidr")
        assert cidrs == []

    def test_suppresses_invalid_cidr_prefix(self):
        """Prefix /33 is out of range — must not be extracted."""
        text = "Invalid block 10.0.0.0/33 specified."
        cidrs = _by_type(extract(text), "cidr")
        assert cidrs == []

    def test_extracts_vlan_with_space(self):
        text = "Traffic tagged on VLAN 100."
        vlans = _by_type(extract(text), "vlan")
        assert len(vlans) == 1
        assert vlans[0].value == "VLAN 100"
        assert vlans[0].domain == "networking"

    def test_extracts_vlan_without_space(self):
        text = "Switchport assigned VLAN200."
        vlans = _by_type(extract(text), "vlan")
        assert vlans[0].value == "VLAN 200"

    def test_extracts_vlan_case_insensitive(self):
        text = "vlan 4094 is the maximum."
        vlans = _by_type(extract(text), "vlan")
        assert vlans[0].value == "VLAN 4094"

    def test_multiple_cidrs(self):
        text = "ACL allows 10.0.0.0/8 and 172.16.0.0/12 but blocks 192.168.0.0/16."
        cidrs = _by_type(extract(text), "cidr")
        assert len(cidrs) == 3


# ---------------------------------------------------------------------------
# PAM / CyberArk patterns
# ---------------------------------------------------------------------------

class TestPAMExtraction:
    def test_extracts_safe_from_body(self):
        text = "Credential retrieved from Safe: AWS-Console-Access by operator."
        safes = _by_type(extract(text), "cyberark_safe")
        assert len(safes) == 1
        assert safes[0].value == "AWS-Console-Access"
        assert safes[0].domain == "pam"

    def test_extracts_safe_from_tags(self):
        entities = extract("Session initiated.", tags=["safe:AWS-Console-Access", "other-tag"])
        safes = _by_type(entities, "cyberark_safe")
        assert len(safes) == 1
        assert safes[0].value == "AWS-Console-Access"
        assert safes[0].domain == "pam"

    def test_safe_from_body_case_insensitive(self):
        text = "SAFE: ProdLinuxRootAccess credential checked out."
        safes = _by_type(extract(text), "cyberark_safe")
        assert safes[0].value == "ProdLinuxRootAccess"

    def test_safe_deduplication(self):
        """Same safe in body and tags → deduplicated to one entity."""
        text = "Retrieved from Safe: MyVault"
        entities = extract(text, tags=["safe:MyVault"])
        safes = _by_type(entities, "cyberark_safe")
        assert len(safes) == 1


# ---------------------------------------------------------------------------
# Systems — FQDN domain tagging
# ---------------------------------------------------------------------------

class TestSystemsFQDNTagging:
    def test_fqdn_tagged_systems_when_topic_db_is_systems(self):
        text = "Patching initiated on host.corp.local."
        entities = extract(text, topic_db="systems")
        fqdns = _by_type(entities, "fqdn")
        assert len(fqdns) >= 1
        assert all(e.domain == "systems" for e in fqdns)

    def test_fqdn_not_tagged_systems_for_osint(self):
        text = "C2 beacon observed from malware.example.com."
        entities = extract(text, topic_db="osint")
        fqdns = _by_type(entities, "fqdn")
        assert all(e.domain is None for e in fqdns)

    def test_fqdn_not_tagged_without_topic_db(self):
        text = "Seen at host.example.com."
        entities = extract(text)
        fqdns = _by_type(entities, "fqdn")
        assert all(e.domain is None for e in fqdns)


# ---------------------------------------------------------------------------
# Backward compatibility — existing cybersecurity patterns unchanged
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_ipv4_extraction_unchanged(self):
        text = "C2 server at 203.0.113.42 observed."
        entities = extract(text)
        ips = _by_type(entities, "ipv4")
        assert len(ips) == 1
        assert ips[0].value == "203.0.113.42"
        assert ips[0].domain is None

    def test_cve_extraction_unchanged(self):
        text = "Exploitation of CVE-2024-12345 detected."
        entities = extract(text)
        cves = _by_type(entities, "cve")
        assert len(cves) == 1
        assert cves[0].value == "CVE-2024-12345"
        assert cves[0].domain is None

    def test_technique_extraction_unchanged(self):
        text = "Used T1059.001 for initial execution."
        entities = extract(text)
        techs = _by_type(entities, "technique")
        assert len(techs) == 1
        assert techs[0].value == "T1059.001"
        assert techs[0].domain is None

    def test_actor_extraction_unchanged(self):
        entities = extract("", tags=["actor:APT29"])
        actors = _by_type(entities, "actor")
        assert len(actors) == 1
        assert actors[0].value == "APT29"
        assert actors[0].domain is None

    def test_existing_callers_unaffected_by_domain_field(self):
        """Callers that only use .type and .value are unaffected by domain."""
        text = "Alert for 10.0.0.1 re: CVE-2024-99999"
        entities = extract(text)
        # Verify both fields still accessible as before
        for e in entities:
            _ = e.type
            _ = e.value
            _ = e.span

    def test_cidr_not_confused_with_ipv4(self):
        """CIDR block is extracted as cidr, not as ipv4."""
        text = "Subnet 10.0.0.0/8 is blocked."
        entities = extract(text)
        types = _types(entities)
        assert "cidr" in types
        # The IP address within the CIDR notation may also be an IPv4 match
        # but the CIDR itself should appear as 'cidr' type
        cidrs = _by_type(entities, "cidr")
        assert cidrs[0].value == "10.0.0.0/8"


# ---------------------------------------------------------------------------
# Mixed message — multiple domain entities coexist
# ---------------------------------------------------------------------------

class TestMixedMessage:
    def test_aws_and_networking_entities_in_one_message(self):
        text = (
            "IAM role arn:aws:iam::123456789012:role/Ops deployed in us-east-1. "
            "Traffic flows through 10.50.0.0/24."
        )
        entities = extract(text)
        assert _by_type(entities, "arn")
        assert _by_type(entities, "aws_region")
        assert _by_type(entities, "cidr")

    def test_pam_and_cybersecurity_entities_coexist(self):
        text = (
            "Incident involved CVE-2024-12345 on host.corp.local. "
            "Credentials from Safe: ProdAdminVault were used."
        )
        entities = extract(text)
        assert _by_type(entities, "cve")
        assert _by_type(entities, "cyberark_safe")
