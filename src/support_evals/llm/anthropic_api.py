"""Anthropic Messages API backend, used only when ANTHROPIC_API_KEY is set (never on the build machine).

Structured output is requested the standard way: one tool whose input_schema is the schema, forced via tool_choice.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from support_evals.llm.base import LLMError, LLMResponse, ReplayMiss
from support_evals.llm.cache import DiskCache, cache_key

API_URL = "https://api.anthropic.com/v1/messages"


class AnthropicClient:
    backend = "anthropic"

    def __init__(self, model: str = "claude-haiku-5-5", cache: DiskCache | None = None,
                 transport: httpx.BaseTransport | None = None, max_tokens: int = 2048, strict_replay: bool = False):
        self.model = model
        self.cache = cache or DiskCache()
        self.max_tokens = max_tokens
        self.strict_replay = strict_replay
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        self._http = httpx.Client(transport=transport, timeout=120.0, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})

    def complete(self, system: str, prompt: str, *, schema: dict[str, Any] | None = None, sample: int = 0,
                 tag: str = "") -> LLMResponse:
        key = cache_key(model=self.model, system=system, prompt=prompt, schema=schema, sample=sample)
        hit = self.cache.get(key)
        if hit is not None:
            return LLMResponse.from_dict(hit, cached=True)
        if self.strict_replay:
            raise ReplayMiss(f"{self.model}: key {key[:12]} not cached (tag={tag})")
        body: dict[str, Any] = {"model": self.model, "max_tokens": self.max_tokens, "system": system,
                                "messages": [{"role": "user", "content": prompt}]}
        if schema is not None:
            body["tools"] = [{"name": "respond", "description": "Return the answer.", "input_schema": schema}]
            body["tool_choice"] = {"type": "tool", "name": "respond"}
        t0 = time.time()
        r = self._http.post(API_URL, json=body)
        if r.status_code != 200:
            raise LLMError(f"anthropic {r.status_code}: {r.text[:300]}")
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        structured = next((b["input"] for b in data.get("content", []) if b.get("type") == "tool_use"), None)
        u = data.get("usage", {})
        resp = LLMResponse(text=text, model=self.model, structured=structured,
                           input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                           latency_s=round(time.time() - t0, 3))
        self.cache.put(key, resp.to_dict(), meta={"tag": tag, "backend": self.backend})
        return resp
