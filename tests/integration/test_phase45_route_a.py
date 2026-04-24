import pytest

from cairn.sync.chroma_sync import _procedure_doc_id, search_methodologies, sync_procedures


class _FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []

    def count(self):
        return len(self.docs)

    def upsert(self, ids, documents, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.docs.append({"id": i, "document": d, "metadata": m})

    def query(self, query_texts, n_results=10, where=None, include=None):
        query = (query_texts or [""])[0].lower()
        filtered = []
        for row in self.docs:
            md = row["metadata"]
            if where:
                ok = True
                for k, v in where.items():
                    if md.get(k) != v:
                        ok = False
                        break
                if not ok:
                    continue
            score = 0.9 if query in row["document"].lower() else 0.4
            filtered.append((row, score))

        filtered = filtered[:n_results]
        return {
            "ids": [[r["id"] for r, _ in filtered]],
            "documents": [[r["document"] for r, _ in filtered]],
            "metadatas": [[r["metadata"] for r, _ in filtered]],
            "distances": [[1 - s for _, s in filtered]],
        }


def _write_procedure(path, title, steps):
    path.write_text(
        f"""title: "{title}"
author: "phase45-test"
created_at: "2026-04-24T00:00:00Z"
version: "1.0"
tags: ["triage", "route-a"]
summary: "Procedure summary"
steps:
{chr(10).join([f'  - "{s}"' for s in steps])}
""",
        encoding="utf-8",
    )


def test_route_a_procedure_file_syncs_and_search_returns_kind(tmp_path):
    procedures_dir = tmp_path / "methodologies" / "procedures"
    procedures_dir.mkdir(parents=True)
    p = procedures_dir / "phishing.procedure.yml"
    _write_procedure(p, "Phishing Triage", ["Collect headers", "Pivot on sender infra"])

    collection = _FakeCollection()
    synced, failed = sync_procedures(collection, procedures_dir)

    assert synced == 1
    assert failed == 0

    results = search_methodologies(collection, "phishing", n=5, where={"kind": "procedure"})
    assert len(results) >= 1
    assert results[0]["kind"] == "procedure"
    assert results[0]["title"] == "Phishing Triage"


def test_route_a_kind_filter_excludes_sigma_documents(tmp_path):
    procedures_dir = tmp_path / "methodologies" / "procedures"
    procedures_dir.mkdir(parents=True)
    p = procedures_dir / "network.procedure.yml"
    _write_procedure(p, "Network Triage", ["Collect pcap", "Correlate flows"])

    collection = _FakeCollection()
    sync_procedures(collection, procedures_dir)

    collection.upsert(
        ids=["sigma-1"],
        documents=["Sigma style rule for network discovery"],
        metadatas=[
                {
                    "gitlab_path": "sigma/network/discovery.yml",
                    "commit_sha": "deadbeef",
                    "title": "Sigma Discovery Rule",
                    "status": "experimental",
                    "tags": "sigma",
                    "kind": "sigma",
                }
        ],
    )

    proc = search_methodologies(collection, "network", n=10, where={"kind": "procedure"})
    sig = search_methodologies(collection, "network", n=10, where={"kind": "sigma"})

    assert proc
    assert all(r["kind"] == "procedure" for r in proc)
    assert sig
    assert all(r["kind"] == "sigma" for r in sig)


def test_route_a_procedure_doc_id_is_stable():
    a = _procedure_doc_id("Phishing Triage", "methodologies/procedures/phishing.procedure.yml")
    b = _procedure_doc_id("Phishing Triage", "methodologies/procedures/phishing.procedure.yml")
    c = _procedure_doc_id("Other", "methodologies/procedures/phishing.procedure.yml")

    assert a == b
    assert a != c
