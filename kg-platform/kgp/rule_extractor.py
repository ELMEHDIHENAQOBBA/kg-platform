"""Offline rule-based extractor (LLM_PROVIDER=mock).

Deterministic regex patterns covering the sample corpus. It lets you validate the whole
pipeline without any LLM, and serves as a cheap baseline for the LLM extractor.
"""
import re

CAP = r"(?:[A-Z]\.|[A-Z][A-Za-z0-9'’\-]*)"
NAME = rf"{CAP}(?:[ ]+(?:(?:of|de|for)[ ]+)?{CAP})*"
ROLES = (r"(?:CEO|CTO|CFO|COO|CIO|Chief\s+\w+\s+Officer|Partner|Director|Engineer|Scientist|Researcher|"
         r"Professor|Manager|Analyst|Lead|Head|Founder|President|Investor|Developer|Architect)")

LEAD_STOP = {
    "Today", "In", "On", "At", "The", "A", "An", "Since", "After", "Before", "During", "Later", "Meanwhile",
    "However", "Moreover", "Also", "Earlier", "Yesterday", "Recently", "Hi", "Dear", "Minutes", "Subject",
    "From", "To", "Note", "According", "Following", "Currently", "Now", "Then", "Still", "Furthermore",
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December", "Last", "This", "That", "Our", "Their",
}

# (regex, predicate, subject_type, object_type, swap_groups)
_P = [
    (rf"(?P<s>{NAME}),?\s+(?:is|was|became|serves as|has been)\s+(?:the|a|an)\s+(?:[A-Za-z\-]+\s+){{0,3}}?(?i:{ROLES})\s+(?:of|at)\s+(?P<o>{NAME})",
     "WORKS_FOR", "Person", "Organization"),
    (rf"(?P<s>{NAME}),\s+(?:the\s+)?(?i:{ROLES})\s+(?:of|at)\s+(?P<o>{NAME})", "WORKS_FOR", "Person", "Organization"),
    (rf"(?P<s>{NAME})\s+(?:works|worked|joined)\s+(?:for|at)?\s*(?P<o>{NAME})", "WORKS_FOR", "Person", "Organization"),
    (rf"(?P<s>{NAME})\s+(?:co-?founded|founded|established)\s+(?P<o>{NAME})", "FOUNDED", "Person", "Organization"),
    (rf"(?P<o>{NAME})\s+(?:was\s+)?(?:co-?founded|founded|established)\s+by\s+(?P<s>{NAME})", "FOUNDED", "Person", "Organization"),
    (rf"(?P<s>{NAME})\s+(?:studied|graduated|holds\s+a\s+degree)\s+(?:at|from)\s+(?P<o>{NAME})", "STUDIED_AT", "Person", "Organization"),
    (rf"(?P<s>{NAME})\s+(?:collaborates|collaborated|collaborating)\s+with\s+(?P<o>{NAME})", "COLLABORATES_WITH", "Person", "Person"),
    (rf"(?P<s>{NAME})\s+(?:has\s+|had\s+)?(?:acquired|acquires|bought)\s+(?P<o>{NAME})", "ACQUIRED", "Organization", "Organization"),
    (rf"(?P<o>{NAME})\s+(?:was|has been)\s+acquired\s+by\s+(?P<s>{NAME})", "ACQUIRED", "Organization", "Organization"),
    (rf"(?P<s>{NAME})\s+(?:has\s+)?(?:partnered|partners|signed\s+a\s+partnership)\s+with\s+(?P<o>{NAME})", "PARTNERS_WITH", "Organization", "Organization"),
    (rf"(?P<s>{NAME})\s+competes\s+with\s+(?P<o>{NAME})", "COMPETES_WITH", "Organization", "Organization"),
    (rf"(?P<s>{NAME})\s+(?:has\s+)?(?:invested\s+in|funded|backed)\s+(?P<o>{NAME})", "INVESTED_IN", "Organization", "Organization"),
    (rf"(?P<s>{NAME})\s+(?:is|are)\s+(?:headquartered|based|located)\s+in\s+(?P<o>{NAME})", "LOCATED_IN", "Organization", "Location"),
    (rf"(?P<s>{NAME})\s+(?:develops|developed|builds|built|launched|released)\s+(?P<o>{NAME})", "DEVELOPS", "Organization", "Product"),
    (rf"(?P<s>{NAME})\s+(?:uses|relies\s+on|runs\s+on)\s+(?P<o>{NAME})", "USES", "Organization", "Technology"),
]
PATTERNS = [(re.compile(p), pred, st, ot) for p, pred, st, ot in _P]


def clean_name(name):
    toks = name.split(" ")
    while toks and toks[0] in LEAD_STOP:
        toks.pop(0)
    while toks and toks[-1] in ("of", "de", "for"):
        toks.pop()
    return " ".join(toks).strip()


def split_sentences(text):
    sents = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"“])', line)
        merged = []
        for p in parts:
            if merged and re.search(r"(?:^|\s)[A-Z]\.$", merged[-1]):  # "A. El Fassi" is not a sentence end
                merged[-1] += " " + p
            else:
                merged.append(p)
        sents.extend(merged)
    return sents


def extract(text, confidence=0.85):
    relations, seen = [], set()
    for sent in split_sentences(text):
        for rx, pred, st, ot in PATTERNS:
            for m in rx.finditer(sent):
                s, o = clean_name(m.group("s")), clean_name(m.group("o"))
                if not s or not o or s == o:
                    continue
                key = (s, pred, o)
                if key in seen:
                    continue
                seen.add(key)
                relations.append({"subject": s, "subject_type": st, "predicate": pred, "object": o,
                                  "object_type": ot, "evidence": sent[:300], "confidence": confidence})
    ents = {}
    for r in relations:
        ents[(r["subject"], r["subject_type"])] = 1
        ents[(r["object"], r["object_type"])] = 1
    return {"entities": [{"name": n, "type": t} for n, t in ents], "relations": relations}
