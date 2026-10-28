"""Pure-Python twin of spark/spark_etl.py (same transformations) for machines without Spark."""
from collections import defaultdict

from . import config
from .schema import ENTITY_TYPES, PREDICATES
from .storage import get_storage, read_jsonl_dir, write_jsonl


def run(min_confidence=None):
    min_conf = config.MIN_CONFIDENCE if min_confidence is None else min_confidence
    docs, nodes, rels, rejected = {}, {}, {}, []
    for d in read_jsonl_dir(config.PATH_RESOLVED):
        docs[d["doc_id"]] = {k: d.get(k) for k in ("doc_id", "title", "source", "origin")}
        for e in d["entities"]:
            if e.get("id") and e.get("type") in ENTITY_TYPES:
                n = nodes.setdefault(e["id"], {"id": e["id"], "name": e["name"], "type": e["type"],
                                               "aliases": [], "docs": set(), "mentions": 0})
                n["aliases"] = sorted(set(n["aliases"]) | set(e.get("aliases", [])))
                n["docs"].add(d["doc_id"])
                n["mentions"] += 1
        for r in d["relations"]:
            ok = (r.get("subject_id") and r.get("object_id") and r["subject_id"] != r["object_id"]
                  and r.get("predicate") in PREDICATES and r.get("subject_type") in ENTITY_TYPES
                  and r.get("object_type") in ENTITY_TYPES and (r.get("confidence") or 0) >= min_conf)
            if not ok:
                rejected.append({"doc_id": d["doc_id"], **r})
                continue
            k = (r["subject_id"], r["predicate"], r["object_id"])
            a = rels.setdefault(k, {"subject_id": k[0], "predicate": k[1], "object_id": k[2],
                                    "subject_name": r["subject_name"], "object_name": r["object_name"],
                                    "max_conf": 0.0, "sources": set(), "evidence": []})
            a["max_conf"] = max(a["max_conf"], r["confidence"])
            a["sources"].add(d["doc_id"])
            if r["evidence"] and r["evidence"] not in a["evidence"]:
                a["evidence"].append(r["evidence"])
    out_rels = []
    for a in rels.values():
        if a["subject_id"] in nodes and a["object_id"] in nodes:
            sup = len(a["sources"])
            out_rels.append({**{k: a[k] for k in ("subject_id", "predicate", "object_id", "subject_name", "object_name")},
                             "support": sup, "sources": sorted(a["sources"]), "evidence": a["evidence"][:3],
                             "confidence": min(1.0, round(a["max_conf"] + 0.05 * (sup - 1), 3))})
    out_nodes = [{**n, "docs": sorted(n["docs"])} for n in nodes.values()]
    st = get_storage()
    for name, rows in (("nodes", out_nodes), ("relations", out_rels), ("documents", list(docs.values())),
                       ("rejected", rejected)):
        st.delete(f"{config.PATH_CURATED}/{name}")
        write_jsonl(f"{config.PATH_CURATED}/{name}/part-00000.json", rows)
    print(f"[python-etl] documents={len(docs)} nodes={len(out_nodes)} relations={len(out_rels)} rejected={len(rejected)}")
