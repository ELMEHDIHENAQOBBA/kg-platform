"""Shared graph schema (pure Python, no dependency: also imported by the Spark job)."""

ENTITY_TYPES = ["Person", "Organization", "Location", "Product", "Technology"]

# predicate -> (expected subject type, expected object type)
PREDICATES = {
    "FOUNDED": ("Person", "Organization"),
    "WORKS_FOR": ("Person", "Organization"),
    "STUDIED_AT": ("Person", "Organization"),
    "COLLABORATES_WITH": ("Person", "Person"),
    "ACQUIRED": ("Organization", "Organization"),
    "PARTNERS_WITH": ("Organization", "Organization"),
    "COMPETES_WITH": ("Organization", "Organization"),
    "INVESTED_IN": ("Organization", "Organization"),
    "LOCATED_IN": ("Organization", "Location"),
    "DEVELOPS": ("Organization", "Product"),
    "USES": ("Organization", "Technology"),
}
SYMMETRIC = {"PARTNERS_WITH", "COMPETES_WITH", "COLLABORATES_WITH"}
REL_ALTERNATION = "|".join(PREDICATES)  # for Cypher: [:A|B|C]
