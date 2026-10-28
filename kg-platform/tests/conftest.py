import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update(STORAGE_BACKEND="local", STORAGE_ROOT="/tmp/kgp_test_store", LLM_PROVIDER="mock")
shutil.rmtree("/tmp/kgp_test_store", ignore_errors=True)


import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def pipeline():
    from kgp import crawler, etl_python, extraction
    from kgp.resolution import resolve_docs
    from kgp.storage import write_jsonl
    crawler.run()
    extraction.run(force=True)
    write_jsonl("/data/resolved/resolved.jsonl", resolve_docs(extraction.load_extracted(), True))
    etl_python.run()
