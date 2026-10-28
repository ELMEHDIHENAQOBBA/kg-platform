"""Entity resolution: normalisation + fuzzy matching + person/acronym rules -> canonical ids."""
import re
import unicodedata
from collections import Counter, defaultdict

from rapidfuzz import fuzz

from .schema import SYMMETRIC

HONORIFICS = {"dr", "prof", "mr", "mrs", "ms", "mme", "mlle"}
SUFFIXES = {"inc", "sas", "sarl", "sa", "ltd", "llc", "gmbh", "corp", "corporation", "co", "plc", "bv",
            "ag", "partners", "group", "limited"}
ACRONYM_SKIP = {"of", "de", "for", "the", "and", "du", "des", "la", "le"}
FUZZY_THRESHOLD = 93


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tokens(s):
    return re.sub(r"[^a-z0-9]+", " ", strip_accents(s).lower()).split()


def normalize(name):
    t = tokens(name)
    while len(t) > 1 and t[0] in HONORIFICS:
        t.pop(0)
    while len(t) > 1 and t[-1] in SUFFIXES:
        t.pop()
    return " ".join(t)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", strip_accents(s).lower()).strip("-")


def make_id(etype, name):
    return f"{etype.lower()}:{slug(name)}"


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _person_compatible(a, b):
    """a, b: token lists. Same last name and compatible first token (full name or initial)."""
    if a[-1] != b[-1]:
        return False
    if len(a) == 1 or len(b) == 1:
        return True
    fa, fb = a[0], b[0]
    return fa == fb or (len(fa) == 1 and fb.startswith(fa)) or (len(fb) == 1 and fa.startswith(fb))


def _initials(norm):
    return "".join(t[0] for t in norm.split() if t not in ACRONYM_SKIP)


def build_mapping(mentions, enabled=True):
    """mentions: Counter {(name, type): count}. Returns {(name, type): (canonical_name, type, id)}."""
    if not enabled:
        return {(n, t): (n, t, make_id(t, n)) for (n, t) in mentions}

    # 1) type vote per normalised name (an LLM may label "Neo4j" as Technology once and Product once)
    votes = defaultdict(Counter)
    for (n, t), c in mentions.items():
        votes[normalize(n)][t] += c
    chosen_type = {k: v.most_common(1)[0][0] for k, v in votes.items()}

    # 2) cluster normalised names within each type
    uf = _UF()
    by_type = defaultdict(set)
    for k, t in chosen_type.items():
        by_type[t].add(k)
        uf.find((k, t))
    raw_upper = {normalize(n) for (n, _t) in mentions if n.isupper() and len(n) <= 6}

    for t, norms in by_type.items():
        norms = sorted(norms)
        for i, a in enumerate(norms):          # fuzzy matching (O(n^2): use blocking for big corpora)
            for b in norms[i + 1:]:
                if fuzz.ratio(a, b) >= FUZZY_THRESHOLD:
                    uf.union((a, t), (b, t))
        if t == "Person":
            toks = {n: n.split() for n in norms}
            full = [n for n in norms if len(toks[n]) >= 2 and len(toks[n][0]) > 1]
            for n in norms:
                if n in full:
                    continue  # partial = single token or initial-first
                roots = {uf.find((f, t)) for f in full if _person_compatible(toks[n], toks[f])}
                if len(roots) == 1:  # only merge when unambiguous
                    uf.union((n, t), roots.pop())
        else:
            for n in norms:
                if " " not in n and n in raw_upper:  # acronym such as RIT -> Rabat Institute of Technology
                    roots = {uf.find((o, t)) for o in norms if " " in o and _initials(o) == n}
                    if len(roots) == 1:
                        uf.union((n, t), roots.pop())

    # 3) canonical name per cluster
    members = defaultdict(Counter)
    for (n, t), c in mentions.items():
        k = normalize(n)
        members[uf.find((k, chosen_type[k]))][n] += c
    canon = {}
    for root, cnt in members.items():
        best = max(cnt.items(), key=lambda kv: (kv[1], "." not in kv[0], -len(kv[0])))[0]
        canon[root] = best

    out = {}
    for (n, t) in mentions:
        k = normalize(n)
        t2 = chosen_type[k]
        c = canon[uf.find((k, t2))]
        out[(n, t)] = (c, t2, make_id(t2, c))
    return out


def resolve_docs(docs, enabled=True):
    """docs: extracted documents. Returns resolved documents with canonical ids on entities/relations."""
    mentions = Counter()
    for d in docs:
        for e in d["entities"]:
            mentions[(e["name"], e["type"])] += 1
        for r in d["relations"]:
            mentions[(r["subject"], r["subject_type"])] += 1
            mentions[(r["object"], r["object_type"])] += 1
    mapping = build_mapping(mentions, enabled)

    resolved = []
    for d in docs:
        ents, rels = {}, []

        def add(name, etype):
            cname, ctype, cid = mapping[(name, etype)]
            e = ents.setdefault(cid, {"id": cid, "name": cname, "type": ctype, "aliases": []})
            if name != cname and name not in e["aliases"]:
                e["aliases"].append(name)
            return cid, cname, ctype

        for e in d["entities"]:
            add(e["name"], e["type"])
        for r in d["relations"]:
            sid, sname, stype = add(r["subject"], r["subject_type"])
            oid, oname, otype = add(r["object"], r["object_type"])
            if r["predicate"] in SYMMETRIC and sid > oid:  # canonical direction for symmetric predicates
                sid, sname, stype, oid, oname, otype = oid, oname, otype, sid, sname, stype
            rels.append({"subject_id": sid, "subject_name": sname, "subject_type": stype,
                         "predicate": r["predicate"], "object_id": oid, "object_name": oname,
                         "object_type": otype, "evidence": r.get("evidence", ""),
                         "confidence": float(r.get("confidence", 0.7))})
        resolved.append({"doc_id": d["doc_id"], "title": d.get("title", ""), "source": d.get("source", ""),
                         "origin": d.get("origin", ""), "entities": list(ents.values()), "relations": rels})
    return resolved
