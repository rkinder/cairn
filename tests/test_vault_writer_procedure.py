from pathlib import Path

import pytest

from cairn.vault.writer import write_procedure


@pytest.mark.asyncio
async def test_note_routed_to_procedures_subdir(tmp_path: Path):
    result = await write_procedure(
        tmp_path,
        title="Phishing Triage",
        steps=["Collect mailbox logs", "Block sender domain"],
        tags=["email"],
        narrative="Follow these steps",
        source_message_ids=["msg-1"],
        promoted_at="2026-04-20T00:00:00Z",
        author="analyst-1",
        severity="high",
        low_confidence=False,
    )
    assert "procedures/" in result.kb_rel


@pytest.mark.asyncio
async def test_vault_note_has_procedure_source(tmp_path: Path):
    result = await write_procedure(
        tmp_path,
        title="IR Steps",
        steps=["Isolate endpoint immediately", "Capture volatile memory artifacts"],
        tags=[],
        narrative="IR summary",
        source_message_ids=["msg-2"],
        promoted_at="2026-04-20T00:00:00Z",
        author=None,
        severity=None,
        low_confidence=False,
    )
    note = (tmp_path / result.kb_rel).read_text(encoding="utf-8")
    assert "procedure_source: blackboard" in note


@pytest.mark.asyncio
async def test_prose_body_low_confidence(tmp_path: Path):
    result = await write_procedure(
        tmp_path,
        title="Prose Procedure",
        steps=[],
        tags=[],
        narrative="Some prose only",
        source_message_ids=["msg-3"],
        promoted_at="2026-04-20T00:00:00Z",
        author=None,
        severity=None,
        low_confidence=True,
    )
    note = (tmp_path / result.kb_rel).read_text(encoding="utf-8")
    assert "low_confidence: true" in note


@pytest.mark.asyncio
async def test_steps_rendered_numbered(tmp_path: Path):
    result = await write_procedure(
        tmp_path,
        title="Rendered Steps",
        steps=["First major action", "Second major action"],
        tags=[],
        narrative="Summary",
        source_message_ids=["msg-4"],
        promoted_at="2026-04-20T00:00:00Z",
        author=None,
        severity=None,
        low_confidence=False,
    )
    note = (tmp_path / result.kb_rel).read_text(encoding="utf-8")
    assert "## Steps" in note
    assert "1. First major action" in note
    assert "2. Second major action" in note


@pytest.mark.asyncio
async def test_write_result_success_on_disk_write(tmp_path: Path):
    result = await write_procedure(
        tmp_path,
        title="Disk Success",
        steps=["First major action", "Second major action"],
        tags=[],
        narrative="Summary",
        source_message_ids=["msg-5"],
        promoted_at="2026-04-20T00:00:00Z",
        author=None,
        severity=None,
        low_confidence=False,
    )
    assert bool(result.kb_rel)


@pytest.mark.asyncio
async def test_deduplication_updates_existing(tmp_path: Path):
    result1 = await write_procedure(
        tmp_path,
        title="Dup Title",
        steps=["First major action", "Second major action"],
        tags=[],
        narrative="Summary one",
        source_message_ids=["msg-6"],
        promoted_at="2026-04-20T00:00:00Z",
        author=None,
        severity=None,
        low_confidence=False,
    )
    result2 = await write_procedure(
        tmp_path,
        title="Dup Title",
        steps=["Third major action", "Fourth major action"],
        tags=[],
        narrative="Summary two",
        source_message_ids=["msg-7"],
        promoted_at="2026-04-21T00:00:00Z",
        author=None,
        severity=None,
        low_confidence=False,
    )
    assert result1.kb_rel == result2.kb_rel
    files = list((tmp_path / "cairn" / "procedures").glob("Dup Title.md"))
    assert len(files) == 1


@pytest.mark.asyncio
async def test_tags_in_frontmatter(tmp_path: Path):
    result = await write_procedure(
        tmp_path,
        title="Tags Test",
        steps=["First major action", "Second major action"],
        tags=["email", "triage"],
        narrative="Summary",
        source_message_ids=["msg-8"],
        promoted_at="2026-04-20T00:00:00Z",
        author=None,
        severity=None,
        low_confidence=False,
    )
    note = (tmp_path / result.kb_rel).read_text(encoding="utf-8")
    assert "tags:" in note
    assert "procedure" in note
    assert "cairn-promoted" in note
    assert "email" in note
    assert "triage" in note
