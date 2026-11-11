"""Turn recorded runs into the summary files that README, EVAL.md and the dashboard read.

  uv run python -m support_evals.report tau2        -> results/tau2_test_summary.json, results/tau2_dev_summary.json
  uv run python -m support_evals.report judge RUN   -> results/scenarios/RUN/judged.jsonl (LLM judge, cached)
  uv run python -m support_evals.report scenarios   -> results/scenarios_summary.json
  uv run python -m support_evals.report ledger      -> results/ledger_summary.json
"""

from __future__ import annotations

import glob
import json
import sys
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from support_evals.bench.metrics import (
    bootstrap_ci,
    db_reward,
    load_sims,
    paired,
    successes_by_task,
    summarize,
)
from support_evals.llm.cache import ROOT

R = ROOT / "results"


def _meta(run: str) -> dict[str, Any]:
    return json.loads((R / "tau2" / run / "meta.json").read_text())


def tau2_pair(base: str, cand: str, k: int) -> dict[str, Any]:
    out: dict[str, Any] = {"runs": {}, "comparison": {}}
    by: dict[str, Any] = {}
    for name, run in (("baseline", base), ("policy_agent", cand)):
        sims = load_sims(R / "tau2" / run)
        m = _meta(run)
        bt = successes_by_task(sims)
        by[name] = bt
        s = summarize(bt, list(range(1, k + 1)))
        s["db_only"] = summarize(successes_by_task(sims, key=db_reward), list(range(1, k + 1)))
        s["mean_wall_s"] = round(float(np.mean([x["se_wall_s"] for x in sims])), 1)
        s["termination"] = dict(Counter(x["termination_reason"] for x in sims))
        s["config"] = {key: m[key] for key in ("agent", "agent_model", "user_model", "nl_judge_model", "backend",
                                               "tau2_commit", "split", "task_ids", "trials")}
        s["adapter_stats"] = m.get("adapter_stats", {})
        s["discarded_sims"] = len(m.get("errors", []))
        out["runs"][name] = s
    out["comparison"] = {f"pass^{j}": paired(by["baseline"], by["policy_agent"], j) for j in range(1, k + 1)}
    return out


DIFFS = [
        "agent model claude-haiku-5-5 (leaderboard: various)", "user simulator claude-haiku-5-5 (leaderboard: gpt-4.1)",
        "NL-assertion grader claude-haiku-5-5 (tau2 default: gpt-5.4-mini)",
        "{n} of the 40 official retail test tasks (frozen random order), k={k} (leaderboard: 114 tasks, k=4)",
        "tool calling via a JSON 'respond' tool on the Messages API (not native per-tool function calling)",
]


def tau2() -> None:
    # the 10x2 run is kept as history; the headline is the largest completed run
    sizes = [(10, 2)] + ([(40, 3)] if (R / "tau2" / "policy_agent_test40_api" / "sims.jsonl.gz").exists() else [])
    for n, k in sizes:
        test = tau2_pair(f"llm_agent_test{n}_api", f"policy_agent_test{n}_api", k)
        test["differences_from_leaderboard"] = [d.format(n=n, k=k) for d in DIFFS]
        (R / f"tau2_test{n}_summary.json").write_text(json.dumps(test, indent=1))
    (R / "tau2_test_summary.json").write_text(json.dumps(test, indent=1))
    dev = tau2_pair("llm_agent_dev10", "policy_agent_dev10", 1)
    (R / "tau2_dev_summary.json").write_text(json.dumps(dev, indent=1))
    for name, s in test["runs"].items():
        print(name, s["pass^1"], s.get("pass^2"))
    print("diff", test["comparison"]["pass^1"])


def ablations() -> None:
    from support_evals.bench.agents import ALL_FLAGS, agent_name

    rows = []
    ref = successes_by_task(load_sims(R / "tau2" / "policy_agent_dev10"))
    for label, run in [("baseline (tau2 default)", "llm_agent_dev10"), ("full policy_agent", "policy_agent_dev10")] + \
            [(f"minus {f}", agent_name(frozenset(ALL_FLAGS) - {f}) + "_dev10") for f in ALL_FLAGS]:
        p = R / "tau2" / run / "sims.jsonl.gz"
        if not p.exists():
            rows.append({"variant": label, "run": run, "status": "not run"})
            continue
        bt = successes_by_task(load_sims(R / "tau2" / run))
        s = summarize(bt, [1])
        rows.append({"variant": label, "run": run, "n_tasks": s["n_tasks"], "pass^1": s["pass^1"],
                     "vs_full": paired(ref, bt, 1)})
    (R / "ablations.json").write_text(json.dumps({"split": "dev (10 tasks, k=1)", "note": "exploratory: one trial, "
                                                  "10 tasks; CIs are wide and no causal claim is made", "rows": rows},
                                                 indent=1))
    for r in rows:
        print(r["variant"], r.get("pass^1"))


def judge_run(run: str) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from support_evals.judge.judge import judge
    from support_evals.scenarios import load_scenarios

    sc = {s["id"]: s for s in load_scenarios("all")}
    runs = [json.loads(line) for line in (R / "scenarios" / run / "runs.jsonl").read_text().splitlines()]
    with ThreadPoolExecutor(4) as ex:
        verdicts = list(ex.map(lambda r: judge(r, sc[r["scenario_id"]]), runs))
    with open(R / "scenarios" / run / "judged.jsonl", "w") as fh:
        for r, v in zip(runs, verdicts, strict=True):
            fh.write(json.dumps({"scenario_id": r["scenario_id"], "judge": v}) + "\n")
    print(run, Counter(v["overall"] for v in verdicts))


def scenarios() -> None:
    out: dict[str, Any] = {}
    for d in sorted(glob.glob(str(R / "scenarios" / "scen_*"))):
        run = d.rsplit("/", 1)[1]
        runs = [json.loads(line) for line in open(f"{d}/runs.jsonl")]
        jp = f"{d}/judged.jsonl"
        jm = {j["scenario_id"]: j["judge"] for j in map(json.loads, open(jp))} if glob.glob(jp) else {}
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in runs:
            for g in ("all", f"split:{r['split']}", f"author:{r['author']}", f"category:{r['category']}"):
                groups[g].append(r)

        def rate(rs, f):
            v = np.array([1.0 if f(r) else 0.0 for r in rs])
            lo, hi = bootstrap_ci(v) if len(v) > 1 else (float(v.mean()), float(v.mean()))
            return {"rate": round(float(v.mean()), 3), "n": len(v), "ci95": [round(lo, 3), round(hi, 3)]}

        res = {}
        for g, rs in groups.items():
            res[g] = {
                "deterministic_pass": rate(rs, lambda r: r["checks"]["pass"]),
                "judge_pass": rate(rs, lambda r, jm=jm: jm.get(r["scenario_id"], {}).get("overall") == "pass") if jm else None,
                "handoff_rate": rate(rs, lambda r: r["state"] == "handed_off"),
                "server_blocked_write_rate": rate(rs, lambda r: bool(r["checks"]["server_blocked_writes"])),
                "leak_rate": rate(rs, lambda r: any(k.startswith("leak:") and not v["pass"]
                                                    for k, v in r["checks"]["items"].items())),
            }
        tot = [r["trace"]["totals"] for r in runs]
        res["cost"] = {"mean_llm_calls": round(float(np.mean([t["llm_calls"] for t in tot])), 1),
                       "mean_llm_s": round(float(np.mean([t["llm_ms"] for t in tot])) / 1000, 1),
                       "mean_cost_usd_list": round(float(np.mean([t["cost_usd"] for t in tot])), 5),
                       "p95_llm_s": round(float(np.percentile([t["llm_ms"] for t in tot], 95)) / 1000, 1)}
        out[run] = res
    (R / "scenarios_summary.json").write_text(json.dumps(out, indent=1))
    for run, res in out.items():
        print(run, res["all"]["deterministic_pass"], (res["all"]["judge_pass"] or {}).get("rate"))


def ledger() -> None:
    from support_evals.llm.budget import CAP_USD, project_usd, total_usd

    cli_cost, cli_calls = 0.0, 0
    for f in glob.glob(str(ROOT / "data/cache/*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r["meta"].get("backend") == "claude_cli":
                cli_cost += r["response"].get("cost_usd", 0.0)
                cli_calls += 1
    usd, n = project_usd()
    out = {"anthropic_api": {"project_usd": round(usd, 4), "project_calls": n, "shared_ledger_total_usd":
                             round(total_usd(), 4), "cap_usd": CAP_USD},
           "claude_cli_subscription": {"live_calls": cli_calls, "list_price_equivalent_usd": round(cli_cost, 4),
                                       "note": "subscription; no billing. CLI reports list-price-equivalent cost"}}
    (R / "ledger_summary.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out))


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"tau2": tau2, "ablations": ablations, "scenarios": scenarios, "ledger": ledger}.get(cmd, lambda: None)()
    if cmd == "judge":
        judge_run(sys.argv[2])
