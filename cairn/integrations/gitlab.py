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

"""GitLab REST API integration for methodology retrieval.

Works against self-hosted GitLab CE and gitlab.com with identical code —
only the base URL differs, controlled by CAIRN_GITLAB_URL.

Design constraint: this module fetches metadata and raw content from GitLab
but never stores methodology text in the Cairn database.  SQLite stores only
the commit SHA and path; content is always fetched on demand from here.

Usage:
    from cairn.integrations.gitlab import GitLabClient

    async with GitLabClient.from_settings() as gl:
        mf = await gl.fetch_methodology("methodologies/apt29/named-pipe.yml", "abc123")
        raw = await gl.get_file_at_sha("methodologies/apt29/named-pipe.yml", "abc123")
        files = await gl.list_methodologies(tag="lateral-movement")
"""

from __future__ import annotations

import base64
import dataclasses
import logging
from typing import Any
from urllib.parse import quote

import httpx
import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class MethodologyFile:
    """Methodology metadata extracted from a GitLab file.

    Full raw content is intentionally omitted — fetch it explicitly via
    get_file_at_sha() only when needed (e.g. for execution, not for indexing).
    """
    path:        str          # relative path in the GitLab repo
    commit_sha:  str          # commit SHA at which this metadata was read
    title:       str          # from Sigma 'title' field
    description: str          # from Sigma 'description' field
    tags:        list[str]    # from Sigma 'tags' field
    status:      str          # from Sigma 'status' field (proposed/experimental/stable/deprecated)
    methodology_id: str       # from Sigma 'name' field, or derived from path


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class GitLabClient:
    """Async GitLab REST API v4 client scoped to a single project.

    Args:
        base_url:    Base URL of the GitLab instance (no trailing slash).
                     Self-hosted: 'http://gitlab.local'
                     Cloud:       'https://gitlab.com'
        token:       GitLab personal or project access token (read_repository scope).
        project_id:  Numeric project ID or URL-encoded namespace/project path.
        timeout:     HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        project_id: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._base_url   = base_url.rstrip("/")
        self._project_id = _encode_project_id(project_id)
        self._http = httpx.AsyncClient(
            base_url=f"{self._base_url}/api/v4",
            headers={"PRIVATE-TOKEN": token},
            timeout=timeout,
        )

    @classmethod
    def from_settings(cls) -> "GitLabClient":
        """Construct from the application settings singleton."""
        from cairn.config import get_settings
        s = get_settings()
        return cls(
            base_url=s.gitlab_url,
            token=s.gitlab_token,
            project_id=s.gitlab_project_id,
        )

    async def __aenter__(self) -> "GitLabClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Core file operations
    # ------------------------------------------------------------------

    async def get_file_at_sha(self, file_path: str, sha: str) -> str:
        """Fetch the raw content of a file at a specific commit SHA.

        Returns the decoded file content as a UTF-8 string.
        GitLab returns content as base64; this method decodes it transparently.

        Raises:
            httpx.HTTPStatusError: If the file is not found or the token lacks access.
        """
        encoded_path = quote(file_path, safe="")
        response = await self._http.get(
            f"/projects/{self._project_id}/repository/files/{encoded_path}",
            params={"ref": sha},
        )
        response.raise_for_status()
        data = response.json()
        return base64.b64decode(data["content"]).decode("utf-8")

    async def create_or_update_file(
        self,
        file_path: str,
        content: str,
        commit_message: str,
        branch: str = "main",
        author_name: str = "Cairn",
        author_email: str = "cairn@noreply",
    ) -> dict:
        """Create or update a file in the GitLab repository.

        Checks whether the file exists first, then uses the appropriate
        GitLab API endpoint (create vs update).

        Returns a dict with 'file_path', 'branch', and 'commit_id' (the SHA).
        """
        encoded_path = quote(file_path, safe="")
        payload = {
            "branch": branch,
            "content": content,
            "commit_message": commit_message,
            "author_name": author_name,
            "author_email": author_email,
        }

        # Check if file exists to decide create vs update.
        head = await self._http.head(
            f"/projects/{self._project_id}/repository/files/{encoded_path}",
            params={"ref": branch},
        )
        if head.status_code == 200:
            resp = await self._http.put(
                f"/projects/{self._project_id}/repository/files/{encoded_path}",
                json=payload,
            )
        else:
            resp = await self._http.post(
                f"/projects/{self._project_id}/repository/files/{encoded_path}",
                json=payload,
            )
        resp.raise_for_status()
        data = resp.json()
        return {
            "file_path": data.get("file_path", file_path),
            "branch": data.get("branch", branch),
            "commit_id": data.get("content_sha256", ""),  # GitLab returns content hash
        }

    async def get_latest_commit_sha(self, branch: str = "main") -> str:
        """Get the latest commit SHA on a branch."""
        resp = await self._http.get(
            f"/projects/{self._project_id}/repository/branches/{quote(branch, safe='')}",
        )
        resp.raise_for_status()
        return resp.json()["commit"]["id"]

    async def fetch_methodology(self, file_path: str, sha: str) -> MethodologyFile:
        """Fetch a methodology file and extract its Sigma YAML metadata.

        Parses the Sigma rule header fields (title, description, tags, status,
        name) to build the MethodologyFile.  The full raw content is NOT
        included in the return value — call get_file_at_sha() explicitly when
        the content itself is needed for execution.

        Args:
            file_path: Path to the .yml file within the repository.
            sha:       Commit SHA or ref (branch name, tag).

        Returns:
            MethodologyFile with metadata fields populated from Sigma YAML.
        """
        raw = await self.get_file_at_sha(file_path, sha)
        meta = _parse_sigma_metadata(raw)
        return MethodologyFile(
            path=file_path,
            commit_sha=sha,
            title=meta.get("title", ""),
            description=meta.get("description", ""),
            tags=meta.get("tags", []),
            status=meta.get("status", "proposed"),
            methodology_id=meta.get("name") or _path_to_id(file_path),
        )

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    async def list_methodology_files(
        self,
        directory: str = "methodologies",
        ref: str = "HEAD",
        *,
        recursive: bool = True,
    ) -> list[dict[str, Any]]:
        """Return repository tree entries for .yml files in a directory.

        Each entry is a dict from the GitLab tree API with keys:
        id, name, type, path, mode.

        Handles GitLab's 100-item pagination automatically.
        """
        all_items: list[dict] = []
        page = 1
        while True:
            response = await self._http.get(
                f"/projects/{self._project_id}/repository/tree",
                params={
                    "path":      directory,
                    "ref":       ref,
                    "recursive": "true" if recursive else "false",
                    "per_page":  100,
                    "page":      page,
                },
            )
            response.raise_for_status()
            items = response.json()
            all_items.extend(
                item for item in items
                if item.get("type") == "blob" and item.get("name", "").endswith(".yml")
            )
            # GitLab sets X-Next-Page header; empty string means last page.
            next_page = response.headers.get("X-Next-Page", "")
            if not next_page:
                break
            page = int(next_page)
        return all_items

    async def list_methodologies(
        self,
        *,
        tag: str | None = None,
        ref: str = "HEAD",
        directory: str | None = None,
    ) -> list[MethodologyFile]:
        """List all methodology files, optionally filtered by a Sigma tag.

        Fetches each file's content to parse metadata, so this is an O(N)
        operation.  Use sparingly — prefer the ChromaDB search endpoint for
        discovery in hot paths.

        Args:
            tag:       If set, only return files whose Sigma tags include this value.
            ref:       Commit SHA or branch name to list at.
            directory: Override the repository directory to scan.
        """
        from cairn.config import get_settings
        dir_ = directory or get_settings().gitlab_methodology_dir
        items = await self.list_methodology_files(directory=dir_, ref=ref)

        results: list[MethodologyFile] = []
        for item in items:
            try:
                mf = await self.fetch_methodology(item["path"], ref)
                if tag is None or tag in mf.tags:
                    results.append(mf)
            except Exception:
                logger.warning(
                    "Failed to fetch methodology %s at ref %s — skipping",
                    item["path"],
                    ref,
                )
        return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _encode_project_id(project_id: str) -> str:
    """URL-encode a namespaced project path; leave numeric IDs unchanged."""
    try:
        int(project_id)
        return project_id  # numeric ID — no encoding needed
    except ValueError:
        return quote(project_id, safe="")


def _path_to_id(file_path: str) -> str:
    """Derive a stable methodology_id from a file path when no 'name' field exists."""
    # e.g. 'methodologies/apt29/named-pipe.yml' → 'apt29/named-pipe'
    stem = file_path.rsplit(".", 1)[0]
    # Strip leading directory if it matches the default methodology dir.
    for prefix in ("methodologies/", "sigma/"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    return stem


def _parse_sigma_metadata(content: str) -> dict[str, Any]:
    """Extract metadata fields from a Sigma rule YAML file.

    Sigma rules are top-level YAML documents (no --- frontmatter delimiters).
    Fields extracted: title, description, name, tags, status.

    Returns an empty dict on any parse failure — callers must handle missing
    fields gracefully.
    """
    try:
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            return {}

        tags = data.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        elif not isinstance(tags, list):
            tags = []

        return {
            "title":       str(data.get("title", "")),
            "description": str(data.get("description", "")),
            "name":        str(data.get("name", "")),
            "tags":        [str(t) for t in tags if t],
            "status":      str(data.get("status", "proposed")),
        }
    except Exception:
        logger.debug("Failed to parse Sigma YAML metadata", exc_info=True)
        return {}
