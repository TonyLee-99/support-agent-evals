import json

import httpx
import pytest

from support_evals.llm.anthropic_api import AnthropicClient
from support_evals.llm.base import ReplayMiss
from support_evals.llm.cache import DiskCache, cache_key
from support_evals.llm.claude_cli import ClaudeCLIClient, UsageLimitError
from support_evals.llm.replay import ReplayClient

SCHEMA = {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}


def test_cache_roundtrip_and_first_write_wins(tmp_path):
    c = DiskCache(write_dir=tmp_path, read_dirs=[])
    k = cache_key(model="m", system="s", prompt="p", schema=None, sample=0)
    c.put(k, {"text": "a", "model": "m"})
    c.put(k, {"text": "b", "model": "m"})
    assert DiskCache(write_dir=tmp_path, read_dirs=[]).get(k)["text"] == "a"


def test_sample_changes_key():
    a = cache_key(model="m", system="s", prompt="p", schema=None, sample=0)
    b = cache_key(model="m", system="s", prompt="p", schema=None, sample=1)
    assert a != b


def test_claude_cli_isolated_command_and_cache(fake_claude, tmp_path):
    cli = ClaudeCLIClient("claude-haiku-5-5", cache=DiskCache(write_dir=tmp_path / "c", read_dirs=[]))
    r1 = cli.complete("SYS", "PROMPT", schema=SCHEMA, tag="t")
    r2 = cli.complete("SYS", "PROMPT", schema=SCHEMA, tag="t")
    assert r1.structured == {"message": "hello from fake"} and not r1.cached and r2.cached
    calls = [json.loads(line) for line in fake_claude.read_text().splitlines()]
    assert len(calls) == 1  # second call served from cache
    args = calls[0]["args"]
    for flag in ("--tools", "--strict-mcp-config", "--setting-sources", "--no-session-persistence",
                 "--disable-slash-commands", "--system-prompt"):
        assert flag in args
    assert args[args.index("--tools") + 1] == "" and args[args.index("--setting-sources") + 1] == ""
    assert calls[0]["prompt"] == "PROMPT"  # prompt goes via stdin, not argv
    assert calls[0]["cwd"].endswith(".llm_sandbox")


def test_claude_cli_usage_limit_raises(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    cli = ClaudeCLIClient("claude-haiku-5-5", cache=DiskCache(write_dir=tmp_path / "c", read_dirs=[]))
    with pytest.raises(UsageLimitError):
        cli.complete("S", "P")


def test_replay_serves_recorded_and_misses_loudly(tmp_path):
    cache = DiskCache(write_dir=tmp_path, read_dirs=[])
    k = cache_key(model="m", system="s", prompt="p", schema=None, sample=0)
    cache.put(k, {"text": "recorded", "model": "m", "cost_usd": 0.5})
    rc = ReplayClient("m", cache)
    assert rc.complete("s", "p").text == "recorded"
    with pytest.raises(ReplayMiss):
        rc.complete("s", "other")


def _api(tmp_path, handler, **kw):
    return AnthropicClient("claude-haiku-5-5", cache=DiskCache(write_dir=tmp_path / "c", read_dirs=[]),
                           transport=httpx.MockTransport(handler), ledger_path=tmp_path / "ledger.jsonl", **kw)


def _ok(req: httpx.Request):
    return httpx.Response(200, json={"content": [{"type": "tool_use", "name": "respond", "input": {"message": "hi"}}],
                                     "usage": {"input_tokens": 1000, "output_tokens": 200,
                                               "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 0}})


def test_anthropic_backend_shape_caching_and_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["key"] = req.headers["x-api-key"]
        return _ok(req)

    c = _api(tmp_path, handler)
    r = c.complete("S", "P", schema=SCHEMA)
    assert r.structured == {"message": "hi"} and seen["key"] == "test-key"
    assert seen["body"]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert seen["body"]["tool_choice"] == {"type": "auto"} and seen["body"]["model"] == "claude-haiku-5-5"
    rec = json.loads((tmp_path / "ledger.jsonl").read_text())
    # 1000*0.10 + 200*0.50 + 5000*0.01 per million
    assert abs(rec["usd"] - 0.00025) < 1e-9 and rec["project"] == "support-agent-evals"
    assert "test-key" not in (tmp_path / "ledger.jsonl").read_text()
    assert all("test-key" not in f.read_text() for f in (tmp_path / "c").glob("*.jsonl"))


def test_anthropic_budget_cap_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    (tmp_path / "ledger.jsonl").write_text(json.dumps({"project": "other", "usd": 3.50}) + "\n")

    class Fallback:
        backend, model = "claude_cli", "claude-haiku-5-5"

        def complete(self, system, prompt, **kw):
            from support_evals.llm.base import LLMResponse
            return LLMResponse(text="cli", model=self.model)

    def handler(req):
        raise AssertionError("API must not be called over budget")

    r = _api(tmp_path, handler, fallback=Fallback()).complete("S", "P")
    assert r.text == "cli" and r.meta["fallback_from"] == "anthropic"
    from support_evals.llm.budget import BudgetExceeded
    with pytest.raises(BudgetExceeded):
        _api(tmp_path, handler).complete("S", "P2")


def test_api_model_restricted_to_haiku():
    with pytest.raises(ValueError):
        AnthropicClient("claude-sonnet-5-5")
