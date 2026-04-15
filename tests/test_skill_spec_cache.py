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

"""Tests for cairn.skill.spec_cache.SpecCache."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from cairn.skill.spec_cache import SpecCache
from cairn.skill.exceptions import SpecError, SpecOutdatedError


# Minimal valid OpenAPI spec fixture.
_SPEC_V1 = {
    "openapi": "3.1.0",
    "info": {"title": "Cairn", "version": "0.1.0"},
    "paths": {
        "/messages": {
            "post": {"operationId": "post_message", "summary": "Post"},
            "get":  {"operationId": "query_messages", "summary": "Query"},
        },
        "/messages/{message_id}": {
            "get":   {"operationId": "get_message", "summary": "Get"},
            "patch": {"operationId": "flag_for_promotion", "summary": "Promote"},
        },
        "/stream": {
            "get": {"operationId": "subscribe_stream", "summary": "Stream"},
        },
    },
}

_SPEC_V2 = {**_SPEC_V1, "info": {"title": "Cairn", "version": "0.2.0"}}


@pytest.fixture
def cache_path(tmp_path) -> Path:
    return tmp_path / "spec.json"


@pytest.fixture
def cache(cache_path) -> SpecCache:
    return SpecCache(
        base_url="http://localhost:8000",
        cache_path=cache_path,
        ttl_seconds=60,
    )


class TestSpecCacheDisk:
    def test_loads_from_disk_on_second_instantiation(self, tmp_path):
        path = tmp_path / "spec.json"
        spec_bytes = json.dumps(_SPEC_V1).encode()
        spec_hash  = hashlib.sha256(spec_bytes).hexdigest()
        path.write_text(json.dumps({
            "fetched_at": "2026-04-14T10:00:00+00:00",
            "spec_hash":  spec_hash,
            "spec":       _SPEC_V1,
        }))
        c = SpecCache("http://localhost:8000", path, ttl_seconds=3600)
        c._load_from_disk()
        assert c._spec is not None
        assert c._spec_hash == spec_hash

    def test_stale_when_fetched_at_old(self, cache):
        cache._spec       = _SPEC_V1
        cache._fetched_at = time.time() - 9999
        assert not cache._is_fresh()

    def test_fresh_when_fetched_recently(self, cache):
        cache._spec       = _SPEC_V1
        cache._fetched_at = time.time()
        assert cache._is_fresh()


class TestOpMap:
    def test_resolve_known_operation(self, cache):
        cache._spec = _SPEC_V1
        cache._build_op_map()
        method, url = cache.resolve_url("post_message")
        assert method == "POST"
        assert url.endswith("/messages")

    def test_resolve_patch_operation(self, cache):
        cache._spec = _SPEC_V1
        cache._build_op_map()
        method, url = cache.resolve_url("flag_for_promotion")
        assert method == "PATCH"
        assert "{message_id}" in url

    def test_unknown_operation_raises(self, cache):
        cache._spec = _SPEC_V1
        cache._build_op_map()
        with pytest.raises(SpecError, match="not found in spec"):
            cache.resolve_url("nonexistent_op")

    def test_resolve_before_load_raises(self, cache):
        with pytest.raises(SpecError, match="not been loaded"):
            cache.resolve_url("post_message")


class TestVersionCheck:
    def test_no_error_when_versions_equal(self, cache):
        cache._spec = _SPEC_V1
        cache._spec_hash = "old_hash"
        # Should not raise.
        cache._check_version(_SPEC_V1)

    def test_raises_when_server_version_newer(self, cache):
        with pytest.raises(SpecOutdatedError) as exc_info:
            cache._check_version(_SPEC_V2)
        assert exc_info.value.server_version == "0.2.0"
        assert exc_info.value.skill_version  == "0.1.0"
