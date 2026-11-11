"""Every headline number in README is regenerated from results/*.json and must appear verbatim."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
R = ROOT / "results"


def pct(x: float) -> str:
    return f"{round(100 * x)}%"


def test_tau2_numbers():
    s = json.loads((R / "tau2_test_summary.json").read_text())
    b, p = s["runs"]["baseline"], s["runs"]["policy_agent"]
    c = s["comparison"]["pass^1"]
    for needle in (f"pass^1 {pct(p['pass^1']['mean'])} vs {pct(b['pass^1']['mean'])}",
                   f"pass^2 = {pct(p['pass^2']['mean'])} vs {pct(b['pass^2']['mean'])}",
                   f"{pct(b['pass^1']['mean'])} [{round(100 * b['pass^1']['ci95'][0])}, {round(100 * b['pass^1']['ci95'][1])}]",
                   f"{pct(p['pass^1']['mean'])} [{round(100 * p['pass^1']['ci95'][0])}, {round(100 * p['pass^1']['ci95'][1])}]",
                   f"+{round(100 * c['diff'])} pts, CI [{round(100 * c['ci95'][0])}, +{round(100 * c['ci95'][1])}]",
                   f"{c['tasks_better']} tasks better, {c['tasks_worse']} worse"):
        assert needle in README, needle


def test_scenario_judge_regression_numbers():
    sc = json.loads((R / "scenarios_summary.json").read_text())
    for run in ("scen_baseline_20", "scen_v1_20", "scen_v2_20"):
        a = sc[run]["all"]
        assert f"| {run} | {pct(a['deterministic_pass']['rate'])} | {pct(a['judge_pass']['rate'])} |" in README, run
    j = json.loads((R / "judge_calibration.json").read_text())
    k = j["pairs"]["judge_vs_grader_a"]
    assert f"kappa {k['kappa']:.2f}" in README and f"{pct(k['agreement'])} agreement" in README
    for v in ("v1", "v2"):
        rep = json.loads((R / "regression" / f"report_{v}.json").read_text())
        assert f"{v}: {rep['service_pass_rate']:.2f}" in README, v
