import time

import pytest

from cairn.sync.chroma_sync import search_methodologies, sync_procedures


class _FakeCollection:
    def __init__(self):
        self.docs = []

    def count(self):
        return len(self.docs)

    def upsert(self, ids, documents, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.docs.append({"id": i, "document": d, "metadata": m})

    def query(self, query_texts, n_results=10, where=None, include=None):
        q = (query_texts or [""])[0].lower()
        rows = []
        for item in self.docs:
            md = item["metadata"]
            if where and any(md.get(k) != v for k, v in where.items()):
                continue
            score = 0.95 if q in item["document"].lower() else 0.5
            rows.append((item, score))
        rows = rows[:n_results]
        return {
            "ids": [[r["id"] for r, _ in rows]],
            "documents": [[r["document"] for r, _ in rows]],
            "metadatas": [[r["metadata"] for r, _ in rows]],
            "distances": [[1 - s for _, s in rows]],
        }


def _mk_procedure_file(path, i: int):
    path.write_text(
        f"""title: "Synthetic Procedure {i}"
author: "perf-test"
created_at: "2026-04-24T00:00:00Z"
version: "1.0"
tags: ["perf", "procedure"]
summary: "Synthetic perf procedure"
steps:
  - "Collect host telemetry for case {i}"
  - "Correlate process lineage and network indicators for case {i}"
""",
        encoding="utf-8",
    )


@pytest.mark.performance
def test_sync_procedures_100_files_under_60s(tmp_path):
    procedures = tmp_path / "methodologies" / "procedures"
    procedures.mkdir(parents=True)
    for i in range(100):
        _mk_procedure_file(procedures / f"proc_{i}.procedure.yml", i)

    col = _FakeCollection()
    t0 = time.perf_counter()
    synced, failed = sync_procedures(col, procedures)
    elapsed = time.perf_counter() - t0

    assert failed == 0
    assert synced == 100
    assert elapsed <= 60.0


@pytest.mark.performance
def test_methodology_search_kind_procedure_p95_under_200ms(tmp_path):
    procedures = tmp_path / "methodologies" / "procedures"
    procedures.mkdir(parents=True)
    for i in range(100):
        _mk_procedure_file(procedures / f"proc_{i}.procedure.yml", i)

    col = _FakeCollection()
    sync_procedures(col, procedures)

    runs = []
    for _ in range(10):
        t0 = time.perf_counter()
        results = search_methodologies(col, "Synthetic Procedure", n=20, where={"kind": "procedure"})
        runs.append((time.perf_counter() - t0) * 1000.0)
        assert results

    runs.sort()
    p95 = runs[max(0, int(len(runs) * 0.95) - 1)]
    assert p95 <= 200.0
