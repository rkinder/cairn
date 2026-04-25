import pytest
import time
from pathlib import Path

from cairn.sync.chroma_sync import sync_procedures, search_methodologies

class FakeCollection:
    def __init__(self):
        self.docs = []

    def count(self):
        return len(self.docs)

    def upsert(self, ids, documents, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.docs.append({"id": i, "document": d, "metadata": m})

    def query(self, query_texts, n_results=10, where=None, include=None):
        return {
            "ids": [[r["id"] for r in self.docs[:n_results]]],
            "documents": [[r["document"] for r in self.docs[:n_results]]],
            "metadatas": [[r["metadata"] for r in self.docs[:n_results]]],
            "distances": [[0.1 for _ in self.docs[:n_results]]],
        }

@pytest.mark.performance
def test_sync_procedures_perf(tmp_path):
    proc_dir = tmp_path / "procedures"
    proc_dir.mkdir()
    
    for i in range(100):
        f = proc_dir / f"proc_{i}.procedure.yml"
        f.write_text(f"""
title: "Proc {i}"
author: "perf"
created_at: "2026-04-24T00:00:00Z"
version: "1.0"
tags: ["triage", "perf"]
summary: "Performance procedure"
steps:
  - "Step 1"
  - "Step 2"
""")
        
    collection = FakeCollection()
    
    start_time = time.time()
    synced, failed = sync_procedures(collection, proc_dir)
    end_time = time.time()
    
    assert synced == 100
    assert failed == 0
    assert (end_time - start_time) <= 60.0

@pytest.mark.performance
def test_search_procedures_perf():
    collection = FakeCollection()
    
    for i in range(100):
        collection.docs.append({
            "id": f"id_{i}",
            "document": f"Doc {i}",
            "metadata": {"kind": "procedure", "title": f"Title {i}", "tags": "perf", "gitlab_path": "path"}
        })
        
    runs = 10
    latencies = []
    
    for _ in range(runs):
        start = time.perf_counter()
        search_methodologies(collection, "Title", n=10, where={"kind": "procedure"})
        end = time.perf_counter()
        latencies.append((end - start) * 1000) # ms
        
    latencies.sort()
    p95 = latencies[int(0.95 * len(latencies))]
    
    assert p95 <= 200.0
