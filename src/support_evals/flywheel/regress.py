"""Steps 4-5: failures -> regression cases -> gate.

A case = (conversation prefix up to the first bad step, a checkable property of the agent's next step).
Running a case is ONE agent step, so a suite of dozens costs a few cents and replays offline.

  generate:  uv run python -m support_evals.flywheel.regress generate     (from results/flywheel/labels.jsonl)
  run:       uv run python -m support_evals.flywheel.regress run --version v2
  gate:      uv run python -m support_evals.flywheel.regress gate --version v2   (exit 1 = gate fails)

Cases start with approved=false; a person reviews the property and flips it (cases.jsonl is the reviewed file).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any

from support_evals.llm.cache import ROOT

DIR = ROOT / "results" / "regression"
CASES = DIR / "cases.jsonl"
GATE = {"min_pass_rate": 0.8, "max_new_failures": 0}


def generate() -> int:
    labels = [json.loads(line) for line in (ROOT / "results/flywheel/labels.jsonl").read_text().splitlines()]
    existing = {c["source"]: c for c in load_cases()} if CASES.exists() else {}
    out = []
    for lab in labels:
        lb = lab["label"]
        if lb.get("category") in ("user_sim_error", "harness_error") or "first_bad_index" not in lb:
            continue  # benchmark noise is tracked, not turned into agent regressions
        i = int(lb["first_bad_index"])
        msgs = lab["messages"]
        if not (0 < i <= len(msgs)) or msgs[i - 1]["role"] == "assistant" and not msgs[i - 1].get("tool_calls"):
            pass
        if lab["source"] in existing:
            out.append(existing[lab["source"]])
            continue
        out.append({"id": "R" + hashlib.sha256(lab["source"].encode()).hexdigest()[:6], "source": lab["source"],
                    "target": lab["target"], "agent_at_failure": lab["agent"], "category": lb["category"],
                    "root_cause": lb.get("root_cause", ""), "prefix": msgs[:i], "property": lb["regression"],
                    "trial": _trial(lab["source"]), "approved": False, "review_note": ""})
    DIR.mkdir(parents=True, exist_ok=True)
    with open(CASES, "w") as fh:
        for c in out:
            fh.write(json.dumps(c) + "\n")
    print(f"{len(out)} cases -> {CASES}")
    return 0


def _trial(source: str) -> Any:
    parts = source.split(":")
    return int(parts[-1]) if parts[0] == "tau2" else 0


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]


def check(prop: dict[str, Any], content: str | None, tool: str | None) -> bool:
    t = prop.get("type")
    if t == "must_not_call":
        return tool not in set(prop.get("tools") or [])
    if t == "must_call":
        return tool in set(prop.get("tools") or [])
    if t == "must_message":
        return tool is None
    if t == "must_not_mention":
        low = (content or "").lower()
        return not any(p.lower() in low for p in prop.get("phrases") or [])
    raise ValueError(t)


def step_service(case: dict[str, Any], version: str) -> tuple[str | None, str | None]:
    from support_evals.service.agent import agent_step

    msgs = [m for m in case["prefix"]]
    sample = case.get("sample")
    if sample is None:
        sample = 0
    turn = agent_step(version, msgs, sample=sample)
    return turn.content, (turn.tool_call or {}).get("name")


def step_tau2(case: dict[str, Any], agent_name: str) -> tuple[str | None, str | None]:
    from pydantic import TypeAdapter
    from tau2.data_model.message import Message
    from tau2.registry import registry
    from tau2.runner.build import build_environment

    from support_evals.bench.run import setup

    setup("claude-haiku-5-5")
    env = build_environment("retail")
    agent = registry.get_agent_factory(agent_name)(
        tools=env.get_tools(), domain_policy=env.get_policy(), llm="claudecli/claude-haiku-5-5",
        llm_args={"metadata": {"trial": case["trial"], "role": "agent"}})
    ta = TypeAdapter(Message)
    msgs = [ta.validate_python(m) for m in case["prefix"]]
    state = agent.get_init_state(message_history=msgs[:-1])
    out, _ = agent.generate_next_message(msgs[-1], state)
    return out.content, (out.tool_calls[0].name if out.tool_calls else None)


def fingerprint(version: str) -> str:
    from support_evals.service.agent import system_prompt

    return hashlib.sha256(system_prompt(version).encode()).hexdigest()[:16]


def run(version: str, tau2_agent: str = "policy_agent", only_approved: bool = True) -> dict[str, Any]:
    if version == "v2":
        from support_evals.flywheel.lessons import register_v2

        register_v2()
    if version == "broken":
        from support_evals.flywheel.lessons import register_broken

        register_broken()
    results = []
    for c in load_cases():
        if only_approved and not c["approved"]:
            continue
        try:
            if c["target"] == "service":
                content, tool = step_service(c, version)
            else:
                content, tool = step_tau2(c, tau2_agent)
            ok = check(c["property"], content, tool)
            results.append({"id": c["id"], "target": c["target"], "category": c["category"], "pass": ok,
                            "tool": tool, "content": (content or "")[:200]})
        except Exception as e:  # a replay miss or adapter failure is a failed case, never silently skipped
            results.append({"id": c["id"], "target": c["target"], "category": c["category"], "pass": False,
                            "error": repr(e)[:200]})
    svc = [r for r in results if r["target"] == "service"]
    rep = {"version": version, "fingerprint": fingerprint(version), "tau2_agent": tau2_agent,
           "n": len(results), "service_n": len(svc),
           "service_pass_rate": round(sum(r["pass"] for r in svc) / len(svc), 3) if svc else None,
           "tau2_pass_rate": round(sum(r["pass"] for r in results if r["target"] == "tau2") /
                                   max(1, sum(1 for r in results if r["target"] == "tau2")), 3),
           "results": results}
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"report_{version}.json").write_text(json.dumps(rep, indent=1))
    return rep


def gate(version: str, baseline_report: str = "report_v1.json") -> int:
    rep = json.loads((DIR / f"report_{version}.json").read_text())
    if rep["fingerprint"] != fingerprint(version):
        print(f"GATE FAIL: report for {version} is stale (prompt changed); re-run `regress run --version {version}`")
        return 1
    rate = rep["service_pass_rate"] or 0.0
    prev = {}
    if (DIR / baseline_report).exists() and baseline_report != f"report_{version}.json":
        prev = {r["id"]: r["pass"] for r in json.loads((DIR / baseline_report).read_text())["results"]}
    new_fail = [r["id"] for r in rep["results"] if r["target"] == "service" and not r["pass"] and prev.get(r["id"])]
    ok = rate >= GATE["min_pass_rate"] and len(new_fail) <= GATE["max_new_failures"]
    print(f"GATE {'PASS' if ok else 'FAIL'}: {version} service pass rate {rate:.2f} (min {GATE['min_pass_rate']}), "
          f"new failures vs {baseline_report}: {new_fail}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["generate", "run", "gate"])
    ap.add_argument("--version", default="v2")
    a = ap.parse_args(argv)
    if a.cmd == "generate":
        return generate()
    if a.cmd == "run":
        rep = run(a.version)
        print(json.dumps({k: rep[k] for k in ("version", "n", "service_pass_rate", "tau2_pass_rate")}))
        return 0
    return gate(a.version)


if __name__ == "__main__":
    sys.exit(main())
