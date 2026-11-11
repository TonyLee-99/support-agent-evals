"""Every headline number in README is regenerated from results/*.json and must appear verbatim."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
R = ROOT / "results"


def half_up(v: float) -> int:
    import math

    return math.floor(v + 0.5)  # same rounding as the dashboard (JS Math.round), so 82.5% -> 83%


def pct(x: float) -> str:
    return f"{half_up(100 * x)}%"


def test_tau2_numbers():
    s = json.loads((R / "tau2_test_summary.json").read_text())
    b, p = s["runs"]["baseline"], s["runs"]["policy_agent"]
    c = s["comparison"]["pass^1"]
    def ci(x):
        return f"{pct(x['mean'])} [{half_up(100 * x['ci95'][0])}, {half_up(100 * x['ci95'][1])}]"

    def signed(v):
        return f"{half_up(100 * v):+d}".replace("+0", "0")

    c3 = s["comparison"]["pass^3"]
    for needle in (f"pass^1 {pct(p['pass^1']['mean'])} vs {pct(b['pass^1']['mean'])}",
                   *(ci(r[f"pass^{k}"]) for r in (b, p) for k in (1, 2, 3)),
                   f"{signed(c['diff'])} pts, CI [{signed(c['ci95'][0])}, {signed(c['ci95'][1])}]",
                   f"pass^3: {signed(c3['diff'])} pts, CI [{signed(c3['ci95'][0])}, {signed(c3['ci95'][1])}]",
                   f"{c['tasks_better']} tasks better, {c['tasks_worse']} worse"):
        assert needle in README, needle


def test_scenario_judge_regression_numbers():
    sc = json.loads((R / "scenarios_summary.json").read_text())
    for run in ("scen_baseline_20", "scen_v1_20", "scen_v2_20", "scen_v3_20"):
        a = sc[run]["all"]
        assert f"| {run} | {pct(a['deterministic_pass']['rate'])} | {pct(a['judge_pass']['rate'])} |" in README, run
    j = json.loads((R / "judge_calibration.json").read_text())
    k = j["pairs"]["judge_vs_grader_a"]
    assert f"kappa {k['kappa']:.2f}" in README and f"{pct(k['agreement'])} agreement" in README
    for v in ("v1", "v2", "v3"):
        rep = json.loads((R / "regression" / f"report_{v}.json").read_text())
        assert f"{v}: {rep['service_pass_rate']:.2f}" in README, v
