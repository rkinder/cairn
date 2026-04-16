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

"""Wikilink resolver for Obsidian vault notes.

Scans the vault directory to find existing notes whose filename (stem) or
frontmatter ``aliases`` list matches the entity value being promoted.  If a
match is found, the canonical ``[[note-title]]`` wikilink is returned so the
new promotion note can link back to the existing note graph.  If no match is
found, an ``[[entity-value]]`` wikilink is returned that Obsidian renders as a
red (unresolved) link — a useful signal that a note should be created later.

The resolver maintains a **run-duration cache** (an in-memory dict populated
on first use) so the vault directory is scanned only once per process
lifetime, or until ``invalidate()`` is called.  The cache maps both the
filename stem and all declared aliases to the canonical note title.

Usage::

    from cairn.vault.wikilink_resolver import WikilinkResolver

    resolver = WikilinkResolver(vault_path)
    link = resolver.resolve("APT29")       # "[[APT29]]" if note exists
    link = resolver.resolve("203.0.113.1") # "[[203.0.113.1]]" (unresolved)
    resolver.register("New Note Title")    # add a just-created note to cache
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches the YAML frontmatter block at the very start of a markdown file.
_RE_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)

# Matches 'aliases: [foo, bar]' (flow style) or multiline block style list items.
_RE_ALIASES_FLOW  = re.compile(r"^aliases\s*:\s*\[([^\]]*)\]", re.MULTILINE | re.IGNORECASE)
_RE_ALIASES_BLOCK = re.compile(r"^aliases\s*:\s*\n((?:\s+-[^\n]+\n?)+)", re.MULTILINE | re.IGNORECASE)
_RE_LIST_ITEM     = re.compile(r"-\s+(.+)")


class WikilinkResolver:
    """Scan the vault once, then resolve entity values to ``[[wikilinks]]``.

    Args:
        vault_path: Path to the Obsidian vault root directory.
    """

    def __init__(self, vault_path: Path) -> None:
        self._vault_path = vault_path
        # Maps lower-cased key → canonical note title (stem of the .md file)
        self._cache: dict[str, str] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, entity_value: str) -> str:
        """Return ``[[canonical-title]]`` if a matching note exists, else
        ``[[entity-value]]`` (an unresolved wikilink Obsidian renders in red).
        """
        cache = self._get_cache()
        canonical = cache.get(entity_value.lower())
        if canonical:
            return f"[[{canonical}]]"
        return f"[[{entity_value}]]"

    def register(self, note_title: str, aliases: list[str] | None = None) -> None:
        """Add a freshly-created note to the run-duration cache.

        Call this immediately after writing a new vault note so subsequent
        resolvers in the same process can find it.

        Args:
            note_title: Stem of the new .md file (without extension).
            aliases:    Optional alias list from the note frontmatter.
        """
        cache = self._get_cache()
        cache[note_title.lower()] = note_title
        for alias in (aliases or []):
            cache[alias.strip().lower()] = note_title

    def invalidate(self) -> None:
        """Drop the in-memory cache so the vault is re-scanned on next use."""
        self._cache = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_cache(self) -> dict[str, str]:
        if self._cache is None:
            self._cache = self._build_cache()
        return self._cache

    def _build_cache(self) -> dict[str, str]:
        """Walk the vault and index all note titles and aliases."""
        cache: dict[str, str] = {}

        if not self._vault_path.is_dir():
            logger.warning("Vault path does not exist or is not a directory: %s", self._vault_path)
            return cache

        for md_file in self._vault_path.rglob("*.md"):
            # Skip hidden Obsidian internals (.obsidian/, .trash/, etc.)
            if any(part.startswith(".") for part in md_file.parts):
                continue

            title = md_file.stem  # filename without extension

            # Register the title itself
            cache[title.lower()] = title

            # Parse frontmatter for aliases
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
                for alias in _parse_aliases(content):
                    cache[alias.strip().lower()] = title
            except OSError:
                pass  # unreadable file — index title only

        logger.debug("WikilinkResolver: indexed %d keys from %s", len(cache), self._vault_path)
        return cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_aliases(content: str) -> list[str]:
    """Extract the aliases list from YAML frontmatter, if present."""
    fm_match = _RE_FRONTMATTER.match(content)
    if not fm_match:
        return []

    fm_text = fm_match.group(1)

    # Flow style: aliases: [APT29, "Cozy Bear"]
    flow_match = _RE_ALIASES_FLOW.search(fm_text)
    if flow_match:
        raw = flow_match.group(1)
        return [a.strip().strip('"\'') for a in raw.split(",") if a.strip()]

    # Block style:
    # aliases:
    #   - APT29
    #   - Cozy Bear
    block_match = _RE_ALIASES_BLOCK.search(fm_text)
    if block_match:
        block = block_match.group(1)
        return [m.group(1).strip().strip('"\'') for m in _RE_LIST_ITEM.finditer(block)]

    return []
