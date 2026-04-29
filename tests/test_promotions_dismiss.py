"""Regression test for dismiss-not-persisting bug (Bug 004).

The corroboration job re-creates dismissed candidates for the same entity
if it only checked for `pending_review` and `promoted` statuses.
The fix adds `dismissed` to the exclusion set in _find_existing_candidate().
"""
import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_dismissed_entity_not_recreated_by_corroboration(
    agent_key_pair, data_dir, tmp_path
):
    """A dismissed candidate's entity should not be re-created by the corroboration job."""
    import aiosqlite
    from cairn.jobs.corroboration import run_corroboration_job
    from cairn.config import Settings

    agent_id, _ = agent_key_pair
    now = datetime.now(tz=timezone.utc)
    now_iso = now.isoformat()

    # osint.db is already created and schema-initialised by the data_dir fixture
    # (via init_all).  Just insert the test message.
    osint_db = data_dir / "osint.db"
    async with aiosqlite.connect(osint_db) as db:
        await db.execute(
            """
            INSERT INTO messages
                (id, agent_id, message_type, tags, timestamp, ingested_at,
                 tlp_level, promote, body, frontmatter, raw_content)
            VALUES
                ('msg-test-cve-001', ?, 'finding', '["cve"]', ?, ?,
                 'amber', 'none',
                 'Observed CVE-2024-99999 being exploited in the wild.',
                 '{"tags":["cve"]}',
                 'tags: ["cve"]\n---\nObserved CVE-2024-99999 being exploited in the wild.')
            """,
            (agent_id, now_iso, now_iso),
        )
        await db.commit()

    # Manually create a dismissed candidate for the same entity
    async with aiosqlite.connect(data_dir / "index.db") as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO promotion_candidates
                (id, entity, entity_type, topic_db, trigger, status, confidence,
                 source_message_ids, narrative, created_at, updated_at)
            VALUES (?, 'CVE-2024-99999', 'cve', 'osint', 'human',
                    'dismissed', 0.9,
                    '["msg-test-cve-001"]', 'Not actionable.', ?, ?)
            """,
            ("cand-dismissed-001", now_iso, now_iso),
        )
        await db.commit()

    # Run the corroboration job
    from cairn.db.connections import DatabaseManager

    db_mgr = DatabaseManager()
    await db_mgr.open(
        index_path=data_dir / "index.db",
        topic_paths={"osint": osint_db},
    )
    settings = Settings(
        quartz_content_dir=tmp_path / "quartz",
        corroboration_n=1,
        corroboration_window_hours=24,
        promotion_confidence_threshold=0.5,
    )
    await run_corroboration_job(db_mgr, settings)

    # Verify: exactly one dismissed candidate, no new pending/promoted created
    async with aiosqlite.connect(data_dir / "index.db") as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, status FROM promotion_candidates "
            "WHERE lower(entity) = lower('CVE-2024-99999')"
        )
        rows = await cursor.fetchall()

    statuses = [r["status"] for r in rows]
    assert statuses.count("dismissed") == 1, (
        f"Expected 1 dismissed, got: {[(r['id'], r['status']) for r in rows]}"
    )
    assert "pending_review" not in statuses, (
        f"Dismissed entity was re-created: {rows}"
    )
    assert "promoted" not in statuses