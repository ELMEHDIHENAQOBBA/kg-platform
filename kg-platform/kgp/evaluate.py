"""Step 6 - Benchmark: extraction quality (with/without entity resolution) and Cypher vs GraphRAG QA."""
import json
import statistics
import time
from collections import defaultdict

from . import config
from .extraction import load_extracted
from .resolution import resolve_docs, tokens
from .schema import REL_ALTERNATION, SYMMETRIC

GT = config.ROOT / "data/ground_truth"


def _norm(s):
    return " ".join(tokens(s))


def _key(s, p, o):
    return (min(s, o), p, max(s, o)) if p in SYMMETRIC else (s, p, o)


# ---------------- extraction & entity resolution ----------------
def eval_extraction():
    gold = {_key(t["subject"], t["predicate"], t["object"]) for t in json.loads((GT / "triples.json").read_text())}
    gold_nodes = {n for s, _p, o in gold for n in (s, o)}
    docs = load_extracted()
    rows = []
    for enabled in (True, False):
        res = resolve_docs(docs, enabled)
        pred = {_key(r["subject_name"], r["predicate"], r["object_name"]) for d in res for r in d["relations"]}
        nodes = {e["id"] for d in res for e in d["entities"]}
        tp = len(pred & gold)
        p, r = tp / max(len(pred), 1), tp / max(len(gold), 1)
        rows.append({"entity_resolution": enabled, "precision": round(p, 3), "recall": round(r, 3),
                     "f1": round(2 * p * r / max(p + r, 1e-9), 3), "nodes": len(nodes),
                     "gold_nodes": len(gold_nodes), "duplicate_nodes": max(0, len(nodes) - len(gold_nodes)),
                     "triples": len(pred), "gold_triples": len(gold)})
    return rows


# ---------------- question answering ----------------
def _prf(pred, expected, recall_only):
    exp = {_norm(x) for x in expected}
    got = {_norm(x) for x in pred}
    tp = len(exp & got)
    rec = tp / len(exp) if exp else 0.0
    if recall_only or not got:
        return None, rec, None
    prec = len(got & exp) / len(got)
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def eval_qa(graphrag=None):
    from .graphrag import GraphRAG
    from .neo4j_utils import read
    graphrag = graphrag or GraphRAG()
    questions = json.loads((GT / "questions.json").read_text())
    results = []
    for q in questions:
        rec = {"id": q["id"], "type": q["type"], "question": q["question"], "expected": q["expected"]}
        ro = q.get("recall_only", False)
        # --- Cypher baseline (hand-written query) ---
        if q.get("cypher"):
            t0 = time.time()
            try:
                rows = read(q["cypher"].replace("{REL}", REL_ALTERNATION))
                got = [str(r["answer"]) for r in rows]
                p, r_, f = _prf(got, q["expected"], ro)
                rec["cypher"] = {"answer": got, "precision": p, "recall": r_, "f1": f,
                                 "latency_s": round(time.time() - t0, 4), "explainable": True, "cost_usd": 0.0}
            except Exception as e:  # noqa: BLE001
                rec["cypher"] = {"error": str(e)}
        else:
            rec["cypher"] = None  # question cannot be expressed as a fixed Cypher query
        # --- GraphRAG ---
        out = graphrag.ask(q["question"])
        text = " ".join([out["answer"], *out["entities"]])
        found = [e for e in q["expected"] if _norm(e) in _norm(text)]
        p, r_, f = _prf(out["entities"], q["expected"], ro)
        r_ = len(found) / len(q["expected"])  # recall measured on the answer text + entity list
        if p is not None:
            f = 2 * p * r_ / (p + r_) if p + r_ else 0.0
        rec["graphrag"] = {"answer": out["answer"], "entities": out["entities"], "precision": p, "recall": r_, "f1": f,
                           "latency_s": out["latency_s"], "explainable": bool(out["provenance"]),
                           "cost_usd": out["usage"]["cost_usd"], "in_tokens": out["usage"]["in_tokens"],
                           "out_tokens": out["usage"]["out_tokens"], "n_facts": out["n_facts"]}
        results.append(rec)
        print(f" {q['id']} [{q['type']}] cypher={_fmt(rec['cypher'])} graphrag={_fmt(rec['graphrag'])}")
    return results


def _fmt(x):
    if not x:
        return "n/a"
    if "error" in x:
        return "ERR"
    return f"R={x['recall']:.2f}" + (f" P={x['precision']:.2f}" if x.get("precision") is not None else "")


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.mean(vals), 3) if vals else None


def summarize(results):
    agg = {}
    for method in ("cypher", "graphrag"):
        rs = [r[method] for r in results if r.get(method) and "error" not in r[method]]
        agg[method] = {"questions": len(rs), "recall": _avg(x["recall"] for x in rs),
                       "precision": _avg(x["precision"] for x in rs), "f1": _avg(x["f1"] for x in rs),
                       "latency_s": _avg(x["latency_s"] for x in rs),
                       "explainable_rate": _avg(1.0 if x["explainable"] else 0.0 for x in rs),
                       "cost_usd_total": round(sum(x["cost_usd"] for x in rs), 5)}
    by_type = defaultdict(dict)
    for t in sorted({r["type"] for r in results}):
        for method in ("cypher", "graphrag"):
            rs = [r[method] for r in results if r["type"] == t and r.get(method) and "error" not in r[method]]
            by_type[t][method] = _avg(x["recall"] for x in rs) if rs else None
    return {"overall": agg, "recall_by_question_type": by_type}


def to_markdown(extraction, results, summary, provider):
    L = ["# Benchmark report", "", f"LLM provider: `{provider}`" +
         ("  \n> **mock** = rule-based extraction + retrieval-only answers: smoke test, NOT a representative GraphRAG result." if provider == "mock" else ""),
         "", "## 1. Extraction and entity resolution", "",
         "| Entity resolution | Precision | Recall | F1 | Nodes | Gold nodes | Duplicate nodes |", "|---|---|---|---|---|---|---|"]
    for r in extraction:
        L.append(f"| {'on' if r['entity_resolution'] else 'off'} | {r['precision']} | {r['recall']} | {r['f1']} | "
                 f"{r['nodes']} | {r['gold_nodes']} | {r['duplicate_nodes']} |")
    L += ["", "## 2. Cypher vs GraphRAG", "", "| Metric | Cypher | GraphRAG |", "|---|---|---|"]
    o = summary["overall"]
    for k in ("questions", "recall", "precision", "f1", "latency_s", "explainable_rate", "cost_usd_total"):
        L.append(f"| {k} | {o['cypher'][k]} | {o['graphrag'][k]} |")
    L += ["", "### Recall by question type", "", "| Type | Cypher | GraphRAG |", "|---|---|---|"]
    for t, d in summary["recall_by_question_type"].items():
        L.append(f"| {t} | {d.get('cypher')} | {d.get('graphrag')} |")
    L += ["", "### Per-question details", "", "| ID | Type | Question | Cypher | GraphRAG |", "|---|---|---|---|---|"]
    for r in results:
        L.append(f"| {r['id']} | {r['type']} | {r['question']} | {_fmt(r['cypher'])} | {_fmt(r['graphrag'])} |")
    return "\n".join(L) + "\n"


def run(skip_qa=False):
    from .llm import LLM
    extraction = eval_extraction()
    print("Extraction:", json.dumps(extraction, indent=1))
    results, summary = [], {}
    if not skip_qa:
        results = eval_qa()
        summary = summarize(results)
        print(json.dumps(summary, indent=1))
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    provider = LLM().provider
    (config.REPORTS_DIR / "benchmark.json").write_text(json.dumps(
        {"provider": provider, "extraction": extraction, "summary": summary, "questions": results},
        indent=1, ensure_ascii=False))
    if not skip_qa:
        (config.REPORTS_DIR / "benchmark.md").write_text(to_markdown(extraction, results, summary, provider))
    print(f"Report written to {config.REPORTS_DIR}/benchmark.(md|json)")
