"""LLM backends. `get_client()` picks one from SE_LLM_BACKEND (claude_cli | anthropic | replay | auto)."""

from __future__ import annotations

import os

from support_evals.llm.base import LLMClient, LLMError, LLMResponse, ReplayMiss
from support_evals.llm.cache import DiskCache

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"

_clients: dict[tuple[str, str], LLMClient] = {}


def backend_name() -> str:
    b = os.environ.get("SE_LLM_BACKEND", "auto")
    if b == "auto":
        return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "claude_cli"
    return b


def get_client(model: str = HAIKU, backend: str | None = None) -> LLMClient:
    b = backend or backend_name()
    k = (b, model)
    if k not in _clients:
        cache = DiskCache()
        if b == "replay":
            from support_evals.llm.replay import ReplayClient
            _clients[k] = ReplayClient(model, cache)
        elif b == "anthropic":
            from support_evals.llm.anthropic_api import AnthropicClient
            _clients[k] = AnthropicClient(model, cache)
        elif b == "claude_cli":
            from support_evals.llm.claude_cli import ClaudeCLIClient
            _clients[k] = ClaudeCLIClient(model, cache=cache)
        else:
            raise ValueError(f"unknown SE_LLM_BACKEND {b}")
    return _clients[k]


__all__ = ["LLMClient", "LLMError", "LLMResponse", "ReplayMiss", "DiskCache", "get_client", "HAIKU", "SONNET"]
