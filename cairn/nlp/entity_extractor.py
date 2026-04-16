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
that appear most often in cybersecurity intelligence notes:

    ipv4       — 203.0.113.42
    ipv6       — 2001:db8::1
    fqdn       — host.example.com  (min 2 labels, TLD ≥ 2 chars)
    cve        — CVE-2024-12345
    technique  — T1059, T1059.003  (MITRE ATT&CK)
    actor      — name extracted from `actor:` or `group:` frontmatter tags

Spans are character offsets into the original text passed to extract().

Usage::

    from cairn.nlp.entity_extractor import extract, Entity

    entities = extract(body_text, tags=["actor:APT29", "group:Cozy Bear"])
    for e in entities:
        print(e.type, e.value, e.span)
"""

from __future__ import annotations

import dataclasses
import re
from typing import Sequence


# ---------------------------------------------------------------------------
# Entity dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Entity:
    type:  str          # ipv4 | ipv6 | fqdn | cve | technique | actor
    value: str          # canonical string value
    span:  tuple[int, int]  # (start, end) character offsets into the source text


# ---------------------------------------------------------------------------
# Compiled patterns
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
# Public API
# ---------------------------------------------------------------------------

def extract(
    text: str,
    *,
    tags: Sequence[str] | None = None,
) -> list[Entity]:
    """Extract cybersecurity entities from *text* and optionally from *tags*.

    Args:
        text: Markdown body text of a blackboard message.
        tags: Optional YAML frontmatter tag list.  Actor/group tags are parsed
              here; other tag values are ignored.

    Returns:
        Deduplicated list of Entity objects, ordered by span start offset.
        Actor entities extracted from tags have span (0, 0).
    """
    entities: list[Entity] = []
    seen: set[tuple[str, str]] = set()  # (type, value) dedup key

    def _add(entity_type: str, value: str, span: tuple[int, int]) -> None:
        key = (entity_type, value.lower())
        if key not in seen:
            seen.add(key)
            entities.append(Entity(type=entity_type, value=value, span=span))

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
        _add("fqdn", value, span)

    # --- Actors from tags ---
    for tag in (tags or []):
        m = _RE_ACTOR_TAG.match(tag.strip())
        if m:
            actor_name = m.group(1).strip()
            if actor_name:
                _add("actor", actor_name, (0, 0))

    # Sort by span start (actors at 0 come first, then by document order)
    entities.sort(key=lambda e: e.span[0])
    return entities
