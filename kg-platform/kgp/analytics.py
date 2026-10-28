"""Step 4 - Graph analytics: Cypher queries + Neo4j GDS algorithms (networkx fallback)."""
import re

from .neo4j_utils import read, write
from .schema import REL_ALTERNATION


def stats():
    nodes = read("MATCH (e:Entity) RETURN e.type AS type, count(*) AS n ORDER BY n DESC")
    rels = read(f"MATCH (:Entity)-[r:{REL_ALTERNATION}]->(:Entity) RETURN type(r) AS type, count(*) AS n ORDER BY n DESC")
    docs = read("MATCH (d:Document) RETURN count(d) AS n")[0]["n"]
    return {"nodes_by_type": nodes, "relations_by_type": rels, "documents": docs,
            "total_nodes": sum(x["n"] for x in nodes), "total_relations": sum(x["n"] for x in rels)}


def top_degree(limit=10):
    return read(f"MATCH (e:Entity) OPTIONAL MATCH (e)-[r:{REL_ALTERNATION}]-(:Entity) "
                "RETURN e.name AS name, e.type AS type, count(r) AS degree ORDER BY degree DESC, name LIMIT $limit", limit=limit)


def _edges():
    return read(f"MATCH (a:Entity)-[r:{REL_ALTERNATION}]->(b:Entity) "
                "RETURN a.id AS s, a.name AS sn, a.type AS st, b.id AS t, b.name AS tn, b.type AS tt")


def _nx_graph():
    import networkx as nx
    g = nx.Graph()
    for e in _edges():
        g.add_node(e["s"], name=e["sn"], type=e["st"])
        g.add_node(e["t"], name=e["tn"], type=e["tt"])
        g.add_edge(e["s"], e["t"])
    return g


def _project(name, undirected):
    write("CALL gds.graph.drop($n, false)", n=name)
    cfg = "{undirectedRelationshipTypes: ['*']}" if undirected else "{}"
    write(f"MATCH (s:Entity)-[r]->(t:Entity) WITH gds.graph.project($n, s, t, {{}}, {cfg}) AS g RETURN g.graphName AS g", n=name)


def pagerank(limit=10):
    try:
        _project("kgp_dir", False)
        rows = read("CALL gds.pageRank.stream('kgp_dir') YIELD nodeId, score "
                    "RETURN gds.util.asNode(nodeId).name AS name, gds.util.asNode(nodeId).type AS type, "
                    "round(score, 4) AS score ORDER BY score DESC LIMIT $limit", limit=limit)
        return {"algorithm": "pageRank", "engine": "neo4j-gds", "rows": rows}
    except Exception:  # GDS plugin missing -> networkx
        import networkx as nx
        g = _nx_graph()
        d = nx.DiGraph([(e["s"], e["t"]) for e in _edges()])
        pr = nx.pagerank(d)
        rows = sorted(({"name": g.nodes[n]["name"], "type": g.nodes[n]["type"], "score": round(s, 4)}
                       for n, s in pr.items()), key=lambda r: -r["score"])[:limit]
        return {"algorithm": "pageRank", "engine": "networkx", "rows": rows}


def communities(limit=10):
    try:
        _project("kgp_undir", True)
        rows = read("CALL gds.louvain.stream('kgp_undir') YIELD nodeId, communityId "
                    "WITH communityId, collect(gds.util.asNode(nodeId).name) AS members "
                    "RETURN communityId AS community, size(members) AS size, members ORDER BY size DESC LIMIT $limit", limit=limit)
        return {"algorithm": "louvain", "engine": "neo4j-gds", "rows": rows}
    except Exception:
        import networkx as nx
        g = _nx_graph()
        comms = nx.community.louvain_communities(g, seed=42)
        rows = sorted(({"community": i, "size": len(c), "members": sorted(g.nodes[n]["name"] for n in c)}
                       for i, c in enumerate(comms)), key=lambda r: -r["size"])[:limit]
        return {"algorithm": "louvain", "engine": "networkx", "rows": rows}


def betweenness(limit=10):
    try:
        _project("kgp_undir", True)
        rows = read("CALL gds.betweenness.stream('kgp_undir') YIELD nodeId, score "
                    "RETURN gds.util.asNode(nodeId).name AS name, gds.util.asNode(nodeId).type AS type, "
                    "round(score, 3) AS score ORDER BY score DESC LIMIT $limit", limit=limit)
        return {"algorithm": "betweenness", "engine": "neo4j-gds", "rows": rows}
    except Exception:
        import networkx as nx
        g = _nx_graph()
        bc = nx.betweenness_centrality(g)
        rows = sorted(({"name": g.nodes[n]["name"], "type": g.nodes[n]["type"], "score": round(s, 3)}
                       for n, s in bc.items()), key=lambda r: -r["score"])[:limit]
        return {"algorithm": "betweenness", "engine": "networkx", "rows": rows}


ALGORITHMS = {"pagerank": pagerank, "communities": communities, "betweenness": betweenness, "degree": top_degree}


def find_entity(name):
    return read("CALL db.index.fulltext.queryNodes('entity_names', $q) YIELD node, score "
                "RETURN node.id AS id, node.name AS name, node.type AS type, score ORDER BY score DESC LIMIT 5",
                q=_lucene(name))


def shortest_path(source, target, max_hops=6):
    max_hops = max(1, min(int(max_hops), 8))
    rows = read(f"MATCH (a:Entity), (b:Entity) WHERE toLower(a.name) = toLower($s) AND toLower(b.name) = toLower($t) "
                f"MATCH p = shortestPath((a)-[:{REL_ALTERNATION}*..{max_hops}]-(b)) "
                "RETURN [n IN nodes(p) | n.name] AS nodes, [r IN relationships(p) | type(r)] AS relations, "
                "[r IN relationships(p) | r.sources] AS sources LIMIT 1", s=source, t=target)
    return rows[0] if rows else None


def neighbors(name):
    return read(f"MATCH (a:Entity)-[r:{REL_ALTERNATION}]-(b:Entity) WHERE toLower(a.name) = toLower($n) "
                "RETURN a.name AS entity, type(r) AS relation, startNode(r) = a AS outgoing, b.name AS other, "
                "b.type AS other_type, r.confidence AS confidence, r.sources AS sources, r.evidence AS evidence "
                "ORDER BY relation", n=name)


def subgraph(name, hops=1, limit=200):
    hops = max(1, min(int(hops), 3))
    return read(f"MATCH (s:Entity) WHERE toLower(s.name) = toLower($n) "
                f"MATCH p = (s)-[:{REL_ALTERNATION}*1..{hops}]-(:Entity) UNWIND relationships(p) AS r WITH DISTINCT r "
                "RETURN startNode(r).name AS source, startNode(r).type AS source_type, type(r) AS relation, "
                "endNode(r).name AS target, endNode(r).type AS target_type, r.confidence AS confidence LIMIT $limit",
                n=name, limit=limit)


_FORBIDDEN = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|FOREACH|CALL\s+dbms|CALL\s+apoc|CALL\s+gds\.[a-z.]*\.(write|mutate))\b", re.I)


def run_cypher_readonly(query, params=None, limit=500):
    """Admin-only ad-hoc Cypher: read transaction + keyword blocklist (defence in depth)."""
    if _FORBIDDEN.search(query):
        raise ValueError("Only read-only Cypher is allowed.")
    return read(query, **(params or {}))[:limit]


def _lucene(text):
    words = [w for w in re.findall(r"[\w]+", text) if len(w) > 2]
    return " OR ".join(f"{w}~" for w in words) or "*"
