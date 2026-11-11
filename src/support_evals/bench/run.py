"""Batch runner: (task x trial) simulations through tau2's public Layer-2 API, resumable, <=4 concurrent.

Usage:
  uv run python -m support_evals.bench.run --agent llm_agent --split test --trials 3 --run-id base_test
  uv run python -m support_evals.bench.run --agent policy_agent --split dev --trials 1 --tasks 2,5
Output: results/tau2/<run-id>/sims.jsonl.gz (one SimulationRun per line, raw_data stripped) + meta.json.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from support_evals.llm.cache import ROOT

RESULTS = ROOT / "results" / "tau2"
TAU2_COMMIT = "c06b07dd7c4d4ec61ba6ff9bcdf0b15dd7ef36d4"


def setup(nl_judge_model: str, prefix: str = "claudecli") -> None:
    os.environ.setdefault("TAU2_LOG_LEVEL", "ERROR")
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level="ERROR")
    from support_evals.bench.agents import register_agents
    from support_evals.bench.provider import register

    register()
    register_agents()
    # tau2 grades NL assertions with an LLM (default gpt-5.4-mini, unreachable here). Route it to our backend;
    # this is a reported difference from the leaderboard setup (docs/EVAL.md).
    import tau2.evaluator.evaluator_nl_assertions as nl

    nl.DEFAULT_LLM_NL_ASSERTIONS = f"{prefix}/{nl_judge_model}"
    nl.DEFAULT_LLM_NL_ASSERTIONS_ARGS = {}


def strip(sim: dict[str, Any]) -> dict[str, Any]:
    for m in sim.get("messages") or []:
        m.pop("raw_data", None)
    return sim


def run_one(task, trial: int, args) -> dict[str, Any]:
    from tau2 import TextRunConfig
    from tau2.runner import build_text_orchestrator, run_simulation

    cfg = TextRunConfig(
        domain="retail",
        agent=args.agent,
        llm_agent=f"{args.prefix}/{args.model}",
        llm_args_agent={"metadata": {"trial": trial, "role": "agent"}},
        user="user_simulator",
        llm_user=f"{args.prefix}/{args.user_model}",
        llm_args_user={"metadata": {"trial": trial, "role": "user"}},
        max_steps=args.max_steps,
        seed=300 + trial,
    )
    orch = build_text_orchestrator(cfg, task, seed=300 + trial, simulation_id=f"{args.run_id}:{task.id}:{trial}")
    from support_evals.bench.provider import USAGE_LIMIT, infra_errors, reset_infra_errors
    from support_evals.llm.claude_cli import UsageLimitError

    reset_infra_errors()
    t0 = time.time()
    sim = run_simulation(orch)
    if USAGE_LIMIT.is_set():
        raise UsageLimitError("usage limit hit during simulation; result discarded")
    if infra_errors():
        raise RuntimeError(f"infra errors during simulation (not scored): {infra_errors()[:2]}")
    d = strip(json.loads(sim.model_dump_json()))
    d["se_trial"] = trial
    d["se_wall_s"] = round(time.time() - t0, 1)
    guard_log = getattr(orch.agent, "guard_log", None)
    if guard_log is not None:
        d["se_guard_log"] = guard_log
    return d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--split", default="dev")
    ap.add_argument("--tasks", default="", help="comma-separated task ids (subset of split)")
    ap.add_argument("--n", type=int, default=0, help="first N tasks of the split's frozen random order (0 = all)")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--trial-offset", type=int, default=0)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--user-model", default="claude-haiku-5-5")
    ap.add_argument("--judge-model", default="claude-haiku-5-5")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-steps", type=int, default=100)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--backend", default="claude_cli", choices=["claude_cli", "anthropic", "replay"])
    ap.add_argument("--interface", default="wrapper", choices=["wrapper", "native"],
                    help="wrapper = JSON respond schema (rounds 1-2); native = Messages-API tool_use (round 3, API only)")
    args = ap.parse_args(argv)
    args.prefix = "claudenative" if args.interface == "native" else "claudecli"
    if args.interface == "native" and args.backend == "claude_cli":
        raise SystemExit("native tool calling needs --backend anthropic (or replay)")
    os.environ["SE_LLM_BACKEND"] = args.backend
    if args.backend == "anthropic":
        from support_evals.llm.budget import key_from_keychain

        key = os.environ.get("ANTHROPIC_API_KEY") or key_from_keychain()
        if not key:
            raise SystemExit("no API key in env or keychain")
        os.environ["ANTHROPIC_API_KEY"] = key  # process memory only; never written anywhere

    setup(args.judge_model, "claudecli")  # NL grader keeps the round-2 JSON path: tau2 json.loads its raw text
    from tau2.runner.helpers import get_tasks

    from support_evals.bench.provider import STATS
    from support_evals.bench.splits import load_split
    from support_evals.llm.claude_cli import UsageLimitError, cli_version

    ids = load_split(args.split)
    if args.n:
        from support_evals.bench.splits import first_n

        ids = first_n(args.split, args.n)
    if args.tasks:
        want = args.tasks.split(",")
        bad = [t for t in want if t not in ids]
        if bad:
            raise SystemExit(f"tasks {bad} not in split {args.split}")
        ids = want
    tasks = {t.id: t for t in get_tasks("retail", task_ids=ids)}
    out_dir = RESULTS / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    sims_path = out_dir / "sims.jsonl.gz"
    done: set[tuple[str, int]] = set()
    if sims_path.exists():
        with gzip.open(sims_path, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                done.add((r["task_id"], r["se_trial"]))
    todo = [(tid, t) for t in range(args.trial_offset, args.trial_offset + args.trials) for tid in ids
            if (tid, t) not in done]
    meta_path = out_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta.update({
        "run_id": args.run_id, "agent": args.agent, "split": args.split, "task_ids": ids,
        "trials": sorted({*meta.get("trials", []), *range(args.trial_offset, args.trial_offset + args.trials)}),
        "agent_model": args.model, "user_model": args.user_model, "nl_judge_model": args.judge_model,
        "interface": args.interface,
        "backend": ("anthropic Messages API, native tool_use (tools + system cached)" if args.interface == "native"
                    else "claude_cli (claude -p --output-format json --json-schema)" if args.backend == "claude_cli"
                    else "anthropic Messages API (respond tool, cached system prefix; CLI fallback over budget)"),
        "cli_version": cli_version(),
        "tau2_commit": TAU2_COMMIT, "max_steps": args.max_steps, "domain": "retail",
    })
    meta_path.write_text(json.dumps(meta, indent=1))
    print(f"{args.run_id}: {len(todo)} simulations to run ({len(done)} already done)", flush=True)
    lock = threading.Lock()
    stop = threading.Event()
    errors: list[dict[str, Any]] = []

    def work(tid: str, trial: int):
        if stop.is_set():
            return None
        try:
            return run_one(tasks[tid], trial, args)
        except UsageLimitError:
            stop.set()
            raise
        except Exception as e:  # recorded, never silently dropped
            errors.append({"task_id": tid, "trial": trial, "error": repr(e)[:500],
                           "tb": traceback.format_exc()[-1500:]})
            return None

    t_start = time.time()
    n_ok = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, tid, tr): (tid, tr) for tid, tr in todo}
        for f in as_completed(futs):
            tid, tr = futs[f]
            try:
                d = f.result()
            except UsageLimitError as e:
                print(f"USAGE LIMIT: {e}", flush=True)
                continue
            if d is None:
                continue
            with lock, gzip.open(sims_path, "at") as fh:
                fh.write(json.dumps(d) + "\n")
            n_ok += 1
            r = (d.get("reward_info") or {}).get("reward")
            print(f"[{n_ok}/{len(todo)}] task {tid} trial {tr} reward={r} wall={d['se_wall_s']}s "
                  f"term={d.get('termination_reason')} elapsed={time.time() - t_start:.0f}s", flush=True)
    meta = json.loads(meta_path.read_text())
    meta.setdefault("adapter_stats", {})
    from support_evals.bench.native import STATS as NATIVE_STATS

    for k, v in [*STATS.items(), *((f"native:{k}", v) for k, v in NATIVE_STATS.items())]:
        meta["adapter_stats"][k] = meta["adapter_stats"].get(k, 0) + v
    meta.setdefault("errors", []).extend(errors)
    meta_path.write_text(json.dumps(meta, indent=1))
    print(f"done: {n_ok} ok, {len(errors)} errors, usage_limit={stop.is_set()}", flush=True)
    return 2 if stop.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
