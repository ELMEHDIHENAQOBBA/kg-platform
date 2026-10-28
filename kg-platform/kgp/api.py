"""Step 7 - REST API (FastAPI): GraphRAG + analytics, API-key access control, audit log, Prometheus metrics."""
import logging
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from . import analytics, config
from .graphrag import GraphRAG
from .neo4j_utils import read

config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
audit = logging.getLogger("kgp.audit")
audit.setLevel(logging.INFO)
audit.addHandler(logging.FileHandler(config.REPORTS_DIR / "audit.log"))

REQ = Counter("kgp_requests_total", "HTTP requests", ["path", "status"])
LAT = Histogram("kgp_request_seconds", "Request latency", ["path"])

app = FastAPI(title="Knowledge Graph Analytics Platform", version="1.0")
_rag = None


def rag():
    global _rag
    if _rag is None:
        _rag = GraphRAG()
    return _rag


def auth(x_api_key: str = Header(default=None)):
    """Role-based access: reader (queries) / admin (ad-hoc Cypher). Empty API_KEYS disables auth (dev)."""
    if not config.API_KEYS:
        return "admin"
    role = config.API_KEYS.get(x_api_key)
    if not role:
        raise HTTPException(401, "Missing or invalid X-API-Key")
    return role


def admin(role: str = Depends(auth)):
    if role != "admin":
        raise HTTPException(403, "Admin role required")
    return role


@app.middleware("http")
async def metrics_and_audit(request: Request, call_next):
    t0 = time.time()
    resp = await call_next(request)
    dt = time.time() - t0
    path = request.url.path
    REQ.labels(path, resp.status_code).inc()
    LAT.labels(path).observe(dt)
    audit.info("%s role=%s %s %s %d %.3fs", time.strftime("%Y-%m-%dT%H:%M:%S"),
               config.API_KEYS.get(request.headers.get("x-api-key"), "anon"), request.method, path,
               resp.status_code, dt)
    return resp


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    hops: int = Field(2, ge=1, le=3)


class CypherRequest(BaseModel):
    query: str
    params: dict = {}


@app.get("/health")
def health():
    try:
        read("RETURN 1 AS ok")
        return {"status": "ok", "neo4j": "up", "llm_provider": rag().llm.provider}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"neo4j down: {e}")


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/stats")
def stats(_=Depends(auth)):
    return analytics.stats()


@app.post("/ask")
def ask(req: AskRequest, _=Depends(auth)):
    return rag().ask(req.question, hops=req.hops)


@app.get("/analytics/{algorithm}")
def run_algorithm(algorithm: str, limit: int = 10, _=Depends(auth)):
    fn = analytics.ALGORITHMS.get(algorithm)
    if not fn:
        raise HTTPException(404, f"unknown algorithm; choose from {list(analytics.ALGORITHMS)}")
    res = fn(max(1, min(limit, 100)))
    return res if isinstance(res, dict) else {"algorithm": algorithm, "engine": "cypher", "rows": res}


@app.get("/path")
def path(source: str, target: str, max_hops: int = 6, _=Depends(auth)):
    res = analytics.shortest_path(source, target, max_hops)
    if not res:
        raise HTTPException(404, "no path found")
    return res


@app.get("/entity")
def entity(name: str, _=Depends(auth)):
    return {"matches": analytics.find_entity(name), "relations": analytics.neighbors(name)}


@app.get("/subgraph")
def subgraph(name: str, hops: int = 1, _=Depends(auth)):
    return {"edges": analytics.subgraph(name, hops)}


@app.post("/cypher")
def cypher(req: CypherRequest, _=Depends(admin)):
    try:
        return {"rows": analytics.run_cypher_readonly(req.query, req.params)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Cypher error: {e}")
