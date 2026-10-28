"""Thin Neo4j helper."""
import time

from neo4j import GraphDatabase

from . import config

_driver = None


def driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))
    return _driver


def read(query, **params):
    with driver().session() as s:
        return s.execute_read(lambda tx: [r.data() for r in tx.run(query, **params)])


def write(query, **params):
    with driver().session() as s:
        return s.execute_write(lambda tx: [r.data() for r in tx.run(query, **params)])


def wait_ready(timeout=180):
    t0 = time.time()
    while True:
        try:
            driver().verify_connectivity()
            return True
        except Exception as e:  # noqa: BLE001
            if time.time() - t0 > timeout:
                raise RuntimeError(f"Neo4j not reachable at {config.NEO4J_URI}: {e}")
            time.sleep(3)
