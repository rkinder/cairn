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

"""Regex-only entity extractor for blackboard message bodies.

No NLP dependencies — purely pattern-based extraction of the entity types
that appear most often in cybersecurity and IT-operations notes:

    Cybersecurity:
        ipv4       — 203.0.113.42
        ipv6       — 2001:db8::1
        fqdn       — host.example.com  (min 2 labels, TLD ≥ 2 chars)
        cve        — CVE-2024-12345
        technique  — T1059, T1059.003  (MITRE ATT&CK)
        actor      — name extracted from `actor:` or `group:` frontmatter tags

    IT domain (Phase 4.2):
        arn                  — arn:aws:iam::123456789012:role/AdminRole
        aws_account_id       — 12-digit AWS account number (context-guarded)
        aws_region           — us-east-1, eu-west-2, ap-southeast-1, …
        azure_subscription_id — RFC 4122 UUID in subscription context
        azure_resource_group — /resourceGroups/<name> ARM path
        cidr                 — 10.100.0.0/16  (validated octets + prefix)
        vlan                 — VLAN 100 / VLAN100  (case-insensitive)
        cyberark_safe        — Safe: <name> in body or safe:<name> in tags

Spans are character offsets into the original text passed to extract().

Usage::

    from cairn.nlp.entity_extractor import extract, Entity

    # Cybersecurity message:
    entities = extract(body_text, tags=["actor:APT29", "group:Cozy Bear"])

    # IT domain message (enables domain-aware FQDN tagging):
    entities = extract(body_text, topic_db="systems")
"""

from __future__ import annotations

import dataclasses
import re
from typing import Sequence


# ---------------------------------------------------------------------------
# Title derivation helper
# ---------------------------------------------------------------------------

# Match Markdown ATX headings: up to 6 #, space, then heading text.
_RE_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def derive_title(body: str, fallback: str) -> str:
    """Derive a human-readable title from a message body.

    1. First ATX heading (``# Title`` or ``## Title``) if found.
    2. First non-empty line of the body, stripped and truncated to 80 chars.
    3. ``fallback`` (typically the entity value).

    ``fallback`` is returned verbatim so callers can pass ``f"msg:{msg_id}"``
    as the last-resort title — the note filename will then be ``msg:...`` which
    is the documented current behaviour for entirely un-entity messages.
    """
    # Look for the first ATX heading at any level.
    m = _RE_HEADING.search(body)
    if m:
        return m.group(2).strip()[:80]
    # Fall back to the first non-empty line, split on double-newline (paragraph).
    first_para = body.strip().split("\n\n")[0].strip()
    if first_para:
        # Collapse the line to a single short string.
        return first_para.replace("\n", " ").replace("\r", "").strip()[:80]
    return fallback


# ---------------------------------------------------------------------------
# Entity dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Entity:
    type:   str               # entity type — see module docstring
    value:  str               # canonical string value
    span:   tuple[int, int]   # (start, end) character offsets into source text
    domain: str | None = None # IT domain hint — None for cybersecurity entities
                              # Phase 4.2: 'aws' | 'azure' | 'networking' |
                              #            'systems' | 'pam'


# ---------------------------------------------------------------------------
# Compiled patterns — cybersecurity (unchanged from Phase 4.1)
# ---------------------------------------------------------------------------

# IPv4: four octets 0-255 separated by dots, not part of a larger number literal
_RE_IPV4 = re.compile(
    r"(?<!\d)"
    r"(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)"
    r"(?!\d)"
)

# IPv6: simplified — requires at least two colon-separated groups; handles ::
_RE_IPV6 = re.compile(
    r"(?<![:\w])"
    r"(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{0,4}"
    r"|::(?:[0-9a-fA-F]{1,4}:)*[0-9a-fA-F]{1,4}"
    r"|(?:[0-9a-fA-F]{1,4}:)*[0-9a-fA-F]{1,4}::"
    r"(?![:0-9a-fA-F])"
)

# CVE IDs: CVE-YYYY-NNNNN (4-digit year, 4+ digit identifier)
_RE_CVE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)

# MITRE ATT&CK technique IDs: T followed by 4 digits, optional .NNN sub-technique
_RE_TECHNIQUE = re.compile(r"\bT\d{4}(?:\.\d{3})?\b")

# FQDN: at least two labels, TLD ≥ 2 alpha chars.
# Must NOT be immediately preceded by @ (to avoid catching email addresses twice)
# and NOT look like an IPv4 address (digits-only labels).
_RE_FQDN = re.compile(
    r"(?<![@\w])"
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)"
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z]{2,}"
    r"(?![\w\-])"
)

# Actor / group tags in YAML frontmatter: 'actor:APT29' or 'group:Cozy Bear'
_RE_ACTOR_TAG = re.compile(r"^(?:actor|group):(.+)$", re.IGNORECASE)

# Common TLDs that should NOT be treated as FQDNs (version numbers, file extensions, etc.)
_FQDN_BLOCKLIST_TLDS = frozenset({
    "exe", "dll", "sys", "bat", "cmd", "ps1", "py", "js", "sh",
    "log", "txt", "csv", "xml", "json", "yaml", "yml",
    "png", "jpg", "jpeg", "gif", "svg", "pdf", "zip", "tar", "gz",
    "tmp", "bak", "old",
})


# ---------------------------------------------------------------------------
# Compiled patterns — IT domain (Phase 4.2)
# ---------------------------------------------------------------------------

# --- AWS ---

# ARN: arn:aws:<service>:<region>:<account-id>:<resource>
# Resource segment is greedy up to whitespace / quote / angle bracket.
_RE_AWS_ARN = re.compile(
    r"\barn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:[^\s\"'<>]+"
)

# AWS region codes: us-east-1, eu-west-2, ap-southeast-1, etc.
_RE_AWS_REGION = re.compile(
    r"\b(?:us|eu|ap|sa|ca|me|af)-(?:east|west|north|south|central|northeast|southeast)-\d\b"
)

# AWS account ID: exactly 12 digits.
# Context-guarded: the word "account" must appear within 20 characters of the match.
# Without the guard, timestamps, port numbers, and log-correlation IDs
# are indistinguishable from account IDs.
_RE_AWS_ACCOUNT_RAW = re.compile(r"\b(\d{12})\b")
_RE_AWS_ACCOUNT_CTX = re.compile(r"account", re.IGNORECASE)


# --- Azure ---

# Subscription UUID — full RFC 4122 format.
# Context-guarded: "subscription" must appear within 30 characters.
# The window is wider than the AWS guard because UUIDs appear innocuously
# in many non-Azure contexts (log correlation IDs, request IDs, etc.).
_RE_AZURE_UUID_RAW = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)
_RE_AZURE_UUID_CTX = re.compile(r"subscription", re.IGNORECASE)

# Azure Resource Group ARM path: /resourceGroups/<name>
_RE_AZURE_RG = re.compile(r"/resourceGroups/([a-zA-Z0-9_\-]+)", re.IGNORECASE)


# --- Networking ---

# CIDR: validated octets (0–255) + prefix length (0–32).
# Reuses the same octet class as _RE_IPV4 to prevent matching 999.0.0.0/8.
_RE_CIDR = re.compile(
    r"\b"
    r"(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)"
    r"/(?:3[0-2]|[12]?\d)"
    r"\b"
)

# VLAN identifier: VLAN 100, VLAN100, vlan 4094 (case-insensitive, 1-4 digits)
_RE_VLAN = re.compile(r"\bVLAN\s*(\d{1,4})\b", re.IGNORECASE)


# --- PAM / CyberArk ---

# Safe name in message body: "Safe: AWS-Console-Access"
_RE_CYBERARK_SAFE_BODY = re.compile(r"\bSafe:\s*([a-zA-Z0-9_\-]+)", re.IGNORECASE)

# Safe name in tags: "safe:AWS-Console-Access"
_RE_CYBERARK_SAFE_TAG = re.compile(r"^safe:(.+)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Context-guard helper
# ---------------------------------------------------------------------------

def _has_context(
    text: str,
    match_start: int,
    match_end: int,
    pattern: re.Pattern,
    window: int = 20,
) -> bool:
    """Return True if *pattern* appears within *window* chars of the match.

    Used to suppress false positives for patterns like 12-digit AWS account
    IDs (common in log lines) and UUIDs (common as correlation IDs).

    Args:
        text:        The full source text.
        match_start: Start offset of the candidate match.
        match_end:   End offset of the candidate match.
        pattern:     Compiled regex that must appear near the match.
        window:      Character radius around the match to search.

    Returns:
        True if *pattern* matches anywhere in the surrounding window.
    """
    lo = max(0, match_start - window)
    hi = min(len(text), match_end + window)
    return bool(pattern.search(text[lo:hi]))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract(
    text: str,
    *,
    tags: Sequence[str] | None = None,
    topic_db: str | None = None,
) -> list[Entity]:
    """Extract entities from *text* and optionally from *tags*.

    Args:
        text:     Markdown body text of a blackboard message.
        tags:     Optional YAML frontmatter tag list.  Actor/group tags are
                  parsed here; ``safe:`` tags trigger CyberArk extraction.
                  Other tag values are ignored.
        topic_db: Optional topic database slug for the originating message.
                  When ``"systems"``, extracted FQDNs are tagged with
                  ``domain="systems"`` for domain-aware vault routing.
                  All other callers can omit this parameter.

    Returns:
        Deduplicated list of Entity objects, ordered by span start offset.
        Actor entities extracted from tags have span (0, 0).
    """
    entities: list[Entity] = []
    seen: set[tuple[str, str]] = set()  # (type, value.lower()) dedup key

    def _add(
        entity_type: str,
        value: str,
        span: tuple[int, int],
        domain: str | None = None,
    ) -> None:
        key = (entity_type, value.lower())
        if key not in seen:
            seen.add(key)
            entities.append(
                Entity(type=entity_type, value=value, span=span, domain=domain)
            )

    # -----------------------------------------------------------------------
    # Cybersecurity patterns (unchanged from Phase 4.1)
    # -----------------------------------------------------------------------

    # --- IPv4 (before FQDN so IPs are classified correctly) ---
    ipv4_spans: set[tuple[int, int]] = set()
    for m in _RE_IPV4.finditer(text):
        _add("ipv4", m.group(), (m.start(), m.end()))
        ipv4_spans.add((m.start(), m.end()))

    # --- CVE IDs ---
    for m in _RE_CVE.finditer(text):
        _add("cve", m.group().upper(), (m.start(), m.end()))

    # --- MITRE ATT&CK techniques ---
    for m in _RE_TECHNIQUE.finditer(text):
        _add("technique", m.group(), (m.start(), m.end()))

    # --- IPv6 ---
    for m in _RE_IPV6.finditer(text):
        _add("ipv6", m.group(), (m.start(), m.end()))

    # --- FQDNs (after IPv4 so pure numeric addresses are excluded) ---
    # When topic_db is 'systems', FQDNs carry domain='systems' for vault routing.
    fqdn_domain = "systems" if topic_db == "systems" else None
    for m in _RE_FQDN.finditer(text):
        span = (m.start(), m.end())
        # Skip if this span overlaps any IPv4 match
        if any(s <= span[0] < e or s < span[1] <= e for s, e in ipv4_spans):
            continue
        value = m.group().rstrip(".")
        tld = value.rsplit(".", 1)[-1].lower()
        if tld in _FQDN_BLOCKLIST_TLDS:
            continue
        # Must contain at least one non-numeric label to be a real hostname
        labels = value.split(".")
        if all(lbl.isdigit() for lbl in labels):
            continue
        _add("fqdn", value, span, domain=fqdn_domain)

    # --- Actors from tags ---
    for tag in (tags or []):
        m = _RE_ACTOR_TAG.match(tag.strip())
        if m:
            actor_name = m.group(1).strip()
            if actor_name:
                _add("actor", actor_name, (0, 0))

    # -----------------------------------------------------------------------
    # IT domain patterns (Phase 4.2)
    # -----------------------------------------------------------------------

    # --- AWS ARNs (FR-3) ---
    for m in _RE_AWS_ARN.finditer(text):
        _add("arn", m.group(), (m.start(), m.end()), domain="aws")

    # --- AWS Account IDs (FR-3, context-guarded: "account" within 20 chars) ---
    for m in _RE_AWS_ACCOUNT_RAW.finditer(text):
        if _has_context(text, m.start(), m.end(), _RE_AWS_ACCOUNT_CTX, window=20):
            _add("aws_account_id", m.group(), (m.start(), m.end()), domain="aws")

    # --- AWS Regions (FR-3) ---
    for m in _RE_AWS_REGION.finditer(text):
        _add("aws_region", m.group(), (m.start(), m.end()), domain="aws")

    # --- Azure Subscription IDs (FR-4, context-guarded: "subscription" within 30 chars) ---
    for m in _RE_AZURE_UUID_RAW.finditer(text):
        if _has_context(text, m.start(), m.end(), _RE_AZURE_UUID_CTX, window=30):
            _add(
                "azure_subscription_id",
                m.group().lower(),
                (m.start(), m.end()),
                domain="azure",
            )

    # --- Azure Resource Groups (FR-4) ---
    for m in _RE_AZURE_RG.finditer(text):
        _add("azure_resource_group", m.group(1), (m.start(), m.end()), domain="azure")

    # --- CIDRs (FR-5) ---
    for m in _RE_CIDR.finditer(text):
        _add("cidr", m.group(), (m.start(), m.end()), domain="networking")

    # --- VLANs (FR-5) ---
    for m in _RE_VLAN.finditer(text):
        _add("vlan", f"VLAN {m.group(1)}", (m.start(), m.end()), domain="networking")

    # --- CyberArk Safes from body (FR-7) ---
    for m in _RE_CYBERARK_SAFE_BODY.finditer(text):
        _add("cyberark_safe", m.group(1), (m.start(), m.end()), domain="pam")

    # --- CyberArk Safes from tags (FR-7) ---
    for tag in (tags or []):
        tm = _RE_CYBERARK_SAFE_TAG.match(tag.strip())
        if tm:
            safe_name = tm.group(1).strip()
            if safe_name:
                _add("cyberark_safe", safe_name, (0, 0), domain="pam")

    # Sort by span start (actors / tag-extracted entities at 0 come first)
    entities.sort(key=lambda e: e.span[0])
    return entities
