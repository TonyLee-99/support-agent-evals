"""Build the static dashboard (docs/site/index.html) from files in results/ only. No server, no CDN."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from support_evals.llm.cache import ROOT

R = ROOT / "results"
OUT = ROOT / "docs" / "site"


def _load(p: Path) -> Any:
    return json.loads(p.read_text()) if p.exists() else None


def _replays() -> list[dict[str, Any]]:
    """A handful of real conversations: tau2 test sims (both agents, same task) and scenario runs."""
    out = []
    for run, label in (("llm_agent_test10_api", "baseline"), ("policy_agent_test10_api", "policy_agent")):
        p = R / "tau2" / run / "sims.jsonl.gz"
        if not p.exists():
            continue
        with gzip.open(p, "rt") as fh:
            sims = [json.loads(line) for line in fh]
        for s in sims:
            if s["se_trial"] != 0:
                continue
            msgs = []
            for m in s["messages"]:
                if m.get("tool_calls"):
                    msgs.append({"r": "tool", "t": "; ".join(f"{t['name']}({json.dumps(t['arguments'])})"
                                                          for t in m["tool_calls"])})
                elif m["role"] == "tool":
                    msgs.append({"r": "result", "t": str(m.get("content"))[:240]})
                else:
                    msgs.append({"r": "agent" if m["role"] == "assistant" else "customer", "t": m.get("content") or ""})
            out.append({"id": f"tau2 task {s['task_id']} - {label}", "kind": "tau2",
                        "outcome": "pass" if (s.get("reward_info") or {}).get("reward") == 1 else "fail", "msgs": msgs})
    for run in ("scen_baseline_20", "scen_v1_20", "scen_v2_20"):
        p = R / "scenarios" / run / "runs.jsonl"
        if not p.exists():
            continue
        for r in map(json.loads, p.read_text().splitlines()):
            msgs = []
            for m in r["messages"]:
                if m.get("tool_calls"):
                    fn = m["tool_calls"][0]["function"]
                    msgs.append({"r": "tool", "t": f"{fn['name']}({fn['arguments']})"})
                elif m["role"] == "tool":
                    msgs.append({"r": "result", "t": str(m.get("content"))[:240]})
                elif m["role"] == "system" and m["content"].startswith("Action update"):
                    msgs.append({"r": "event", "t": m["content"][:240]})
                elif m["role"] in ("user", "assistant") and m.get("content"):
                    msgs.append({"r": "agent" if m["role"] == "assistant" else "customer", "t": m["content"]})
            out.append({"id": f"{r['scenario_id']} ({r['category']}, {r['author']}) - service {r['version']}",
                        "kind": "scenario", "outcome": "pass" if r["checks"]["pass"] else "fail", "msgs": msgs,
                        "trace": r["trace"]["totals"]})
    return out


def build() -> Path:
    data = {
        "tau2": _load(R / "tau2_test_summary.json"),
        "tau2_dev": _load(R / "tau2_dev_summary.json"),
        "ablations": _load(R / "ablations.json"),
        "scenarios": _load(R / "scenarios_summary.json"),
        "taxonomy": _load(R / "flywheel/taxonomy.json"),
        "regression": {v: _load(R / "regression" / f"report_{v}.json") for v in ("v1", "v2", "broken")},
        "judge": _load(R / "judge_calibration.json"),
        "ledger": _load(R / "ledger_summary.json"),
        "replays": _replays(),
    }
    for v in data["regression"].values():
        if v:
            v.pop("results", None)
    OUT.mkdir(parents=True, exist_ok=True)
    html = (Path(__file__).parent / "template.html").read_text().replace("__DATA__", json.dumps(data))
    (OUT / "index.html").write_text(html)
    print(f"wrote {OUT / 'index.html'} ({len(html) // 1024} KB)")
    return OUT / "index.html"


if __name__ == "__main__":
    build()
