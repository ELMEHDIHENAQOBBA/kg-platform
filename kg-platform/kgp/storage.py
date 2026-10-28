"""Storage abstraction: HDFS (WebHDFS) or a local folder with the same API."""
import json
import shutil
from pathlib import Path

from . import config


class LocalStorage:
    def __init__(self, root):
        self.root = Path(root)

    def _p(self, path):
        return self.root / path.lstrip("/")

    def write(self, path, data):
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)

    def read(self, path):
        return self._p(path).read_bytes()

    def list(self, path):
        p = self._p(path)
        return sorted(x.name for x in p.iterdir()) if p.exists() else []

    def exists(self, path):
        return self._p(path).exists()

    def makedirs(self, path):
        self._p(path).mkdir(parents=True, exist_ok=True)

    def delete(self, path):
        p = self._p(path)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    def mtime(self, path):
        return self._p(path).stat().st_mtime

    def uri(self, path):
        return "file://" + str(self._p(path))


class HDFSStorage:
    def __init__(self, url, user):
        from hdfs import InsecureClient
        self.c = InsecureClient(url, user=user)

    def write(self, path, data):
        data = data.encode() if isinstance(data, str) else data
        self.c.write(path, data=data, overwrite=True)

    def read(self, path):
        with self.c.read(path) as r:
            return r.read()

    def list(self, path):
        return sorted(self.c.list(path)) if self.c.status(path, strict=False) else []

    def exists(self, path):
        return self.c.status(path, strict=False) is not None

    def makedirs(self, path):
        self.c.makedirs(path)

    def delete(self, path):
        self.c.delete(path, recursive=True)

    def mtime(self, path):
        return self.c.status(path)["modificationTime"] / 1000.0

    def uri(self, path):
        return f"hdfs://{config.HDFS_NAMENODE}{path}"


_storage = None


def get_storage():
    global _storage
    if _storage is None:
        if config.STORAGE_BACKEND == "local":
            _storage = LocalStorage(config.STORAGE_ROOT)
        else:
            _storage = HDFSStorage(config.HDFS_URL, config.HDFS_USER)
    return _storage


def write_json(path, obj):
    get_storage().write(path, json.dumps(obj, ensure_ascii=False, indent=1))


def read_json(path):
    return json.loads(get_storage().read(path).decode("utf-8"))


def write_jsonl(path, rows):
    get_storage().write(path, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


def read_jsonl_dir(path):
    """Yield every JSON line of the data files in a directory (handles Spark part-* files)."""
    st = get_storage()
    for name in st.list(path):
        if name.startswith((".", "_")) or not (name.startswith("part-") or name.endswith((".jsonl", ".json"))):
            continue
        for line in st.read(f"{path}/{name}").decode("utf-8").splitlines():
            if line.strip():
                yield json.loads(line)
