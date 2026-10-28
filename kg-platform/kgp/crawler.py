"""Step 1 - Crawler: collect heterogeneous sources, parse light metadata, store raw files in HDFS."""
import hashlib
import sqlite3
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml

from . import config
from .parsing import parse_bytes
from .storage import get_storage, read_json, write_json
from .resolution import slug

UA = "kgp-crawler/1.0 (+research prototype)"


def _store(doc_id, ext, data, source_type, origin, extra=None):
    st = get_storage()
    sha = hashlib.sha256(data).hexdigest()
    meta_path = f"{config.PATH_RAW}/{doc_id}.meta.json"
    if st.exists(meta_path) and read_json(meta_path).get("sha256") == sha:
        print(f"   = {doc_id} unchanged")
        return False
    title, text = parse_bytes(f"x.{ext}", data)
    st.write(f"{config.PATH_RAW}/{doc_id}.{ext}", data)
    meta = {"doc_id": doc_id, "ext": ext, "source_type": source_type, "origin": origin, "title": title,
            "chars": len(text), "bytes": len(data), "sha256": sha,
            "collected_at": datetime.now(timezone.utc).isoformat()}
    meta.update(extra or {})
    write_json(meta_path, meta)
    print(f"   + {doc_id}.{ext} ({source_type}, {len(text)} chars) {title[:50]}")
    return True


def crawl_filesystem(src):
    base = Path(src["path"])
    base = base if base.is_absolute() else config.ROOT / base
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.suffix.lower().lstrip(".") in src.get("extensions", ["txt", "md", "html", "pdf", "docx", "json", "eml"]):
            _store(slug(p.stem), p.suffix.lower().lstrip("."), p.read_bytes(), src.get("label", "filesystem"), str(p))


def crawl_web(src):
    seen, queue, n = set(), [(u, 0) for u in src["urls"]], 0
    rp_cache = {}
    while queue and n < src.get("max_pages", 20):
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        host = urlparse(url)
        rp = rp_cache.get(host.netloc)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser(f"{host.scheme}://{host.netloc}/robots.txt")
            try:
                rp.read()
            except Exception:
                pass
            rp_cache[host.netloc] = rp
        if not rp.can_fetch(UA, url):
            print(f"   x robots.txt forbids {url}")
            continue
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"   ! {url}: {e}")
            continue
        _store(slug(host.netloc + host.path) or "web-page", "html", r.content, "web", url,
               {"content_type": r.headers.get("content-type", "")})
        n += 1
        if depth < src.get("depth", 0):
            from bs4 import BeautifulSoup
            for a in BeautifulSoup(r.content, "html.parser").find_all("a", href=True):
                nxt = urljoin(url, a["href"]).split("#")[0]
                if urlparse(nxt).netloc == host.netloc:
                    queue.append((nxt, depth + 1))
        time.sleep(src.get("delay_s", 1.0))


def crawl_api(src):
    r = requests.get(src["url"], headers={**{"User-Agent": UA}, **src.get("headers", {})}, timeout=30)
    r.raise_for_status()
    _store(slug(src.get("name", src["url"])), "json", r.content, "api", src["url"])


def crawl_database(src):
    dsn = src["dsn"].replace("sqlite:///", "")
    path = Path(dsn) if Path(dsn).is_absolute() else config.ROOT / dsn
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    for q in src["queries"]:
        rows = [dict(r) for r in con.execute(q["sql"])]
        text = "\n".join(q["template"].format(**r) for r in rows)
        _store(slug(f"db-{q['name']}"), "txt", text.encode("utf-8"), "database", f"{src['dsn']}#{q['name']}")
    con.close()


HANDLERS = {"filesystem": crawl_filesystem, "web": crawl_web, "api": crawl_api, "database": crawl_database}


def run(config_path="config/sources.yaml"):
    p = Path(config_path)
    p = p if p.is_absolute() else config.ROOT / p
    cfg = yaml.safe_load(p.read_text())
    st = get_storage()
    st.makedirs(config.PATH_RAW)
    for src in cfg["sources"]:
        if not src.get("enabled", True):
            continue
        print(f"== source: {src['type']} {src.get('path') or src.get('url') or src.get('dsn') or src.get('urls')}")
        HANDLERS[src["type"]](src)
    print(f"Crawl done. Raw documents in storage:{config.PATH_RAW}: "
          f"{len([n for n in st.list(config.PATH_RAW) if n.endswith('.meta.json')])}")
