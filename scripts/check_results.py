"""Offline consistency checks of committed results: summaries must be recomputable from the raw run files."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "results"
sys.path.insert(0, str(ROOT / "src"))


def tau2_test() -> bool:
    from support_evals.bench.metrics import load_sims, successes_by_task, summarize

    s = json.loads((R / "tau2_test_summary.json").read_text())
    ok = True
    n = s["runs"]["baseline"]["n_tasks"]
    k = s["runs"]["baseline"]["n_trials_min"]
    for name, run in (("baseline", f"llm_agent_test{n}_api"), ("policy_agent", f"policy_agent_test{n}_api")):
        fresh = summarize(successes_by_task(load_sims(R / "tau2" / run)), list(range(1, k + 1)))
        ok &= all(fresh[f"pass^{j}"] == s["runs"][name][f"pass^{j}"] for j in range(1, k + 1))
        ok &= fresh["n_tasks"] == n and fresh["n_trials_min"] == k
        ok &= s["runs"][name]["config"]["split"] == "test"
    frozen = set(json.loads((R / "splits/frozen_test.json").read_text())["task_ids"])
    ok &= set(s["runs"]["baseline"]["config"]["task_ids"]) <= frozen
    return ok


def ablations() -> bool:
    a = json.loads((R / "ablations.json").read_text())
    return len([r for r in a["rows"] if "pass^1" in r]) >= 3


def scenarios() -> bool:
    s = json.loads((R / "scenarios_summary.json").read_text())
    need = {"scen_baseline_20", "scen_v1_20"}
    return need <= set(s) and all(s[r]["all"]["deterministic_pass"]["n"] == 20 for r in need)


def taxonomy() -> bool:
    t = json.loads((R / "flywheel/taxonomy.json").read_text())
    return t["n_failures"] > 0 and len(t["clusters"]) >= 2 and sum(t["category_counts"].values()) == t["n_failures"]


def judge() -> bool:
    c = json.loads((R / "judge_calibration.json").read_text())
    return c["n"] >= 10 and "kappa" in c["pairs"]["judge_vs_grader_a"]


if __name__ == "__main__":
    fn = {"tau2_test": tau2_test, "ablations": ablations, "scenarios": scenarios, "taxonomy": taxonomy,
          "judge": judge}[sys.argv[1]]
    try:
        ok = fn()
    except FileNotFoundError as e:
        print(f"missing: {e}")
        ok = False
    print("OK" if ok else "FAIL")
    sys.exit(0 if ok else 1)
