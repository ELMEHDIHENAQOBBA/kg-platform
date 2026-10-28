"""Command line orchestrator:  python -m kgp.pipeline <command>

  wait      wait for HDFS + Neo4j and create the HDFS folders
  crawl     step 1  collect sources -> HDFS /data/raw
  extract   step 2  LLM extraction  -> HDFS /data/extracted
  resolve   step 2b entity resolution -> HDFS /data/resolved
  etl       step 3a transformations (Spark job runs in its own container; --engine python = in-process twin)
  load      step 3b curated data -> Neo4j
  analytics step 4  print graph analytics
  ask "Q"   step 5  GraphRAG question
  eval      step 6  benchmark report
  purge     data-retention policy (delete raw/extracted older than RETENTION_DAYS)
  all       crawl + extract + resolve + etl(python) + load  (+ eval with --eval)
"""
import argparse
import json
import sys
import time

from . import config


def cmd_wait(_a):
    from .neo4j_utils import wait_ready
    from .storage import get_storage
    st = get_storage()
    for i in range(60):
        try:
            for p in (config.PATH_RAW, config.PATH_EXTRACTED, config.PATH_RESOLVED, config.PATH_CURATED):
                st.makedirs(p)
            break
        except Exception as e:  # noqa: BLE001  (HDFS still starting / in safe mode)
            print(f"waiting for storage... ({type(e).__name__})")
            time.sleep(5)
    else:
        raise SystemExit("Storage not reachable")
    wait_ready()
    print("storage and Neo4j are ready")


def cmd_crawl(a):
    from . import crawler
    crawler.run(a.config)


def cmd_extract(a):
    from . import extraction
    extraction.run(force=a.force)


def cmd_resolve(a):
    from .extraction import load_extracted
    from .resolution import resolve_docs
    from .storage import get_storage, write_jsonl
    enabled = config.ENTITY_RESOLUTION and not a.no_resolution
    docs = load_extracted()
    if not docs:
        raise SystemExit("Nothing extracted yet: run `extract` first.")
    resolved = resolve_docs(docs, enabled)
    get_storage().delete(config.PATH_RESOLVED)
    write_jsonl(f"{config.PATH_RESOLVED}/resolved.jsonl", resolved)
    n = len({e["id"] for d in resolved for e in d["entities"]})
    print(f"Entity resolution {'ON' if enabled else 'OFF'}: {len(resolved)} documents, {n} distinct entities")


def cmd_etl(a):
    if a.engine == "python":
        from . import etl_python
        etl_python.run()
    else:
        from .storage import get_storage
        st = get_storage()
        print("Run the Spark job in its container:\n  docker compose --profile etl run --rm spark-etl\n"
              f"(input {st.uri(config.PATH_RESOLVED)}  ->  output {st.uri(config.PATH_CURATED)})")


def cmd_load(a):
    from . import load_neo4j
    load_neo4j.load(reset=a.reset)


def cmd_analytics(_a):
    from . import analytics
    print(json.dumps(analytics.stats(), indent=1))
    for name, fn in analytics.ALGORITHMS.items():
        res = fn(10)
        print(f"\n== {name} ==")
        print(json.dumps(res, indent=1, ensure_ascii=False))


def cmd_ask(a):
    from .graphrag import GraphRAG
    out = GraphRAG().ask(a.question, hops=a.hops)
    print(json.dumps(out, indent=1, ensure_ascii=False))


def cmd_eval(a):
    from . import evaluate
    evaluate.run(skip_qa=a.skip_qa)


def cmd_purge(a):
    from .storage import get_storage
    st, limit = get_storage(), time.time() - (a.days or config.RETENTION_DAYS) * 86400
    n = 0
    for folder in (config.PATH_RAW, config.PATH_EXTRACTED):
        for name in st.list(folder):
            if st.mtime(f"{folder}/{name}") < limit:
                st.delete(f"{folder}/{name}")
                n += 1
    print(f"Retention policy: {n} files deleted")


def cmd_all(a):
    cmd_crawl(argparse.Namespace(config="config/sources.yaml"))
    cmd_extract(argparse.Namespace(force=a.force))
    cmd_resolve(argparse.Namespace(no_resolution=False))
    cmd_etl(argparse.Namespace(engine="python"))
    cmd_load(argparse.Namespace(reset=True))
    if a.eval:
        cmd_eval(argparse.Namespace(skip_qa=False))


def main(argv=None):
    p = argparse.ArgumentParser(prog="kgp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("wait").set_defaults(fn=cmd_wait)
    s = sub.add_parser("crawl"); s.add_argument("--config", default="config/sources.yaml"); s.set_defaults(fn=cmd_crawl)
    s = sub.add_parser("extract"); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_extract)
    s = sub.add_parser("resolve"); s.add_argument("--no-resolution", action="store_true"); s.set_defaults(fn=cmd_resolve)
    s = sub.add_parser("etl"); s.add_argument("--engine", choices=["spark", "python"], default="spark"); s.set_defaults(fn=cmd_etl)
    s = sub.add_parser("load"); s.add_argument("--reset", action="store_true"); s.set_defaults(fn=cmd_load)
    sub.add_parser("analytics").set_defaults(fn=cmd_analytics)
    s = sub.add_parser("ask"); s.add_argument("question"); s.add_argument("--hops", type=int, default=2); s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("eval"); s.add_argument("--skip-qa", action="store_true"); s.set_defaults(fn=cmd_eval)
    s = sub.add_parser("purge"); s.add_argument("--days", type=int); s.set_defaults(fn=cmd_purge)
    s = sub.add_parser("all"); s.add_argument("--force", action="store_true"); s.add_argument("--eval", action="store_true"); s.set_defaults(fn=cmd_all)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
