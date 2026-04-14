"""OpenAPI spec fetcher with TTL-based disk cache.

The agent skill fetches /api/spec.json on first use and caches it locally.
On subsequent calls the cache is used if it is still fresh.  When the spec
changes (detected by SHA-256 hash comparison) the skill logs a notice and
updates the cache.  If the server's version string is higher than the
installed skill version, SpecOutdatedError is raised so the orchestration
layer can decide whether to abort.

Cache file location: ~/.cache/cairn/spec_<url_hash>.json by default.
Override via the spec_cache_path constructor argument on BlackboardClient.

Cache file format (JSON):
    {
        "fetched_at": "<ISO8601>",
        "spec_hash":  "<sha256 hex>",
        "spec":       { ... OpenAPI document ... }
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from cairn.skill.exceptions import SpecError, SpecOutdatedError

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 3600  # seconds
_SKILL_VERSION = "0.1.0"


class SpecCache:
    """Manages a local disk cache of the blackboard's OpenAPI spec.

    Args:
        base_url:         Base URL of the blackboard (e.g. 'http://localhost:8000').
        cache_path:       Path to the JSON cache file.  Created on first fetch.
        ttl_seconds:      How long a cached spec is considered fresh.
        http_client:      httpx.AsyncClient to use for fetching (borrowed; not closed here).
    """

    def __init__(
        self,
        base_url: str,
        cache_path: Path,
        ttl_seconds: int = _DEFAULT_TTL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url    = base_url.rstrip("/")
        self._cache_path  = Path(cache_path)
        self._ttl         = ttl_seconds
        self._http        = http_client
        self._spec:       dict[str, Any] | None = None
        self._spec_hash:  str | None = None
        self._fetched_at: float = 0.0

        # Pre-build the operation_id → (method, path) map when spec is loaded.
        self._op_map: dict[str, tuple[str, str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def default_cache_path(base_url: str) -> Path:
        """Return a default cache path under ~/.cache/cairn/."""
        url_hash = hashlib.sha256(base_url.encode()).hexdigest()[:12]
        parsed   = urlparse(base_url)
        slug     = f"{parsed.hostname}_{parsed.port or 80}"
        return Path.home() / ".cache" / "cairn" / f"spec_{slug}_{url_hash}.json"

    async def get_spec(self, *, force: bool = False) -> dict[str, Any]:
        """Return the cached spec, refreshing if stale or forced.

        Raises:
            SpecError:        If the spec cannot be fetched or parsed.
            SpecOutdatedError: If the server version exceeds the skill version.
        """
        if not force and self._spec is not None and self._is_fresh():
            return self._spec

        # Try loading from disk before hitting the network.
        if not force and self._spec is None:
            self._load_from_disk()
            if self._spec is not None and self._is_fresh():
                return self._spec

        await self._fetch()
        return self._spec  # type: ignore[return-value]

    def resolve_url(self, operation_id: str) -> tuple[str, str]:
        """Return (method, full_url) for the given operationId.

        Raises:
            SpecError: If the operation_id is not found in the spec.
        """
        if not self._op_map:
            raise SpecError("Spec has not been loaded yet. Call get_spec() first.")
        if operation_id not in self._op_map:
            raise SpecError(
                f"Operation '{operation_id}' not found in spec. "
                f"Known operations: {sorted(self._op_map)}"
            )
        method, path = self._op_map[operation_id]
        return method, f"{self._base_url}{path}"

    @property
    def spec_hash(self) -> str | None:
        return self._spec_hash

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_fresh(self) -> bool:
        return (time.time() - self._fetched_at) < self._ttl

    def _load_from_disk(self) -> None:
        if not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            self._spec       = data["spec"]
            self._spec_hash  = data["spec_hash"]
            self._fetched_at = _parse_ts(data["fetched_at"])
            self._build_op_map()
            logger.debug("Loaded spec from disk cache %s", self._cache_path)
        except Exception as exc:
            logger.warning("Could not read spec cache from disk: %s", exc)

    def _save_to_disk(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "fetched_at": _iso_now(),
                "spec_hash":  self._spec_hash,
                "spec":       self._spec,
            }
            self._cache_path.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("Could not write spec cache to disk: %s", exc)

    async def _fetch(self) -> None:
        if self._http is None:
            raise SpecError("No HTTP client available for spec fetch.")

        spec_url = f"{self._base_url}/api/spec.json"
        try:
            response = await self._http.get(spec_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SpecError(f"Failed to fetch spec from {spec_url}: {exc}") from exc

        raw   = response.content
        new_hash = hashlib.sha256(raw).hexdigest()

        try:
            new_spec: dict[str, Any] = response.json()
        except Exception as exc:
            raise SpecError(f"Failed to parse spec JSON: {exc}") from exc

        changed = new_hash != self._spec_hash
        if changed and self._spec_hash is not None:
            logger.info("Blackboard spec has changed (new hash %s)", new_hash[:12])
            self._check_version(new_spec)

        self._spec       = new_spec
        self._spec_hash  = new_hash
        self._fetched_at = time.time()
        self._build_op_map()
        self._save_to_disk()

    def _build_op_map(self) -> None:
        """Index operationId → (HTTP method, path) from the spec paths."""
        self._op_map = {}
        if not self._spec:
            return
        for path, methods in self._spec.get("paths", {}).items():
            for method, op in methods.items():
                if method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                    op_id = op.get("operationId")
                    if op_id:
                        self._op_map[op_id] = (method.upper(), path)

    def _check_version(self, spec: dict[str, Any]) -> None:
        server_version = spec.get("info", {}).get("version", "")
        if server_version and server_version > _SKILL_VERSION:
            raise SpecOutdatedError(
                skill_version=_SKILL_VERSION,
                server_version=server_version,
            )


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat()


def _parse_ts(iso: str) -> float:
    from datetime import datetime, timezone
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0
