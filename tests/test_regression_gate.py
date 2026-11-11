"""The CI regression gate: passes for the shipped version, fails for a known-bad one, and replays offline.

Replay consistency (CI evidence) is distinct from live evidence: CI re-runs the recorded steps from cache/ci and
checks the outcomes match the committed report; it cannot detect new live behaviour (that needs `make regress`).
"""

import json
from pathlib import Path

import pytest

from support_evals.flywheel import regress

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "results" / "regression"


def test_cases_are_reviewed_and_well_formed():
    cases = regress.load_cases()
    assert cases, "no regression cases"
    approved = [c for c in cases if c["approved"]]
    assert approved and all(c["review_note"] for c in approved)
    for c in cases:
        assert c["property"]["type"] in ("must_not_call", "must_call", "must_message", "must_not_mention")
        assert c["source"].split(":")[1].endswith(("dev10", "_20")), "cases must come from dev runs"


def test_gate_passes_for_shipped_version_and_fails_for_broken():
    assert regress.gate("v2") == 0
    assert regress.gate("broken") == 1


def test_gate_fails_on_stale_report(monkeypatch):
    monkeypatch.setattr(regress, "fingerprint", lambda v: "different-prompt")
    assert regress.gate("v2") == 1


@pytest.mark.parametrize("prop,content,tool,ok", [
    ({"type": "must_not_call", "tools": ["cancel_pending_order"]}, None, "cancel_pending_order", False),
    ({"type": "must_call", "tools": ["get_order_details"]}, None, "get_order_details", True),
    ({"type": "must_message"}, "hi", None, True),
    ({"type": "must_not_mention", "phrases": ["unable to access"]}, "I'm unable to access it", None, False),
])
def test_property_checks(prop, content, tool, ok):
    assert regress.check(prop, content, tool) is ok


def test_replay_matches_committed_report(monkeypatch, tmp_path):
    """Offline: every approved service case replays from cache/ci to the recorded outcome."""
    monkeypatch.setenv("SE_LLM_BACKEND", "replay")
    monkeypatch.setenv("SE_CACHE_DIR", str(tmp_path))  # only the committed cache/ci is visible, as in CI
    from support_evals import llm

    llm._clients.clear()
    recorded = {r["id"]: r["pass"] for r in json.loads((DIR / "report_v2.json").read_text())["results"]
                if r["target"] == "service"}
    monkeypatch.setattr(regress, "DIR", tmp_path)  # do not overwrite the committed report
    rep = regress.run("v2")
    fresh = {r["id"]: r["pass"] for r in rep["results"] if r["target"] == "service"}
    assert fresh == recorded
