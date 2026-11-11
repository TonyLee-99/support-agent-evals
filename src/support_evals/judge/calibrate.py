"""Judge calibration: sample conversations, export a blind grading packet, score agreement.

  sample:  uv run python -m support_evals.judge.calibrate sample --n 10
           -> results/judge_calibration/sample.json + packet.md (rubric + transcripts, no judge output)
  score:   uv run python -m support_evals.judge.calibrate score
           reads grader_a.json (independent Claude grader), grader_b.json (codex), and the LLM judge's verdicts
           -> results/judge_calibration.json
Sampling: stratified by deterministic-check outcome (half pass, half fail when available), seed 7, from the
baseline and v1 scenario runs.
"""

from __future__ import annotations

import json
import random
import sys
from typing import Any

import numpy as np

from support_evals.judge.judge import DIMS, RUBRIC, render_transcript
from support_evals.llm.cache import ROOT
from support_evals.scenarios import load_scenarios

D = ROOT / "results" / "judge_calibration"
RUNS = ("scen_baseline_20", "scen_v1_20")


def _runs() -> list[dict[str, Any]]:
    out = []
    for run in RUNS:
        p = ROOT / "results/scenarios" / run / "runs.jsonl"
        for r in map(json.loads, p.read_text().splitlines()):
            r["_run"] = run
            out.append(r)
    return out


def sample(n: int = 10) -> None:
    runs = _runs()
    rng = random.Random(7)
    passed = [r for r in runs if r["checks"]["pass"]]
    failed = [r for r in runs if not r["checks"]["pass"]]
    k_fail = min(len(failed), n // 2)
    pick = rng.sample(failed, k_fail) + rng.sample(passed, n - k_fail)
    rng.shuffle(pick)
    sc = {s["id"]: s for s in load_scenarios("all")}
    D.mkdir(parents=True, exist_ok=True)
    items = [{"item": i + 1, "run": r["_run"], "scenario_id": r["scenario_id"]} for i, r in enumerate(pick)]
    (D / "sample.json").write_text(json.dumps({"seed": 7, "strata": {"det_fail": k_fail, "det_pass": n - k_fail},
                                               "items": items}, indent=1))
    parts = ["# Grading packet\n", "Grade each conversation independently with this rubric. Return JSON: "
             '[{"item": 1, "policy_adherence": "pass|fail", "goal_handling": "pass|fail", "tone": "pass|fail", '
             '"overall": "pass|fail", "note": "..."}, ...]\n', RUBRIC]
    for it, r in zip(items, pick, strict=True):
        s = sc[r["scenario_id"]]
        parts.append(f"\n---\n## Item {it['item']}\n**Customer goal and behaviour:** {s['instructions']}\n\n"
                     f"**Scenario rubric:** {s['rubric']}\n\n```\n{render_transcript(r)}\n```\n")
    (D / "packet.md").write_text("\n".join(parts))
    print(f"sampled {n} ({k_fail} det-fail) -> {D / 'packet.md'}")


def kappa(a: list[str], b: list[str]) -> float | None:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = a.count("pass") / n, b.count("pass") / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def boot_kappa(a: list[str], b: list[str], n_boot: int = 2000) -> list[float] | None:
    rng = np.random.default_rng(0)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(a), len(a))
        k = kappa([a[i] for i in idx], [b[i] for i in idx])
        if k is not None:
            vals.append(k)
    return [round(float(np.percentile(vals, 2.5)), 3), round(float(np.percentile(vals, 97.5)), 3)] if vals else None


def score() -> None:
    s = json.loads((D / "sample.json").read_text())
    graders = {"grader_a": json.loads((D / "grader_a.json").read_text()),
               "grader_b": json.loads((D / "grader_b.json").read_text())}
    judge: dict[int, dict[str, str]] = {}
    for it in s["items"]:
        jp = ROOT / "results/scenarios" / it["run"] / "judged.jsonl"
        jm = {j["scenario_id"]: j["judge"] for j in map(json.loads, jp.read_text().splitlines())}
        judge[it["item"]] = jm[it["scenario_id"]]
    g = {name: {x["item"]: x for x in v} for name, v in graders.items()}
    ids = [it["item"] for it in s["items"]]
    labels = {"judge": [judge[i]["overall"] for i in ids], "grader_a": [g["grader_a"][i]["overall"] for i in ids],
              "grader_b": [g["grader_b"][i]["overall"] for i in ids]}
    pairs = {}
    for x, y in (("judge", "grader_a"), ("judge", "grader_b"), ("grader_a", "grader_b")):
        a, b = labels[x], labels[y]
        k = kappa(a, b)
        pairs[f"{x}_vs_{y}"] = {"agreement": round(sum(p == q for p, q in zip(a, b, strict=True)) / len(a), 3),
                                "kappa": None if k is None else round(k, 3), "kappa_ci95": boot_kappa(a, b)}
    per_dim = {}
    for d in DIMS:
        a = [judge[i][d] for i in ids]
        for gname in ("grader_a", "grader_b"):
            b = [g[gname][i][d] for i in ids]
            per_dim[f"{d}:judge_vs_{gname}"] = round(sum(p == q for p, q in zip(a, b, strict=True)) / len(a), 3)
    dis = []
    for it in s["items"]:
        i = it["item"]
        v = {"judge": judge[i]["overall"], "grader_a": g["grader_a"][i]["overall"],
             "grader_b": g["grader_b"][i]["overall"]}
        if len(set(v.values())) > 1:
            dis.append({**it, **v, "judge_rationale": judge[i].get("rationale", "")[:300],
                        "grader_a_note": g["grader_a"][i].get("note", "")[:300],
                        "grader_b_note": g["grader_b"][i].get("note", "")[:300]})
    out = {"n": len(ids), "dimension": "overall", "sampling": f"seed 7, stratified {s['strata']}, from {list(RUNS)}",
           "graders": {"grader_a": "fresh Claude grader that saw only the rubric and transcripts",
                       "grader_b": "ChatGPT via codex exec, same packet", "judge": "claude-haiku-5-5, frozen rubric"},
           "pass_rates": {k: round(v.count("pass") / len(v), 3) for k, v in labels.items()},
           "pairs": {"judge_vs_grader_a": pairs["judge_vs_grader_a"], "judge_vs_grader_b": pairs["judge_vs_grader_b"],
                     "grader_a_vs_grader_b": pairs["grader_a_vs_grader_b"]},
           "per_dimension_agreement": per_dim, "disagreements": dis,
           "caveat": "n=10: kappa CIs are very wide; neither grader is a human support expert."}
    (ROOT / "results/judge_calibration.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out["pairs"]))


if __name__ == "__main__":
    if sys.argv[1] == "sample":
        sample(int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 10)
    else:
        score()
