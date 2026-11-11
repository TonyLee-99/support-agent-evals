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


def test_anthropic_backend_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        seen["key"] = req.headers["x-api-key"]
        return httpx.Response(200, json={"content": [{"type": "tool_use", "name": "respond", "input": {"message": "hi"}}],
                                         "usage": {"input_tokens": 3, "output_tokens": 2}})

    c = AnthropicClient("claude-haiku-5-5", cache=DiskCache(write_dir=tmp_path, read_dirs=[]),
                        transport=httpx.MockTransport(handler))
    r = c.complete("S", "P", schema=SCHEMA)
    assert r.structured == {"message": "hi"}
    assert seen["key"] == "test-key" and seen["body"]["tool_choice"] == {"type": "tool", "name": "respond"}
    assert seen["body"]["system"] == "S"
