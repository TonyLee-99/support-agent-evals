"""Task splits. Test = tau2's own retail `test` split (chosen by the benchmark authors), frozen by content hash."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from support_evals.llm.cache import ROOT

TAU2_DIR = ROOT / "third_party" / "tau2-bench"
RETAIL_DIR = TAU2_DIR / "data" / "tau2" / "domains" / "retail"
SPLITS_DIR = ROOT / "results" / "splits"
DEV_SIZE = 30
DEV_SEED = 20261111


def load_tasks() -> dict[str, dict]:
    return {t["id"]: t for t in json.loads((RETAIL_DIR / "tasks.json").read_text())}


def task_hash(task: dict) -> str:
    return hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest()


def official_splits() -> dict[str, list[str]]:
    return json.loads((RETAIL_DIR / "split_tasks.json").read_text())


def make_splits() -> dict[str, dict]:
    tasks = load_tasks()
    off = official_splits()
    dev = sorted(random.Random(DEV_SEED).sample(off["train"], DEV_SIZE), key=int)
    out = {}
    for name, ids in (("frozen_test", off["test"]), ("dev", dev)):
        out[name] = {
            "source": "tau2 retail split_tasks.json['test']" if name == "frozen_test"
            else f"random.Random({DEV_SEED}).sample(split_tasks.json['train'], {DEV_SIZE})",
            "task_ids": ids,
            "task_sha256": {i: task_hash(tasks[i]) for i in ids},
        }
    return out


def load_split(name: str) -> list[str]:
    if name in ("test", "frozen_test"):
        return json.loads((SPLITS_DIR / "frozen_test.json").read_text())["task_ids"]
    if name == "dev":
        return json.loads((SPLITS_DIR / "dev.json").read_text())["task_ids"]
    if name == "train_rest":
        dev = set(load_split("dev"))
        return [i for i in official_splits()["train"] if i not in dev]
    raise ValueError(name)


if __name__ == "__main__":
    SPLITS_DIR.mkdir(parents=True, exist_ok=True)
    for n, s in make_splits().items():
        p: Path = SPLITS_DIR / f"{n}.json"
        if p.exists():
            raise SystemExit(f"{p} exists; splits are frozen")
        p.write_text(json.dumps(s, indent=1) + "\n")
        print(n, len(s["task_ids"]))
