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
        # errors[] logs every failed *attempt* (a re-run of the same task/trial appends again); report both views
        kept = {(x["task_id"], x["se_trial"]) for x in sims}
        failed = [(e["task_id"], e["trial"]) for e in m.get("errors", [])]
        s["failed_attempts"] = len(failed)
        s["recovered_on_rerun"] = len({f for f in failed if f in kept})
        s["missing_sims"] = sorted([list(f) for f in set(failed) - kept])
        s["planned_sims"] = len(m["task_ids"]) * len(m["trials"])
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
    sizes = [(10, 2)] + ([(40, 3)] if (R / "tau2" / "policy_agent_test40_api" / "meta.json").exists() and
                         2 in json.loads((R / "tau2" / "policy_agent_test40_api" / "meta.json").read_text())["trials"]
                         else [])
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
    (R / "ablations.json").write_text(json.dumps({"split": "dev (10 tasks, k=1)", "note": "directional only: 10 tasks x 1 trial; "
                                                  "every CI includes 0 (e.g. minus planner -20 pts, CI -60 to +20)", "rows": rows},
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


def invalid_rate(run: str) -> dict[str, Any]:
    """Share of agent LLM turns the interface had to reject and retry (wrapper: bad JSON; native: empty turn)."""
    st = _meta(run).get("adapter_stats", {})
    if any(k.startswith("native:") for k in st):
        calls, bad = st.get("native:agent:calls", 0), st.get("native:agent:invalid_empty", 0)
    else:
        calls, bad = st.get("agent:calls", 0), st.get("agent:adapter_invalid", 0)
    return {"agent_calls": calls, "invalid": bad, "rate": round(bad / calls, 4) if calls else None}


def round3() -> None:
    out: dict[str, Any] = {"prereg": "docs/PREREG_round3.md", "dev": {}, "test": None}
    for run in ("llm_agent_dev30", "policy_agent_dev30", "policy_agent_r3_dev30", "policy_agent_r3b_dev30",
                "llm_agent_dev10_native", "policy_agent_r3_dev10_native"):
        p = R / "tau2" / run / "sims.jsonl.gz"
        if p.exists():
            bt = successes_by_task(load_sims(R / "tau2" / run))
            out["dev"][run] = {**summarize(bt, [1]), "interface": _meta(run).get("interface", "wrapper"),
                               "invalid": invalid_rate(run)}
    if (R / "tau2" / "policy_agent_r3_test40_native" / "sims.jsonl.gz").exists():
        k = max(len(_meta("policy_agent_r3_test40_native")["trials"]), 1)
        t = tau2_pair("llm_agent_test40_native", "policy_agent_r3_test40_native", k)
        for name, run in (("baseline", "llm_agent_test40_native"), ("policy_agent", "policy_agent_r3_test40_native")):
            t["runs"][name]["invalid"] = invalid_rate(run)
        t["differences_from_leaderboard"] = [d.format(n=40, k=k).replace(
            "via a JSON 'respond' tool on the Messages API (not native per-tool function calling)",
            "native Messages-API tool_use, one call per turn") for d in DIFFS]
        out["test"] = t
    out["wrapper_test40_invalid"] = {r: invalid_rate(r) for r in ("llm_agent_test40_api", "policy_agent_test40_api")}
    # Amendment 1 gate: r3 vs llm_agent on devplus (40 non-test tasks), k=2, same CLI interface
    a, b = R / "tau2" / "llm_agent_devplus_k2", R / "tau2" / "policy_agent_r3_devplus_k2"
    if (a / "sims.jsonl.gz").exists() and (b / "sims.jsonl.gz").exists():
        ba, bb = successes_by_task(load_sims(a)), successes_by_task(load_sims(b))
        d1 = paired(ba, bb, 1)
        out["gate"] = {"split": "devplus (40 tasks, none in test)", "k": 2,
                       "baseline": summarize(ba, [1, 2]), "policy_agent_r3": summarize(bb, [1, 2]),
                       "paired_pass^1": d1, "paired_pass^2": paired(ba, bb, 2),
                       "rule": "GO iff paired pass^1 lift >= +0.08 and CI lower bound > 0",
                       "verdict": "GO" if d1["diff"] >= 0.08 and d1["ci95"][0] > 0 else "NO-GO"}
    # native cost per simulation, measured on the dev check (agent + user sim + NL grader)
    import glob as _g
    cost = 0.0
    for f in _g.glob(str(ROOT / "data/cache/*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r["meta"].get("backend") == "anthropic_native":
                cost += r["response"].get("cost_usd", 0.0)
    nat = [x for x in ("native_smoke", "llm_agent_dev10_native") if (R / "tau2" / x / "sims.jsonl.gz").exists()]
    n_sims = sum(len(load_sims(R / "tau2" / x)) for x in nat)
    if n_sims:
        per = cost / n_sims
        out["native_cost_estimate"] = {
            "measured_usd_per_sim_baseline": round(per, 5), "assumed_policy_overhead": 1.15,
            "test_run_usd": {f"K={k}": round(40 * k * per * (1 + 1.15), 2) for k in (2, 3, 4)},
            "note": "both agents, 40 tasks; policy agent assumed 15% more calls (guard retries); excl. NL grader"}
    (R / "round3_summary.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: (v.get("pass^1"), v["invalid"]) for k, v in out["dev"].items()}))
    if out["test"]:
        print(out["test"]["runs"]["baseline"]["pass^1"], out["test"]["runs"]["policy_agent"]["pass^1"],
              out["test"]["comparison"]["pass^1"])


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"tau2": tau2, "ablations": ablations, "scenarios": scenarios, "ledger": ledger, "round3": round3}.get(
        cmd, lambda: None)()
    if cmd == "judge":
        judge_run(sys.argv[2])
