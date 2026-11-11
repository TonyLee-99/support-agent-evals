"""pass^k (tau2 definition), task-cluster bootstrap CIs and paired comparisons.

pass^k for one task with c successes in n trials = C(c, k) / C(n, k): the probability that k i.i.d. trials drawn
from those n all succeed. Averaged over tasks. The CI resamples *tasks* (the unit we generalise over), not
simulations, because trials of the same task are correlated.
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from math import comb
from pathlib import Path
from typing import Any

import numpy as np

SUCCESS = 1.0 - 1e-6
N_BOOT = 2000
SEED = 0


def load_sims(run_dir: Path) -> list[dict[str, Any]]:
    with gzip.open(run_dir / "sims.jsonl.gz", "rt") as fh:
        return [json.loads(line) for line in fh]


def reward(sim: dict[str, Any]) -> float:
    return float((sim.get("reward_info") or {}).get("reward") or 0.0)


def db_reward(sim: dict[str, Any]) -> float:
    ri = sim.get("reward_info") or {}
    return float((ri.get("db_check") or {}).get("db_reward") or 0.0)


def successes_by_task(sims: list[dict[str, Any]], key=reward, trials: list[int] | None = None) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for s in sims:
        if trials is not None and s["se_trial"] not in trials:
            continue
        out[s["task_id"]].append(1 if key(s) >= SUCCESS else 0)
    return dict(out)


def pass_hat_k_task(c: int, n: int, k: int) -> float:
    return comb(c, k) / comb(n, k) if n >= k else float("nan")


def pass_hat_k(by_task: dict[str, list[int]], k: int, tasks: list[str] | None = None) -> np.ndarray:
    tasks = tasks or sorted(by_task, key=lambda t: int(t) if t.isdigit() else t)
    return np.array([pass_hat_k_task(sum(by_task[t]), len(by_task[t]), k) for t in tasks])


def bootstrap_ci(values: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(n_boot, len(values)))
    means = values[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def summarize(by_task: dict[str, list[int]], ks: list[int]) -> dict[str, Any]:
    """pass^k uses each task's own trial count n (C(c,k)/C(n,k) is unbiased for any n >= k); a task with fewer
    than k completed trials (a discarded simulation) is left out of pass^k and counted in n_tasks_used."""
    n_trials = min(len(v) for v in by_task.values())
    n_max = max(len(v) for v in by_task.values())
    out: dict[str, Any] = {"n_tasks": len(by_task), "n_trials_min": n_trials, "n_trials_max": n_max,
                           "n_sims": sum(len(v) for v in by_task.values())}
    for k in ks:
        tasks = [t for t, v in by_task.items() if len(v) >= k]
        if not tasks:
            continue
        v = pass_hat_k(by_task, k, sorted(tasks, key=lambda t: int(t) if t.isdigit() else t))
        lo, hi = bootstrap_ci(v)
        out[f"pass^{k}"] = {"mean": round(float(v.mean()), 4), "ci95": [round(lo, 4), round(hi, 4)],
                            "n_tasks_used": len(tasks)}
    return out


def paired(a: dict[str, list[int]], b: dict[str, list[int]], k: int) -> dict[str, Any]:
    """b minus a on the common tasks; paired bootstrap over tasks."""
    tasks = sorted((t for t in set(a) & set(b) if len(a[t]) >= k and len(b[t]) >= k),
                   key=lambda t: int(t) if t.isdigit() else t)
    va, vb = pass_hat_k(a, k, tasks), pass_hat_k(b, k, tasks)
    d = vb - va
    lo, hi = bootstrap_ci(d)
    return {
        "k": k, "n_tasks": len(tasks), "diff": round(float(d.mean()), 4), "ci95": [round(lo, 4), round(hi, 4)],
        "tasks_better": int((d > 0).sum()), "tasks_worse": int((d < 0).sum()), "tasks_same": int((d == 0).sum()),
        "p_boot_le_0": round(float(_p_le_zero(d)), 4),
    }


def _p_le_zero(d: np.ndarray) -> float:
    rng = np.random.default_rng(SEED + 1)
    idx = rng.integers(0, len(d), size=(N_BOOT, len(d)))
    return float((d[idx].mean(axis=1) <= 0).mean())
