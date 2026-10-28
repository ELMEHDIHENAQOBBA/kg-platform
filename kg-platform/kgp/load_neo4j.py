"""Step 3b - Load curated nodes/relations from HDFS into Neo4j (idempotent MERGE)."""
from . import config
from .neo4j_utils import wait_ready, write
from .schema import ENTITY_TYPES, PREDICATES
from .storage import read_jsonl_dir

BATCH = 500


def _batches(rows):
    for i in range(0, len(rows), BATCH):
        yield rows[i:i + BATCH]


def create_schema():
    for q in (
        "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
        "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
        "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)",
        "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
        "CREATE FULLTEXT INDEX entity_names IF NOT EXISTS FOR (e:Entity) ON EACH [e.name, e.search]",
    ):
        write(q)


def load(reset=False):
    wait_ready()
    nodes = list(read_jsonl_dir(f"{config.PATH_CURATED}/nodes"))
    rels = list(read_jsonl_dir(f"{config.PATH_CURATED}/relations"))
    docs = list(read_jsonl_dir(f"{config.PATH_CURATED}/documents"))
    if not nodes:
        raise SystemExit("No curated data found: run the ETL step first.")
    if reset:
        write("MATCH (n) DETACH DELETE n")
    create_schema()

    for d in _batches(docs):
        write("UNWIND $rows AS row MERGE (d:Document {id: row.doc_id}) "
              "SET d.title = row.title, d.source = row.source, d.origin = row.origin", rows=d)

    for etype in ENTITY_TYPES:  # label is whitelisted -> safe to inline
        rows = [{**n, "search": " ".join([n["name"], *n.get("aliases", [])])} for n in nodes if n["type"] == etype]
        for b in _batches(rows):
            write(f"UNWIND $rows AS row MERGE (e:Entity {{id: row.id}}) SET e:{etype}, e.name = row.name, "
                  "e.type = row.type, e.aliases = row.aliases, e.search = row.search, e.mentions = row.mentions", rows=b)

    mentions = [{"id": n["id"], "doc": d} for n in nodes for d in n.get("docs", [])]
    for b in _batches(mentions):
        write("UNWIND $rows AS row MATCH (e:Entity {id: row.id}) MATCH (d:Document {id: row.doc}) "
              "MERGE (e)-[:MENTIONED_IN]->(d)", rows=b)

    for pred in PREDICATES:
        rows = [r for r in rels if r["predicate"] == pred]
        for b in _batches(rows):
            write(f"UNWIND $rows AS row MATCH (a:Entity {{id: row.subject_id}}) MATCH (b:Entity {{id: row.object_id}}) "
                  f"MERGE (a)-[r:{pred}]->(b) SET r.confidence = row.confidence, r.support = row.support, "
                  "r.sources = row.sources, r.evidence = row.evidence", rows=b)
    print(f"[neo4j] loaded nodes={len(nodes)} relations={len(rels)} documents={len(docs)}")
