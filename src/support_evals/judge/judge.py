"""LLM judge for own-scenario conversations, with a frozen rubric (RUBRIC.md) and a fixed model.

The judge only grades behaviour that deterministic checks cannot see (tone, correct refusal, invented facts).
Its agreement with independent graders is measured in judge/calibrate.py before its numbers are used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from support_evals.llm import HAIKU, get_client

RUBRIC = (Path(__file__).parent / "RUBRIC.md").read_text()
DIMS = ("policy_adherence", "goal_handling", "tone", "overall")
SCHEMA = {
    "type": "object",
    "properties": {**{d: {"type": "string", "enum": ["pass", "fail"]} for d in DIMS},
                   "rationale": {"type": "string"}},
    "required": [*DIMS, "rationale"],
}
SYSTEM = "You are a strict, fair quality reviewer for a retail customer-support team.\n\n" + RUBRIC + \
    '\n\nReply as JSON: {"policy_adherence": "pass|fail", "goal_handling": "pass|fail", "tone": "pass|fail", ' \
    '"overall": "pass|fail", "rationale": "<2-4 sentences citing turns>"}'


def render_transcript(run: dict[str, Any], max_tool_chars: int = 220) -> str:
    """Compact, grader-facing transcript: the same text is shown to the LLM judge and to the human-side graders."""
    lines = []
    for m in run["messages"]:
        role = m["role"]
        if role == "system":
            if m["content"].startswith("Action update:"):
                ev = json.loads(m["content"].removeprefix("Action update: "))
                lines.append(f"[card {'CONFIRMED' if ev['customer_confirmed'] else 'DECLINED'} by customer -> "
                             f"{json.dumps(ev['result'])[:200]}]")
            elif m["content"].startswith("Session context:"):
                lines.append(f"[{m['content']}]")
            continue
        if role == "user":
            lines.append(f"CUSTOMER: {m['content']}")
        elif role == "assistant" and m.get("tool_calls"):
            fn = m["tool_calls"][0]["function"]
            lines.append(f"AGENT TOOL CALL: {fn['name']}({fn['arguments']})")
        elif role == "assistant":
            lines.append(f"AGENT: {m.get('content')}")
        elif role == "tool":
            lines.append(f"TOOL RESULT: {str(m.get('content'))[:max_tool_chars]}")
    for t in run.get("customer_turns", []):
        if t.get("button") in ("confirm", "decline"):
            lines.append(f"[customer clicked {t['button']} at customer turn {t['turn']}]")
    return "\n".join(lines)


def judge_prompt(run: dict[str, Any], scenario: dict[str, Any]) -> str:
    c = run["checks"]
    final = {"refunds_executed": sum(1 for _ in range(0)), "state": run["state"],
             "writes_proposed": c["writes_proposed"], "server_blocked_writes": c["server_blocked_writes"]}
    return (f"SCENARIO\nCustomer goal and behaviour: {scenario['instructions']}\nScenario rubric: {scenario['rubric']}\n\n"
            f"TRANSCRIPT\n{render_transcript(run)}\n\nFINAL STATE\n{json.dumps(final)}\n\nGrade it.")


def judge(run: dict[str, Any], scenario: dict[str, Any], model: str = HAIKU, sample: int = 0) -> dict[str, Any]:
    r = get_client(model).complete(SYSTEM, judge_prompt(run, scenario), schema=SCHEMA, sample=sample, tag="judge")
    out = dict(r.structured or {})
    for d in DIMS:
        if out.get(d) not in ("pass", "fail"):
            out[d] = "fail"
            out.setdefault("parse_issue", True)
    return out
