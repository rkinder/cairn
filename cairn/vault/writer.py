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

"""Obsidian vault note writer for promoted blackboard entities (Phase 4).

Produces structured Obsidian-compatible markdown notes and handles
deduplication — if a note for the entity already exists, a new ## Evidence
entry is appended and ``last_updated`` in the frontmatter is refreshed rather
than creating a duplicate file.

Note structure
--------------

    ---
    title: APT29
    tags: [threat-actor, cairn-promoted]
    entity_type: actor
    confidence: 0.91
    sources:
      - msg-00234
      - msg-00198
    promoted_at: 2026-04-14T10:32:00Z
    last_updated: 2026-04-14T10:32:00Z
    ---

    ## Summary

    <narrative — analyst-edited or auto-generated>

    ## Evidence

    - **2026-04-14T10:32:00Z** — [[osint]] — msg-00234, msg-00198

    ## Related

    [[Cobalt Strike]] [[lateral-movement]]

Public API
----------
write_note(vault_path, entity, entity_type, narrative, source_message_ids,
           confidence, promoted_at, tags, related_links)
    Write or update a vault note and return the relative path within the vault.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cairn.vault.couchdb_sync import CouchDBVaultClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Subdirectory within the vault for Cairn-promoted notes
_CAIRN_SUBDIR = "cairn"

@dataclass(frozen=True)
class WriteResult:
    """Result of a write_note() call."""
    vault_rel: str
    couchdb_synced: bool
    couchdb_error: str | None


# Regex to find and update last_updated in frontmatter
_RE_LAST_UPDATED = re.compile(r"^last_updated\s*:.*$", re.MULTILINE)

# Regex to find the ## Evidence section so we can append to it
_RE_EVIDENCE_SECTION = re.compile(r"(## Evidence\s*\n)", re.IGNORECASE)

# Regex to find the ## Source Findings section
_RE_SOURCE_FINDINGS_SECTION = re.compile(r"(## Source Findings\s*\n)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def write_note(
    vault_root: Path,
    *,
    entity: str,
    entity_type: str,
    narrative: str,
    source_message_ids: list[str],
    confidence: float | None,
    promoted_at: str,
    tags: list[str] | None = None,
    related_links: list[str] | None = None,
    domain: str | None = None,
    couchdb_client: "CouchDBVaultClient | None" = None,
    source_findings: list[dict] | None = None,
) -> WriteResult:
    """Write or update an Obsidian vault note for a promoted entity.

    Performs a disk write first (primary store), then a best-effort CouchDB
    sync so Obsidian LiveSync clients receive the note immediately.  CouchDB
    failures are logged but do not prevent the promotion from completing.

    Args:
        vault_root:         Absolute path to the Obsidian vault root.
        entity:             Canonical entity value (e.g. "APT29", "203.0.113.1").
        entity_type:        Entity type string — see entity_extractor.py.
        narrative:          Markdown body for the ## Summary section.
        source_message_ids: Blackboard message IDs that produced this promotion.
        confidence:         Promotion confidence score (0–1) or None.
        promoted_at:        ISO8601 timestamp of the promotion event.
        tags:               Additional Obsidian tags (beyond auto-generated ones).
        related_links:      Pre-resolved wikilinks for the ## Related section.
        domain:             Optional IT domain hint (Phase 4.2) — routes the
                            note into ``cairn/{domain}/`` when set.
        couchdb_client:     Optional CouchDB client for LiveSync sync (Phase 4.4).
                            When None, disk-only write; ``couchdb_synced`` is False.
        source_findings:    Full message bodies from source messages. Each dict
                            contains ``agent_id``, ``timestamp``, and ``body``.
                            Rendered as a ``## Source Findings`` section so the
                            vault note preserves the complete original content.

    Returns:
        WriteResult with vault_rel path and CouchDB sync status.
    """
    # Determine target directory within the vault.
    if domain:
        target_dir = vault_root / _CAIRN_SUBDIR / domain
        vault_rel_prefix = f"{_CAIRN_SUBDIR}/{domain}"
    else:
        target_dir = vault_root / _CAIRN_SUBDIR
        vault_rel_prefix = _CAIRN_SUBDIR

    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(entity)
    note_file = target_dir / f"{safe_name}.md"
    vault_rel = f"{vault_rel_prefix}/{safe_name}.md"

    now_iso = _now_iso()

    if note_file.exists():
        _update_existing_note(
            note_file,
            source_message_ids=source_message_ids,
            promoted_at=promoted_at,
            now_iso=now_iso,
            source_findings=source_findings,
        )
        logger.info("vault/writer: updated existing note %s", vault_rel)
    else:
        content = _build_new_note(
            entity=entity,
            entity_type=entity_type,
            narrative=narrative,
            source_message_ids=source_message_ids,
            confidence=confidence,
            promoted_at=promoted_at,
            tags=tags or [],
            related_links=related_links or [],
            now_iso=now_iso,
            source_findings=source_findings,
        )
        note_file.write_text(content, encoding="utf-8")
        logger.info("vault/writer: created new note %s", vault_rel)

    # CouchDB sync — best effort, disk is the primary store
    couchdb_synced = False
    couchdb_error: str | None = None

    if couchdb_client is not None:
        stat = note_file.stat()
        ctime_ms = int(stat.st_ctime * 1000)
        mtime_ms = int(stat.st_mtime * 1000)
        file_content = note_file.read_text(encoding="utf-8")

        put_result = await couchdb_client.put_note(
            vault_rel_path=vault_rel,
            content=file_content,
            ctime_ms=ctime_ms,
            mtime_ms=mtime_ms,
        )
        couchdb_synced = put_result.success
        couchdb_error = put_result.error
        if not put_result.success:
            logger.warning(
                "vault/writer: CouchDB sync failed for %s: %s",
                vault_rel,
                couchdb_error,
            )

    return WriteResult(
        vault_rel=vault_rel,
        couchdb_synced=couchdb_synced,
        couchdb_error=couchdb_error,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_new_note(
    *,
    entity: str,
    entity_type: str,
    narrative: str,
    source_message_ids: list[str],
    confidence: float | None,
    promoted_at: str,
    tags: list[str],
    related_links: list[str],
    now_iso: str,
    source_findings: list[dict] | None = None,
) -> str:
    """Render the full markdown content for a brand-new vault note."""

    # Build the tags list: always include entity_type and cairn-promoted
    all_tags = list(dict.fromkeys(
        [_entity_type_to_tag(entity_type), "cairn-promoted"] + tags
    ))
    tags_yaml = "[" + ", ".join(all_tags) + "]"

    # Sources list in YAML
    if source_message_ids:
        sources_yaml = "\n" + "\n".join(f"  - {mid}" for mid in source_message_ids)
    else:
        sources_yaml = " []"

    conf_line = f"confidence: {confidence:.2f}\n" if confidence is not None else ""

    # Evidence entry
    evidence_entry = _format_evidence_entry(
        promoted_at=promoted_at,
        source_message_ids=source_message_ids,
    )

    # Related section
    related_section = ""
    if related_links:
        related_section = "\n## Related\n\n" + "  ".join(related_links) + "\n"

    narrative_body = narrative.strip() if narrative.strip() else "_No summary provided._"

    # Source Findings section — full message bodies from source messages
    source_findings_section = ""
    if source_findings:
        source_findings_section = "\n## Source Findings\n\n" + _format_source_findings_block(source_findings) + "\n"

    return (
        f"---\n"
        f"title: {entity}\n"
        f"tags: {tags_yaml}\n"
        f"entity_type: {entity_type}\n"
        f"{conf_line}"
        f"sources:{sources_yaml}\n"
        f"promoted_at: {promoted_at}\n"
        f"last_updated: {now_iso}\n"
        f"---\n"
        f"\n"
        f"## Summary\n"
        f"\n"
        f"{narrative_body}\n"
        f"{source_findings_section}"
        f"\n"
        f"## Evidence\n"
        f"\n"
        f"{evidence_entry}\n"
        f"{related_section}"
    )


def _update_existing_note(
    note_file: Path,
    *,
    source_message_ids: list[str],
    promoted_at: str,
    now_iso: str,
    source_findings: list[dict] | None = None,
) -> None:
    """Append a new Evidence entry and refresh last_updated."""
    content = note_file.read_text(encoding="utf-8")

    # Update last_updated in frontmatter
    content = _RE_LAST_UPDATED.sub(f"last_updated: {now_iso}", content)

    # Append source findings before ## Evidence (create section if missing)
    if source_findings:
        new_findings_text = _format_source_findings_block(source_findings)
        has_source_section = bool(_RE_SOURCE_FINDINGS_SECTION.search(content))
        ev_match = _RE_EVIDENCE_SECTION.search(content)
        if ev_match:
            insert_at = ev_match.start()
            if has_source_section:
                content = content[:insert_at] + new_findings_text + "\n\n" + content[insert_at:]
            else:
                content = content[:insert_at] + "## Source Findings\n\n" + new_findings_text + "\n\n" + content[insert_at:]
        else:
            if has_source_section:
                content = content.rstrip() + "\n\n" + new_findings_text + "\n"
            else:
                content = content.rstrip() + "\n\n## Source Findings\n\n" + new_findings_text + "\n"

    # Append to ## Evidence section
    evidence_entry = _format_evidence_entry(
        promoted_at=promoted_at,
        source_message_ids=source_message_ids,
    )
    match = _RE_EVIDENCE_SECTION.search(content)
    if match:
        insert_at = match.end()
        content = content[:insert_at] + evidence_entry + "\n" + content[insert_at:]
    else:
        # No Evidence section — append at end
        content = content.rstrip() + "\n\n## Evidence\n\n" + evidence_entry + "\n"

    note_file.write_text(content, encoding="utf-8")


def _format_source_findings_block(source_findings: list[dict]) -> str:
    """Render source message bodies as markdown subsections."""
    parts = []
    for finding in source_findings:
        agent = finding.get("agent_id") or "unknown"
        ts = finding.get("timestamp") or ""
        body = (finding.get("body") or "").strip()
        header = f"### Finding from {agent} ({ts})" if ts else f"### Finding from {agent}"
        parts.append(f"{header}\n\n{body}")
    return "\n\n".join(parts)


def _format_evidence_entry(
    *,
    promoted_at: str,
    source_message_ids: list[str],
) -> str:
    """Format a single evidence list item."""
    ids_str = ", ".join(source_message_ids) if source_message_ids else "—"
    return f"- **{promoted_at}** — {ids_str}"


def _entity_type_to_tag(entity_type: str) -> str:
    mapping = {
        # Cybersecurity entity types (Phase 4.1 and earlier)
        "ipv4":      "ip-address",
        "ipv6":      "ip-address",
        "fqdn":      "hostname",
        "cve":       "vulnerability",
        "technique": "mitre-attack",
        "actor":     "threat-actor",
        # IT domain entity types (Phase 4.2)
        "arn":                    "aws-resource",
        "aws_account_id":         "aws-account",
        "aws_region":             "aws-region",
        "azure_subscription_id":  "azure-subscription",
        "azure_resource_group":   "azure-resource-group",
        "cidr":                   "network-range",
        "vlan":                   "vlan",
        "cyberark_safe":          "pam-safe",
    }
    return mapping.get(entity_type, entity_type)


def _safe_filename(entity: str) -> str:
    """Strip characters that are not safe in filenames across platforms."""
    # Replace path separators and common problematic chars
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", entity)
    # Collapse multiple underscores
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe or "unnamed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
