"""End-to-end test without Docker/Neo4j: crawl -> extract (rules) -> resolve -> python ETL -> quality vs gold."""
from kgp import evaluate
from kgp.resolution import normalize
from kgp.rule_extractor import extract
from kgp.storage import read_jsonl_dir


def test_rule_extractor_patterns():
    r = extract("Amina El Fassi founded Atlas Dynamics in 2018. A. El Fassi is the CEO of Atlas Dynamics Inc.")
    triples = {(x["subject"], x["predicate"], x["object"]) for x in r["relations"]}
    assert ("Amina El Fassi", "FOUNDED", "Atlas Dynamics") in triples
    assert ("A. El Fassi", "WORKS_FOR", "Atlas Dynamics Inc") in triples


def test_normalize():
    assert normalize("Atlas Dynamics Inc") == "atlas dynamics"
    assert normalize("Sahara Analytics SARL") == "sahara analytics"


def test_extraction_quality_with_resolution():
    on, off = evaluate.eval_extraction()
    assert on["f1"] >= 0.95
    assert on["duplicate_nodes"] == 0
    assert off["duplicate_nodes"] > on["duplicate_nodes"]  # resolution removes duplicates


def test_curated_graph():
    nodes = list(read_jsonl_dir("/data/curated/nodes"))
    rels = list(read_jsonl_dir("/data/curated/relations"))
    ids = {n["id"] for n in nodes}
    assert len(nodes) == 31 and len(rels) == 44
    assert all(r["subject_id"] in ids and r["object_id"] in ids for r in rels)
    assert all(r["sources"] and r["evidence"] for r in rels)  # provenance on every edge
    fassi = next(n for n in nodes if n["id"] == "person:amina-el-fassi")
    assert "A. El Fassi" in fassi["aliases"]
