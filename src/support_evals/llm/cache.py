"""Append-only response cache: JSONL shards keyed by sha256 of the request.

Why JSONL shards instead of one file per key: ~10k live calls would mean ~10k tiny files; 256 append-only shards
are easy to diff, rsync and export, and `fcntl.flock` keeps concurrent writers from interleaving lines.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LIVE_DIR = ROOT / "data" / "cache"
CI_DIR = ROOT / "cache" / "ci"


def cache_key(*, model: str, system: str, prompt: str, schema: dict[str, Any] | None, sample: Any,
              backend: str = "claude_cli") -> str:
    # Backend is part of the key so an API run never silently reuses a CLI response (and vice versa); the replay
    # backend looks up every recorded backend. claude_cli keys omit the field to stay stable with early runs.
    d = {"model": model, "system": system, "prompt": prompt, "schema": schema, "sample": sample}
    if backend != "claude_cli":
        d["backend"] = backend
    payload = json.dumps(
        d,
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class DiskCache:
    def __init__(self, write_dir: Path | None = None, read_dirs: list[Path] | None = None):
        env_dir = os.environ.get("SE_CACHE_DIR")
        self.write_dir = Path(env_dir) if env_dir else (write_dir or DEFAULT_LIVE_DIR)
        self.read_dirs = [self.write_dir, *(read_dirs if read_dirs is not None else [CI_DIR])]
        self._shards: dict[tuple[Path, str], dict[str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def _load(self, d: Path, shard: str) -> dict[str, dict[str, Any]]:
        k = (d, shard)
        if k not in self._shards:
            entries: dict[str, dict[str, Any]] = {}
            f = d / f"{shard}.jsonl"
            if f.exists():
                for line in f.read_text().splitlines():
                    if line.strip():
                        rec = json.loads(line)
                        entries.setdefault(rec["key"], rec)  # first write wins
            self._shards[k] = entries
        return self._shards[k]

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            for d in self.read_dirs:
                rec = self._load(d, key[:2]).get(key)
                if rec is not None:
                    return rec["response"]
        return None

    def put(self, key: str, response: dict[str, Any], meta: dict[str, Any] | None = None) -> None:
        rec = {"key": key, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "meta": meta or {}, "response": response}
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        self.write_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(self.write_dir / f"{key[:2]}.jsonl", "a") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                fh.write(line)
                fcntl.flock(fh, fcntl.LOCK_UN)
            self._load(self.write_dir, key[:2]).setdefault(key, rec)

    def iter_records(self, d: Path | None = None):
        d = d or self.write_dir
        for f in sorted(d.glob("*.jsonl")):
            for line in f.read_text().splitlines():
                if line.strip():
                    yield json.loads(line)
