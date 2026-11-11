import os
from pathlib import Path

import pytest

FAKES = Path(__file__).parent / "fakes"


@pytest.fixture
def fake_claude(monkeypatch, tmp_path):
    """Put the fake `claude` first on PATH and isolate the cache in tmp."""
    monkeypatch.setenv("PATH", f"{FAKES}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SE_CACHE_DIR", str(tmp_path / "cache"))
    import support_evals.llm.claude_cli as cc

    monkeypatch.setattr(cc, "LOCK_DIR", tmp_path / "locks")  # do not compete with live runs for slots
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    return log


@pytest.fixture(autouse=True)
def _offline_default(monkeypatch, tmp_path):
    monkeypatch.setenv("SE_LLM_BACKEND", os.environ.get("SE_LLM_BACKEND", "replay"))
    from support_evals import llm

    llm._clients.clear()
    yield
    llm._clients.clear()
