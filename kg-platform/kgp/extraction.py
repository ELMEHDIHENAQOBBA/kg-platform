"""Step 2 - LLM-based information extraction (entities + relations) from raw documents."""
import json
import time

from . import config
from .llm import LLM, LLMError, cost_usd, parse_json
from .parsing import chunk_text, parse_bytes
from .rule_extractor import extract as rule_extract
from .schema import ENTITY_TYPES, PREDICATES
from .storage import get_storage, read_json, write_json

SYSTEM_PROMPT = """You are an information extraction engine that builds a knowledge graph.
Extract ONLY facts explicitly stated in the text. Never infer or guess.

Entity types: %(types)s
Relation types (SUBJECT_TYPE -> OBJECT_TYPE):
%(rels)s

Rules:
- Use the full, written name of each entity (no pronouns like "he", "the company").
- "evidence" must be the sentence of the text that supports the relation.
- "confidence" is a number between 0 and 1.
- If nothing can be extracted, return empty lists.
- Answer with a single JSON object, no commentary:
{"entities":[{"name":"...","type":"..."}],
 "relations":[{"subject":"...","subject_type":"...","predicate":"...","object":"...","object_type":"...","evidence":"...","confidence":0.9}]}

Example
Text: "Maria Silva founded Orbit Labs in Lisbon. Orbit Labs uses Kafka."
Answer: {"entities":[{"name":"Maria Silva","type":"Person"},{"name":"Orbit Labs","type":"Organization"},{"name":"Lisbon","type":"Location"},{"name":"Kafka","type":"Technology"}],"relations":[{"subject":"Maria Silva","subject_type":"Person","predicate":"FOUNDED","object":"Orbit Labs","object_type":"Organization","evidence":"Maria Silva founded Orbit Labs in Lisbon.","confidence":0.95},{"subject":"Orbit Labs","subject_type":"Organization","predicate":"LOCATED_IN","object":"Lisbon","object_type":"Location","evidence":"Maria Silva founded Orbit Labs in Lisbon.","confidence":0.8},{"subject":"Orbit Labs","subject_type":"Organization","predicate":"USES","object":"Kafka","object_type":"Technology","evidence":"Orbit Labs uses Kafka.","confidence":0.9}]}
""" % {
    "types": ", ".join(ENTITY_TYPES),
    "rels": "\n".join(f"- {p}: {s} -> {o}" for p, (s, o) in PREDICATES.items()),
}


def normalize_output(obj):
    """Validate/clean the raw LLM JSON against the schema."""
    ents, rels = {}, []
    for e in obj.get("entities", []) or []:
        name, typ = str(e.get("name", "")).strip(), e.get("type")
        if name and typ in ENTITY_TYPES:
            ents[(name, typ)] = 1
    for r in obj.get("relations", []) or []:
        pred = str(r.get("predicate", "")).strip().upper().replace(" ", "_")
        s, o = str(r.get("subject", "")).strip(), str(r.get("object", "")).strip()
        if pred not in PREDICATES or not s or not o or s == o:
            continue
        dom, rng = PREDICATES[pred]
        st = r.get("subject_type") if r.get("subject_type") in ENTITY_TYPES else dom
        ot = r.get("object_type") if r.get("object_type") in ENTITY_TYPES else rng
        try:
            conf = max(0.0, min(1.0, float(r.get("confidence", 0.7))))
        except (TypeError, ValueError):
            conf = 0.7
        rels.append({"subject": s, "subject_type": st, "predicate": pred, "object": o, "object_type": ot,
                     "evidence": str(r.get("evidence", ""))[:400], "confidence": conf})
        ents[(s, st)] = 1
        ents[(o, ot)] = 1
    return {"entities": [{"name": n, "type": t} for n, t in ents], "relations": rels}


def extract_text(text, llm):
    """Returns (normalized_result, usage)."""
    usage = {"in_tokens": 0, "out_tokens": 0, "seconds": 0.0, "errors": 0}
    if llm.is_mock:
        t0 = time.time()
        res = rule_extract(text)
        usage["seconds"] = time.time() - t0
        return res, usage
    res = llm.complete(SYSTEM_PROMPT, f"Text:\n\"\"\"\n{text}\n\"\"\"\nAnswer:", json_mode=True, max_tokens=2500)
    usage.update(in_tokens=res.in_tokens, out_tokens=res.out_tokens, seconds=res.seconds)
    return normalize_output(parse_json(res.text)), usage


def extract_document(meta, raw, llm):
    title, text = parse_bytes(f"x.{meta['ext']}", raw)
    entities, relations = {}, []
    usage = {"in_tokens": 0, "out_tokens": 0, "seconds": 0.0, "errors": 0}
    for ch in chunk_text(text, config.CHUNK_CHARS):
        try:
            res, u = extract_text(ch, llm)
        except LLMError as e:
            usage["errors"] += 1
            print(f"   ! chunk skipped ({e})")
            continue
        for k in ("in_tokens", "out_tokens", "seconds", "errors"):
            usage[k] += u[k]
        for e in res["entities"]:
            entities[(e["name"], e["type"])] = 1
        relations.extend(res["relations"])
    return {"doc_id": meta["doc_id"], "title": meta.get("title") or title, "source": meta["source_type"],
            "origin": meta["origin"], "extractor": llm.provider, "model": llm.model,
            "entities": [{"name": n, "type": t} for n, t in entities], "relations": relations,
            "usage": usage}


def run(force=False):
    st, llm = get_storage(), LLM()
    st.makedirs(config.PATH_EXTRACTED)
    metas = [n for n in st.list(config.PATH_RAW) if n.endswith(".meta.json")]
    tot_in = tot_out = n_rel = 0
    for name in metas:
        meta = read_json(f"{config.PATH_RAW}/{name}")
        out = f"{config.PATH_EXTRACTED}/{meta['doc_id']}.json"
        if st.exists(out) and not force:
            print(f" - {meta['doc_id']}: already extracted (use --force to redo)")
            continue
        raw = st.read(f"{config.PATH_RAW}/{meta['doc_id']}.{meta['ext']}")
        doc = extract_document(meta, raw, llm)
        write_json(out, doc)
        u = doc["usage"]
        tot_in, tot_out, n_rel = tot_in + u["in_tokens"], tot_out + u["out_tokens"], n_rel + len(doc["relations"])
        print(f" - {meta['doc_id']}: {len(doc['entities'])} entities, {len(doc['relations'])} relations "
              f"[{llm.provider}:{llm.model}] {u['seconds']:.1f}s")
    print(f"Extraction done. relations={n_rel} tokens_in={tot_in} tokens_out={tot_out} "
          f"est_cost=${cost_usd(tot_in, tot_out):.4f}")


def load_extracted():
    st = get_storage()
    return [read_json(f"{config.PATH_EXTRACTED}/{n}") for n in st.list(config.PATH_EXTRACTED) if n.endswith(".json")]
