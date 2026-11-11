"""A recorded tau2 simulation replays offline, byte-for-byte, through the unmodified tau2 orchestrator."""

import gzip
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "results/tau2/llm_agent_test10_api"


@pytest.mark.parametrize("task_id", ["51"])
def test_recorded_simulation_replays(task_id, tmp_path, monkeypatch):
    monkeypatch.setenv("SE_LLM_BACKEND", "replay")
    if not __import__("os").environ.get("SE_CACHE_TRACE"):  # export mode reads the live cache
        monkeypatch.setenv("SE_CACHE_DIR", str(tmp_path))  # only committed cache/ci
    from support_evals import llm

    llm._clients.clear()
    with gzip.open(RUN / "sims.jsonl.gz", "rt") as fh:
        rec = next(s for s in map(json.loads, fh) if s["task_id"] == task_id and s["se_trial"] == 0)
    from types import SimpleNamespace

    from tau2.runner.helpers import get_tasks

    from support_evals.bench.run import run_one, setup

    setup("claude-haiku-5-5")
    task = get_tasks("retail", task_ids=[task_id])[0]
    args = SimpleNamespace(agent="llm_agent", model="claude-haiku-5-5", user_model="claude-haiku-5-5", max_steps=100,
                           run_id="replay")
    d = run_one(task, 0, args)
    assert d["reward_info"]["reward"] == rec["reward_info"]["reward"]
    assert [m.get("content") for m in d["messages"]] == [m.get("content") for m in rec["messages"]]
