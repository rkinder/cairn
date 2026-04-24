from __future__ import annotations

from unittest.mock import MagicMock

from cairn.sync.chroma_sync import _procedure_doc_id, search_methodologies, sync_procedures


def _write_proc(path, title: str = "Proc") -> None:
    path.write_text(
        f"""
        title: {title}
        tags: [a, b]
        steps:
          - First meaningful step for procedure sync.
          - Second meaningful step for procedure sync.
        description: Example description
        author: analyst
        severity: medium
        """,
        encoding="utf-8",
    )


def test_valid_file_ingested(tmp_path) -> None:
    f = tmp_path / "one.procedure.yml"
    _write_proc(f)
    collection = MagicMock()
    synced, failed = sync_procedures(collection, tmp_path)
    assert (synced, failed) == (1, 0)
    assert collection.upsert.call_count == 1


def test_invalid_file_skipped_counted(tmp_path) -> None:
    f = tmp_path / "bad.procedure.yml"
    f.write_text("title: bad\nsteps: [only one]", encoding="utf-8")
    collection = MagicMock()
    synced, failed = sync_procedures(collection, tmp_path)
    assert (synced, failed) == (0, 1)
    collection.upsert.assert_not_called()


def test_metadata_has_kind_procedure(tmp_path) -> None:
    f = tmp_path / "meta.procedure.yml"
    _write_proc(f)
    collection = MagicMock()
    sync_procedures(collection, tmp_path)
    kwargs = collection.upsert.call_args.kwargs
    assert kwargs["metadatas"][0]["kind"] == "procedure"


def test_metadata_tags_csv(tmp_path) -> None:
    f = tmp_path / "tags.procedure.yml"
    _write_proc(f)
    collection = MagicMock()
    sync_procedures(collection, tmp_path)
    kwargs = collection.upsert.call_args.kwargs
    assert kwargs["metadatas"][0]["tags"] == "a,b"


def test_doc_id_stable() -> None:
    a = _procedure_doc_id("X", "path/file.procedure.yml")
    b = _procedure_doc_id("X", "path/file.procedure.yml")
    assert a == b


def test_empty_dir_returns_zero_zero(tmp_path) -> None:
    collection = MagicMock()
    assert sync_procedures(collection, tmp_path) == (0, 0)


def test_multiple_files_all_valid(tmp_path) -> None:
    for i in range(3):
        _write_proc(tmp_path / f"{i}.procedure.yml", title=f"p{i}")
    collection = MagicMock()
    assert sync_procedures(collection, tmp_path) == (3, 0)
    assert collection.upsert.call_count == 3


def test_one_invalid_in_batch(tmp_path) -> None:
    for i in range(4):
        _write_proc(tmp_path / f"ok{i}.procedure.yml", title=f"p{i}")
    (tmp_path / "bad.procedure.yml").write_text("title: bad\nsteps: [one]", encoding="utf-8")
    collection = MagicMock()
    assert sync_procedures(collection, tmp_path) == (4, 1)


def test_search_methodologies_forwards_where() -> None:
    collection = MagicMock()
    collection.count.return_value = 1
    collection.query.return_value = {
        "ids": [["x"]],
        "metadatas": [[{"gitlab_path": "p", "commit_sha": "s", "title": "t", "tags": "a,b", "status": "", "kind": "procedure"}]],
        "distances": [[0.2]],
    }
    out = search_methodologies(collection, "query", n=5, where={"kind": "procedure"})
    assert out[0]["kind"] == "procedure"
    assert collection.query.call_args.kwargs["where"] == {"kind": "procedure"}
