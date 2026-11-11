"""Deterministic replay backend: serves recorded responses, raises ReplayMiss otherwise. Zero network, zero cost."""

from __future__ import annotations

from typing import Any

from support_evals.llm.base import LLMResponse, ReplayMiss
from support_evals.llm.cache import DiskCache, cache_key


class ReplayClient:
    backend = "replay"

    def __init__(self, model: str, cache: DiskCache | None = None):
        self.model = model
        self.cache = cache or DiskCache()
        self.misses = 0

    def complete(self, system: str, prompt: str, *, schema: dict[str, Any] | None = None, sample: Any = 0,
                 tag: str = "") -> LLMResponse:
        hit = None
        for backend in ("claude_cli", "anthropic"):
            key = cache_key(model=self.model, system=system, prompt=prompt, schema=schema, sample=sample,
                            backend=backend)
            hit = self.cache.get(key)
            if hit is not None:
                break
        if hit is None:
            self.misses += 1
            raise ReplayMiss(f"replay miss for {self.model} key {key[:12]} (tag={tag})")
        return LLMResponse.from_dict(hit, cached=True)
