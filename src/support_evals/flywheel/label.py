"""Step 1-2 of the flywheel: collect failed DEV conversations and label them against a fixed codebook.

Sources (dev only - test/holdout transcripts never feed the flywheel):
- tau2 dev runs: simulations with reward < 1, plus the evaluator's diff (expected vs actual actions, DB, NL checks)
- own scenarios: runs whose deterministic checks or judge failed, restricted to the dev split

Usage: uv run python -m support_evals.flywheel.label --tau2-runs llm_agent_dev10,policy_agent_dev10 \
           --scenario-runs scen_baseline_20,scen_v1_20
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from support_evals.bench.metrics import load_sims, reward
from support_evals.judge.judge import render_transcript
from support_evals.llm import HAIKU, get_client
from support_evals.llm.cache import ROOT

OUT = ROOT / "results" / "flywheel"

# Codebook: drafted from the pilot/smoke failures read by hand (INCIDENTS.md), then frozen before labelling.
CODEBOOK = {
    "auth_skipped": "acted or looked up account data before verifying identity",
    "wrong_args": "called the right tool with a wrong order/item/variant/payment id or value",
    "incomplete_action": "did only part of the request (missed an item, an order or a second request)",
    "extra_action": "performed a write or step the customer did not ask for",
    "no_confirmation": "made a write without explicit customer confirmation of the exact details",
    "policy_violation": "acted against policy (status rules, refund method, another customer's data, limits)",
    "false_refusal": "refused, stalled or transferred a request that policy allowed",
    "hallucinated_fact": "stated a fact, tool failure or procedure not supported by tool results",
    "wrong_info": "told the customer incorrect information (counts, prices, statuses)",
    "premature_end": "ended or transferred before the conversation was resolved",
    "user_sim_error": "the simulated customer deviated from its instructions (benchmark noise, not the agent)",
    "harness_error": "the evaluation harness itself caused the failure",
}
PROPERTY_TYPES = ("must_not_call", "must_call", "must_message", "must_not_mention")

SYSTEM = f"""You label failed customer-support conversations for an error analysis.
Codebook (choose exactly one category):
{json.dumps(CODEBOOK, indent=1)}

Also give: first_bad_index = the index (the [i] number) of the FIRST agent message or tool call that went wrong;
root_cause = one or two plain sentences, generic enough to describe the failure mode (no customer names);
regression = a checkable property of the agent's step AT first_bad_index that a correct agent would satisfy:
  type must_not_call (tools = names the agent must not call there), must_call (tools = names, any of which is
  correct there), must_message (agent should reply in text instead of calling a tool), or must_not_mention
  (phrases = short strings that must not appear in the agent's message).
Reply as JSON."""

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(CODEBOOK)},
        "first_bad_index": {"type": "integer"},
        "root_cause": {"type": "string"},
        "regression": {"type": "object", "properties": {
            "type": {"type": "string", "enum": list(PROPERTY_TYPES)},
            "tools": {"type": "array", "items": {"type": "string"}},
            "phrases": {"type": "array", "items": {"type": "string"}}}, "required": ["type"]},
    },
    "required": ["category", "first_bad_index", "root_cause", "regression"],
}


def tau2_transcript(sim: dict[str, Any]) -> str:
    out = []
    for i, m in enumerate(sim["messages"]):
        if m.get("tool_calls"):
            calls = "; ".join(f"{t['name']}({json.dumps(t['arguments'])})" for t in m["tool_calls"])
            out.append(f"[{i}] AGENT TOOL CALL: {calls}")
        elif m["role"] == "tool":
            out.append(f"[{i}] TOOL RESULT: {str(m.get('content'))[:300]}")
        else:
            out.append(f"[{i}] {'AGENT' if m['role'] == 'assistant' else 'CUSTOMER'}: {m.get('content')}")
    return "\n".join(out)


def tau2_diff(sim: dict[str, Any]) -> str:
    ri = sim.get("reward_info") or {}
    missing = [f"{a['action']['name']}({json.dumps(a['action']['arguments'])})" for a in ri.get("action_checks") or []
               if not a.get("action_match")]
    nl = [f"{c.get('nl_assertion')}: met={c.get('met')}" for c in ri.get("nl_assertions") or []]
    return json.dumps({"db_match": (ri.get("db_check") or {}).get("db_match"), "expected_actions_not_matched": missing,
                       "nl_assertions": nl})


def scenario_transcript(run: dict[str, Any]) -> str:
    # number the messages the same way the regression case will slice them
    lines = []
    for i, m in enumerate(run["messages"]):
        r = render_transcript({"messages": [m], "customer_turns": []})
        if r:
            lines.append(f"[{i}] {r}")
    return "\n".join(lines)


def collect(tau2_runs: list[str], scenario_runs: list[str]) -> list[dict[str, Any]]:
    items = []
    for run in tau2_runs:
        meta = json.loads((ROOT / "results/tau2" / run / "meta.json").read_text())
        if meta["split"] != "dev":
            raise SystemExit(f"{run} is not a dev run; the flywheel only consumes dev")
        for s in load_sims(ROOT / "results/tau2" / run):
            if reward(s) < 1:
                items.append({"source": f"tau2:{run}:{s['task_id']}:{s['se_trial']}", "target": "tau2", "run": run,
                              "agent": meta["agent"], "transcript": tau2_transcript(s), "diff": tau2_diff(s),
                              "messages": s["messages"]})
    for run in scenario_runs:
        p = ROOT / "results/scenarios" / run / "runs.jsonl"
        judged = ROOT / "results/scenarios" / run / "judged.jsonl"
        jmap = {}
        if judged.exists():
            jmap = {j["scenario_id"]: j for j in map(json.loads, judged.read_text().splitlines())}
        for r in map(json.loads, p.read_text().splitlines()):
            if r["split"] != "dev":
                continue
            j = jmap.get(r["scenario_id"], {}).get("judge", {})
            if r["checks"]["pass"] and j.get("overall", "pass") == "pass":
                continue
            failed = {k: v for k, v in r["checks"]["items"].items() if not v["pass"]}
            items.append({"source": f"scenario:{run}:{r['scenario_id']}", "target": "service", "run": run,
                          "agent": r["version"], "transcript": scenario_transcript(r),
                          "diff": json.dumps({"failed_checks": failed, "judge": j}), "messages": r["messages"]})
    return items


def label(item: dict[str, Any]) -> dict[str, Any]:
    prompt = f"TRANSCRIPT\n{item['transcript']}\n\nEVALUATOR FINDINGS\n{item['diff']}\n\nLabel this failure."
    r = get_client(HAIKU).complete(SYSTEM, prompt, schema=SCHEMA, tag="flywheel:label")
    lab = r.structured or {}
    return {k: item[k] for k in ("source", "target", "run", "agent")} | {"label": lab}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tau2-runs", default="")
    ap.add_argument("--scenario-runs", default="")
    a = ap.parse_args(argv)
    items = collect([x for x in a.tau2_runs.split(",") if x], [x for x in a.scenario_runs.split(",") if x])
    OUT.mkdir(parents=True, exist_ok=True)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(4) as ex:
        labels = list(ex.map(label, items))
    for it, lab in zip(items, labels, strict=True):
        lab["messages"] = it["messages"]  # kept for regression-case generation
    with open(OUT / "labels.jsonl", "w") as fh:
        for lab in labels:
            fh.write(json.dumps(lab) + "\n")
    print(f"labelled {len(labels)} failures -> {OUT / 'labels.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def codebook_path() -> Path:
    return OUT / "codebook.json"
