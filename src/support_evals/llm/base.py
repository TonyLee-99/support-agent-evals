"""LLMClient interface shared by the benchmark adapter, the service agent, the labeller and the judge."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    """A backend failed after its retries."""


class ReplayMiss(LLMError):
    """Replay mode was asked for a prompt that is not in the cache."""


@dataclass
class LLMResponse:
    text: str
    model: str
    structured: dict[str, Any] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0  # list-price equivalent reported by the backend
    latency_s: float = 0.0
    cached: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("cached")
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], cached: bool = True) -> LLMResponse:
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "cached"}
        return cls(**known, cached=cached)


class LLMClient(Protocol):
    backend: str
    model: str

    def complete(
        self,
        system: str,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        sample: Any = 0,
        tag: str = "",
    ) -> LLMResponse:
        """Return one completion. `sample` distinguishes repeated draws of the same prompt (trials)."""
        ...
