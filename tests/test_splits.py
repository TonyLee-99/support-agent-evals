import json
from pathlib import Path

from support_evals.bench.splits import first_n, load_tasks, official_splits, task_hash

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_test_is_official_and_unchanged():
    frozen = json.loads((ROOT / "results/splits/frozen_test.json").read_text())
    assert frozen["task_ids"] == official_splits()["test"] and len(frozen["task_ids"]) == 40
    tasks = load_tasks()
    for tid, h in frozen["task_sha256"].items():
        assert task_hash(tasks[tid]) == h, f"task {tid} changed"


def test_dev_disjoint_from_test_and_from_train():
    dev = json.loads((ROOT / "results/splits/dev.json").read_text())["task_ids"]
    assert not set(dev) & set(official_splits()["test"])
    assert set(dev) <= set(official_splits()["train"])


def test_subset_knob_is_prefix_of_frozen_order():
    order = json.loads((ROOT / "results/splits/test_order.json").read_text())
    assert first_n("test", 10) == sorted(order["order"][:10], key=int) == order["task_ids"]
    assert set(first_n("test", 40)) == set(official_splits()["test"])
