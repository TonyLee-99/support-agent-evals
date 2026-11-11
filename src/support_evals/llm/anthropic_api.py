"""Anthropic Messages API backend (Claude Haiku 5.5 only), with prompt caching and a hard shared budget.

- Key: read from ANTHROPIC_API_KEY, which the runner fills at process start from the macOS keychain. Never
  written to disk, logs, cache keys or traces.
- Structured output: the schema is the input_schema of one tool ("respond"); the system prompt tells the model to
  always answer through it (tool_choice stays "auto" because forced tool choice is not supported on every current
  model). Our schemas contain free-form objects (tool arguments), which strict JSON-schema output does not allow.
- Caching: the system prompt (policy + tool schemas + interface) is the stable prefix and gets a cache_control
  breakpoint; transcripts change every turn and follow it.
- Budget: llm/budget.py is checked before every call; over the cap -> BudgetExceeded and, if a fallback client is
  configured, the call goes to claude-cli instead (recorded in the response meta).
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import Any

import httpx

from support_evals.llm import budget
from support_evals.llm.base import LLMClient, LLMError, LLMResponse, ReplayMiss
from support_evals.llm.cache import DiskCache, cache_key

API_URL = "https://api.anthropic.com/v1/messages"
RESPOND_HINT = ("\n\nAlways answer by calling the `respond` tool exactly once with your reply as its input. "
                "Do not write anything outside the tool call.")


class AnthropicClient:
    backend = "anthropic"

    def __init__(self, model: str = "claude-haiku-5-5", cache: DiskCache | None = None,
                 transport: httpx.BaseTransport | None = None, max_tokens: int = 8000, strict_replay: bool = False,
                 fallback: LLMClient | None = None, ledger_path=None, cap: float | None = None):
        if model not in budget.PRICES:
            raise ValueError(f"API backend is limited to {sorted(budget.PRICES)}; got {model}")
        self.model = model
        self.cache = cache or DiskCache()
        self.max_tokens = max_tokens
        self.strict_replay = strict_replay
        self.fallback = fallback
        self.ledger_path = ledger_path
        self.cap = cap
        self.transport = transport
        self.live_calls = 0

    def _http(self) -> httpx.Client:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY not set")
        return httpx.Client(transport=self.transport, timeout=180.0, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})

    def complete(self, system: str, prompt: str, *, schema: dict[str, Any] | None = None, sample: Any = 0,
                 tag: str = "") -> LLMResponse:
        key = cache_key(model=self.model, system=system, prompt=prompt, schema=schema, sample=sample,
                        backend=self.backend)
        hit = self.cache.get(key)
        if hit is not None:
            return LLMResponse.from_dict(hit, cached=True)
        if self.strict_replay:
            raise ReplayMiss(f"{self.model}: key {key[:12]} not cached (tag={tag})")
        try:
            budget.check(self.ledger_path, self.cap)
        except budget.BudgetExceeded:
            if self.fallback is None:
                raise
            r = self.fallback.complete(system, prompt, schema=schema, sample=sample, tag=tag)
            r.meta = {**r.meta, "fallback_from": "anthropic"}
            return r
        sys_text = system + (RESPOND_HINT if schema is not None else "")
        body: dict[str, Any] = {
            "model": self.model, "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": sys_text, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": prompt}],
        }
        if schema is not None:
            body["tools"] = [{"name": "respond", "description": "Send your reply.", "input_schema": schema}]
            body["tool_choice"] = {"type": "auto"}
        last = ""
        for attempt in range(4):
            t0 = time.time()
            try:
                with self._http() as http:
                    r = http.post(API_URL, json=body)
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}"
                time.sleep(2 ** attempt + random.random())
                continue
            latency = time.time() - t0
            if r.status_code in (429, 500, 502, 503, 529):
                last = f"HTTP {r.status_code}"
                time.sleep(min(30, 2 ** attempt * 2) + random.random())
                continue
            if r.status_code != 200:
                raise LLMError(f"anthropic HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            u = data.get("usage", {})
            usd = budget.record(self.model, u, self.ledger_path)
            self.live_calls += 1
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            structured = next((b["input"] for b in data.get("content", [])
                               if b.get("type") == "tool_use" and b.get("name") == "respond"), None)
            if schema is not None and structured is None:
                try:
                    structured = json.loads(text)
                except json.JSONDecodeError:
                    structured = None
            resp = LLMResponse(
                text=text, model=self.model, structured=structured,
                input_tokens=u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0),
                output_tokens=u.get("output_tokens", 0), cache_read_tokens=u.get("cache_read_input_tokens", 0),
                cache_write_tokens=u.get("cache_creation_input_tokens", 0), cost_usd=round(usd, 8),
                latency_s=round(latency, 3), meta={"stop_reason": data.get("stop_reason"), "attempts": attempt + 1})
            self.cache.put(key, resp.to_dict(), meta={"tag": tag, "backend": self.backend})
            return resp
        raise LLMError(f"anthropic failed after retries: {last}")
