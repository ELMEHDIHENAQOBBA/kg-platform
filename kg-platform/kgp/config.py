"""Central configuration, read from environment variables (see .env.example)."""
import os
from pathlib import Path

ROOT = Path(os.getenv("KGP_ROOT", Path(__file__).resolve().parent.parent))


def _bool(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ---- Storage (HDFS in Docker, local folder for quick tests) ----
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "hdfs")  # hdfs | local
STORAGE_ROOT = os.getenv("STORAGE_ROOT", str(ROOT / "storage"))
HDFS_URL = os.getenv("HDFS_URL", "http://namenode:9870")  # WebHDFS
HDFS_USER = os.getenv("HDFS_USER", "root")
HDFS_NAMENODE = os.getenv("HDFS_NAMENODE", "namenode:9000")  # used for hdfs:// URIs (Spark)

PATH_RAW = "/data/raw"
PATH_EXTRACTED = "/data/extracted"
PATH_RESOLVED = "/data/resolved"
PATH_CURATED = "/data/curated"

# ---- Neo4j ----
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "kgpassword")

# ---- LLM ----
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")  # mock | ollama | anthropic | openai
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_PRICE_IN = float(os.getenv("LLM_PRICE_IN", "0"))    # USD per 1M input tokens
LLM_PRICE_OUT = float(os.getenv("LLM_PRICE_OUT", "0"))  # USD per 1M output tokens

# ---- Pipeline ----
ENTITY_RESOLUTION = _bool("ENTITY_RESOLUTION", True)
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.5"))
CHUNK_CHARS = int(os.getenv("CHUNK_CHARS", "1500"))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90"))


# ---- API / access control ----
# Format: "key1:role,key2:role" with role in {reader, admin}. Empty = auth disabled (dev only).
def _parse_keys(raw):
    out = {}
    for item in filter(None, (x.strip() for x in raw.split(","))):
        k, _, role = item.partition(":")
        out[k] = role or "reader"
    return out


API_KEYS = _parse_keys(os.getenv("API_KEYS", ""))
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", ROOT / "reports"))
