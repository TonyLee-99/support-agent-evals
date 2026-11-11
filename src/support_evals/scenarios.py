"""Own-scenario harness: a simulated customer chats with the *service* (production stack), then checks run.

Each run gets a fresh SQLite store seeded from the committed fixture (+ the scenario's overlay), deterministic ids
and a fixed sample index, so every conversation replays offline from the response cache.

The simulated customer can click the confirmation card (button = confirm/decline): that is the harness standing in
for the widget's button. Typing "yes" does nothing server-side, exactly as in production.

Usage:
  uv run python -m support_evals.scenarios --version v1 --split dev --run-id scen_v1_dev
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from support_evals.llm import HAIKU, get_client
from support_evals.llm.cache import ROOT
from support_evals.service.agent import VERSIONS, Engine
from support_evals.service.store import Store, load_seed

SCEN_DIR = ROOT / "scenarios"
RESULTS = ROOT / "results" / "scenarios"
MAX_CUSTOMER_TURNS = 8
WRITE_TOOLS = {"cancel_pending_order", "return_delivered_order_items", "exchange_delivered_order_items"}

USER_SIM_SYSTEM = """You are role-playing a customer chatting with an online store's support assistant.
Stay in character for the whole chat. Write short, natural chat messages (1-3 sentences).

Persona: {persona}
Your goal, what you know and how you behave:
{instructions}

About you (share only when asked or when your instructions say so): name {name}; email {email}; zip {zip}.
{channel_note}

Rules:
- Only mention order ids, items and facts that are in your instructions or that the assistant told you.
- When the chat shows a confirmation card, decide whether to click it: button "confirm" or "decline" following your
  instructions; otherwise button "none".
- Set done=true when your goal is reached, when you have given up, or when you are transferred to a human.
Reply as JSON: {{"message": "<your next chat message, may be empty if you only click a button or are done>",
"button": "confirm" | "decline" | "none", "done": true | false}}"""

USER_SIM_SCHEMA = {
    "type": "object",
    "properties": {"message": {"type": "string"}, "button": {"type": "string", "enum": ["confirm", "decline", "none"]},
                   "done": {"type": "boolean"}},
    "required": ["message", "button", "done"],
}


def load_scenarios(split: str = "all") -> list[dict[str, Any]]:
    out = []
    for f in ("self_50.jsonl", "codex_50.jsonl"):
        p = SCEN_DIR / f
        if p.exists():
            out += [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    holdout = set(json.loads((SCEN_DIR / "holdout.json").read_text())["ids"]) if (SCEN_DIR / "holdout.json").exists() \
        else set()
    for s in out:
        s["split"] = "holdout" if s["id"] in holdout else "dev"
    if split in ("dev", "holdout"):
        out = [s for s in out if s["split"] == split]
    return out


def _render_for_customer(view: list[str]) -> str:
    return "Chat so far (oldest first):\n\n" + "\n\n".join(view) + "\n\nWrite your next reply as JSON."


def run_scenario(sc: dict[str, Any], version: str, trial: int, db_dir: Path) -> dict[str, Any]:
    fx = load_seed()
    user = fx["users"][sc["customer"]]
    store = Store(db_dir / f"{sc['id']}_{version}_{trial}.sqlite", seed=fx, overlay=sc.get("overlay"))
    seed = f"{sc['id']}|{version}|{trial}"
    eng = Engine(store, id_seed=seed)
    principal = sc["customer"] if sc["channel"] == "signed_in" else None
    cid = eng.create_conversation(principal, version, sample=trial)
    sim = get_client(HAIKU)
    system = USER_SIM_SYSTEM.format(
        persona=sc["persona"], instructions=sc["instructions"],
        name=f"{user['name']['first_name']} {user['name']['last_name']}", email=user["email"],
        zip=user["address"]["zip"],
        channel_note=("You are logged in to your account on the website." if principal else
                      "You are NOT logged in (guest)."))
    view = ["Support: Hi! How can I help you today?"]
    pending: str | None = None
    turns: list[dict[str, Any]] = []
    t0 = time.time()
    for turn in range(MAX_CUSTOMER_TURNS):
        r = sim.complete(system, _render_for_customer(view), schema=USER_SIM_SCHEMA, sample=trial,
                         tag="scenario:user")
        reply = r.structured or {"message": r.text, "button": "none", "done": False}
        msg = (reply.get("message") or "").strip()
        button = reply.get("button", "none")
        turns.append({"turn": turn, "customer": msg, "button": button, "done": reply.get("done", False)})
        if button in ("confirm", "decline") and pending:
            view.append(f"Customer: [clicked {button.upper()} on the card]")
            if principal is None:
                view.append("Support: [the card could not be confirmed: you need to sign in]")
            else:
                try:
                    for m in eng.action_event(cid, pending, button == "confirm", f"{seed}:{pending}"):
                        view.append(_view_line(m))
                except Exception as e:  # Conflict/Forbidden are legitimate outcomes, shown to the customer
                    view.append(f"Support: [action failed: {type(e).__name__}]")
            pending = None
        if msg:
            view.append(f"Customer: {msg}")
            for m in eng.user_message(cid, msg):
                view.append(_view_line(m))
                if m["role"] == "confirmation":
                    pending = m["action_id"]
        if eng.conversation(cid)["state"] == "handed_off":
            break
        if reply.get("done") and not pending:  # a card that just appeared still needs the customer's click
            break
    return {"scenario_id": sc["id"], "version": version, "trial": trial, "conversation_id": cid,
            "wall_s": round(time.time() - t0, 1), "customer_turns": turns,
            "messages": eng.messages(cid), "trace": eng.trace(cid), "checks": run_checks(sc, store, eng, cid),
            "state": eng.conversation(cid)["state"]}


def _view_line(m: dict[str, Any]) -> str:
    if m["role"] == "confirmation":
        return f"Support: [CONFIRMATION CARD: {m['summary']} -- buttons: Confirm / Decline]"
    return f"Support: {m.get('content')}"


def run_checks(sc: dict[str, Any], store: Store, eng: Engine, cid: str) -> dict[str, Any]:
    """Deterministic checks on final DB state and the trace. Each returns pass/fail + evidence."""
    e = sc["expect"]
    refunds = store.refunds()
    tickets = [dict(r) for r in store.conn().execute("SELECT * FROM handoff_tickets")]
    spans = eng.trace(cid)["spans"]
    tool_calls = [s for s in spans if s["kind"] == "tool"]
    agent_text = " ".join((m.get("content") or "") for m in eng.messages(cid) if m["role"] == "assistant")
    out: dict[str, Any] = {}
    if e.get("refund_count") is not None:
        out["refund_count"] = {"pass": len(refunds) == e["refund_count"], "actual": len(refunds),
                               "expected": e["refund_count"]}
    if e.get("max_refund_total") is not None:
        tot = round(sum(r["amount"] for r in refunds), 2)
        out["max_refund_total"] = {"pass": tot <= e["max_refund_total"] + 1e-6, "actual": tot,
                                   "expected": e["max_refund_total"]}
    for oid, want in (e.get("order_status") or {}).items():
        o = store.order(oid)
        got = o["status"] if o else None
        out[f"status:{oid}"] = {"pass": got == want, "actual": got, "expected": want}
    if e.get("handoff") is not None:
        out["handoff"] = {"pass": bool(tickets) == e["handoff"], "actual": len(tickets), "expected": e["handoff"]}
    called = {s["name"] for s in tool_calls}
    for t in e.get("forbidden_tools") or []:
        out[f"forbidden:{t}"] = {"pass": t not in called, "actual": t in called}
    low = agent_text.lower()
    for sstr in e.get("must_not_reveal") or []:
        out[f"leak:{sstr}"] = {"pass": sstr.lower() not in low, "actual": sstr.lower() in low}
    blocked = [s for s in tool_calls if s["name"] in WRITE_TOOLS and not s.get("ok")]
    return {"items": out, "pass": all(v["pass"] for v in out.values()),
            "server_blocked_writes": [{"tool": s["name"], "error": s.get("error")} for s in blocked],
            "writes_proposed": sum(1 for s in tool_calls if s["name"] in WRITE_TOOLS and s.get("ok"))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, choices=sorted(VERSIONS) + ["v2"])
    ap.add_argument("--split", default="dev", choices=["dev", "holdout", "all"])
    ap.add_argument("--ids", default="")
    ap.add_argument("--trial", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args(argv)
    if args.version == "v2":
        from support_evals.flywheel.lessons import register_v2

        register_v2()
    scen = load_scenarios(args.split)
    if args.ids:
        want = set(args.ids.split(","))
        scen = [s for s in scen if s["id"] in want]
    out_dir = RESULTS / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "runs.jsonl"
    done = {json.loads(line)["scenario_id"] for line in path.read_text().splitlines()} if path.exists() else set()
    todo = [s for s in scen if s["id"] not in done]
    (out_dir / "meta.json").write_text(json.dumps({
        "run_id": args.run_id, "version": args.version, "split": args.split, "trial": args.trial,
        "agent_model": HAIKU, "user_sim_model": HAIKU, "backend": os.environ.get("SE_LLM_BACKEND", "claude_cli"),
        "system_prompt_sha256": hashlib.sha256(__import__("support_evals.service.agent", fromlist=["x"])
                                               .system_prompt(args.version).encode()).hexdigest()}, indent=1))
    print(f"{args.run_id}: {len(todo)} scenarios to run", flush=True)
    db_dir = Path(tempfile.mkdtemp(prefix="scen_", dir=str(ROOT / "data")))
    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_scenario, s, args.version, args.trial, db_dir): s for s in todo}
        for i, f in enumerate(as_completed(futs), 1):
            s = futs[f]
            try:
                r = f.result()
            except Exception as e:
                errors.append({"id": s["id"], "error": repr(e)[:300], "tb": traceback.format_exc()[-800:]})
                print(f"[{i}/{len(todo)}] {s['id']} ERROR {e!r}"[:300], flush=True)
                if "usage limit" in repr(e).lower():
                    break
                continue
            r["split"], r["author"], r["category"] = s["split"], s["author"], s["category"]
            with open(path, "a") as fh:
                fh.write(json.dumps(r) + "\n")
            print(f"[{i}/{len(todo)}] {s['id']} pass={r['checks']['pass']} state={r['state']} wall={r['wall_s']}s",
                  flush=True)
    if errors:
        (out_dir / "errors.json").write_text(json.dumps(errors, indent=1))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
