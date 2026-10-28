"""Entity linking and context building, with Neo4j mocked by the curated files."""
from kgp import graphrag
from kgp.storage import read_jsonl_dir


def make_rag(monkeypatch):
    nodes = list(read_jsonl_dir("/data/curated/nodes"))
    monkeypatch.setattr(graphrag, "read", lambda q, **p: [
        {"id": n["id"], "name": n["name"], "type": n["type"], "aliases": n.get("aliases", [])} for n in nodes])
    return graphrag.GraphRAG()


def test_linking(monkeypatch):
    rag = make_rag(monkeypatch)
    assert [e["name"] for e in rag.link("Who works for the company that acquired Sahara Analytics?")] == ["Sahara Analytics"]
    names = {e["name"] for e in rag.link("Which people studied at the Rabat Institute of Technology?")}
    assert names == {"Rabat Institute of Technology"}
    assert {e["name"] for e in rag.link("How are Karim Benali and Sophie Laurent connected?")} == {"Karim Benali", "Sophie Laurent"}
    assert rag.link("Which technology is used by the most companies?") == []


def test_context():
    ctx = graphrag.GraphRAG.build_context([{"subject": "A", "predicate": "USES", "object": "B", "confidence": 0.9,
                                           "sources": ["d1"], "evidence": ["A uses B."]}])
    assert "[1] A --USES--> B" in ctx and "d1" in ctx
