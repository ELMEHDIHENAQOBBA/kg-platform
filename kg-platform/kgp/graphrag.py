"""Step 5 - GraphRAG: entity linking -> k-hop subgraph retrieval -> context -> LLM answer with provenance."""
import time

from . import config
from .llm import LLM, LLMError, cost_usd, parse_json
from .neo4j_utils import read
from .resolution import normalize, tokens
from .schema import REL_ALTERNATION

ANSWER_SYSTEM = """You answer questions using ONLY the knowledge-graph facts provided.
Each fact looks like: [n] Subject --PREDICATE--> Object (confidence; sources; evidence).
Rules:
- Reason across several facts when needed (multi-hop). Do not use outside knowledge.
- The facts were extracted automatically and may be wrong or incomplete: if the facts do not contain the
  answer, say so. Never present an inferred relation between people as a verified fact.
- Answer in the same language as the question. Be concise.
- Cite the supporting facts by their number, e.g. [3].
Return a single JSON object:
{"answer": "<text with [n] citations>", "entities": ["<every entity that is part of the final answer>"], "facts_used": [<fact numbers>]}"""


class GraphRAG:
    def __init__(self, llm=None):
        self.llm = llm or LLM()
        self._index, self._index_t = None, 0

    def _entities(self):
        if self._index is None or time.time() - self._index_t > 300:
            self._index = read("MATCH (e:Entity) RETURN e.id AS id, e.name AS name, e.type AS type, e.aliases AS aliases")
            self._index_t = time.time()
        return self._index

    # 1. entity linking ---------------------------------------------------------------
    def link(self, question):
        q = " " + " ".join(tokens(question)) + " "
        hits = []
        for e in self._entities():
            for surface in [e["name"], *(e.get("aliases") or [])]:
                n = normalize(surface)
                if n and f" {n} " in q:
                    hits.append((len(n), e))
                    break
        hits.sort(key=lambda x: -x[0])
        seeds, covered = [], ""
        for _, e in hits:  # drop entities whose name is contained in a longer matched one
            n = normalize(e["name"])
            if n not in covered:
                seeds.append(e)
                covered += " " + n
        return seeds

    # 2. retrieval ---------------------------------------------------------------------
    def retrieve(self, seeds, hops=2, max_facts=150):
        if seeds:
            hops = max(1, min(int(hops), 3))
            q = (f"MATCH (s:Entity) WHERE s.id IN $ids MATCH p = (s)-[:{REL_ALTERNATION}*1..{hops}]-(:Entity) "
                 "UNWIND relationships(p) AS r WITH DISTINCT r "
                 "RETURN startNode(r).name AS subject, type(r) AS predicate, endNode(r).name AS object, "
                 "r.confidence AS confidence, r.sources AS sources, r.evidence AS evidence "
                 "ORDER BY confidence DESC LIMIT $limit")
            return read(q, ids=[s["id"] for s in seeds], limit=max_facts)
        # no entity detected (global question): fall back to the most confident facts of the whole graph
        q = (f"MATCH (a:Entity)-[r:{REL_ALTERNATION}]->(b:Entity) RETURN a.name AS subject, type(r) AS predicate, "
             "b.name AS object, r.confidence AS confidence, r.sources AS sources, r.evidence AS evidence "
             "ORDER BY confidence DESC LIMIT $limit")
        return read(q, limit=max_facts)

    # 3. context -----------------------------------------------------------------------
    @staticmethod
    def build_context(facts):
        lines = []
        for i, f in enumerate(facts, 1):
            ev = (f.get("evidence") or [""])[0][:200]
            lines.append(f"[{i}] {f['subject']} --{f['predicate']}--> {f['object']} "
                         f"(confidence {f.get('confidence') or 0:.2f}; sources: {', '.join(f.get('sources') or [])}; "
                         f"evidence: \"{ev}\")")
        return "\n".join(lines)

    # 4. generation --------------------------------------------------------------------
    def ask(self, question, hops=2, max_facts=150):
        t0 = time.time()
        seeds = self.link(question)
        facts = self.retrieve(seeds, hops, max_facts)
        context = self.build_context(facts)
        out = {"question": question, "seeds": [s["name"] for s in seeds], "n_facts": len(facts),
               "provider": self.llm.provider, "model": self.llm.model,
               "usage": {"in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0}}
        if self.llm.is_mock:  # offline smoke test: no reasoning, just echoes the retrieved subgraph
            names = sorted({n for f in facts for n in (f["subject"], f["object"])} - {s["name"] for s in seeds})
            out.update(answer="(mock LLM - retrieval only) " + "; ".join(
                f"{f['subject']} {f['predicate']} {f['object']}" for f in facts[:8]),
                entities=names, facts_used=list(range(1, len(facts) + 1)))
        else:
            try:
                res = self.llm.complete(ANSWER_SYSTEM, f"Question: {question}\n\nGraph facts:\n{context}\n\nJSON answer:",
                                        json_mode=True, max_tokens=1200)
                out["usage"] = {"in_tokens": res.in_tokens, "out_tokens": res.out_tokens,
                                "cost_usd": cost_usd(res.in_tokens, res.out_tokens)}
                try:
                    j = parse_json(res.text)
                    out.update(answer=str(j.get("answer", "")), entities=[str(x) for x in j.get("entities", [])],
                               facts_used=[int(x) for x in j.get("facts_used", []) if str(x).isdigit()])
                except LLMError:
                    out.update(answer=res.text, entities=[], facts_used=[])
            except LLMError as e:
                out.update(answer=f"LLM error: {e}", entities=[], facts_used=[])
        used = [facts[i - 1] for i in out["facts_used"] if 0 < i <= len(facts)]
        out["provenance"] = [{"fact": f"{f['subject']} --{f['predicate']}--> {f['object']}",
                              "confidence": f.get("confidence"), "sources": f.get("sources"),
                              "evidence": (f.get("evidence") or [""])[0]} for f in used[:25]]
        out["latency_s"] = round(time.time() - t0, 3)
        return out
