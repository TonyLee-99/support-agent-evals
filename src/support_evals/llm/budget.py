"""Hard spend guard for the Anthropic API backend, shared by every project on this machine.

The ledger is one JSON line per API call at ~/.agent-budget/anthropic_ledger.jsonl (flock-protected). Before
every call the running total is read; at or above the cap the call is refused (BudgetExceeded) and callers fall
back to the subscription CLI. The API key itself is never written anywhere.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

from support_evals.llm.base import LLMError

LEDGER = Path(os.environ.get("SE_BUDGET_LEDGER", str(Path.home() / ".agent-budget" / "anthropic_ledger.jsonl")))
CAP_USD = float(os.environ.get("SE_BUDGET_CAP_USD", "3.50"))
PROJECT = "support-agent-evals"

# Claude Haiku 5.5 list prices, USD per million tokens (Anthropic pricing table, prompts <= 100K tokens):
# input $0.10, output $0.50; prompt-cache write (5 min TTL) = 1.25x input; cache read = 0.1x input.
PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_write": 0.125, "cache_read": 0.01},
}


class BudgetExceeded(LLMError):
    """Shared API budget is exhausted; use the claude-cli backend."""


def cost_usd(model: str, usage: dict[str, Any]) -> float:
    p = PRICES[model]
    return (usage.get("input_tokens", 0) * p["input"] + usage.get("output_tokens", 0) * p["output"]
            + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
            + usage.get("cache_read_input_tokens", 0) * p["cache_read"]) / 1e6


def total_usd(path: Path | None = None) -> float:
    path = path or LEDGER
    if not path.exists():
        return 0.0
    tot = 0.0
    with open(path) as fh:
        fcntl.flock(fh, fcntl.LOCK_SH)
        for line in fh:
            if line.strip():
                try:
                    tot += float(json.loads(line).get("usd", 0.0))
                except (json.JSONDecodeError, ValueError):
                    continue
        fcntl.flock(fh, fcntl.LOCK_UN)
    return tot


def project_usd(path: Path | None = None) -> tuple[float, int]:
    path = path or LEDGER
    if not path.exists():
        return 0.0, 0
    usd, n = 0.0, 0
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("project") == PROJECT:
                usd += float(r.get("usd", 0.0))
                n += 1
    return usd, n


def check(path: Path | None = None, cap: float | None = None) -> float:
    t = total_usd(path)
    if t >= (cap if cap is not None else CAP_USD):
        raise BudgetExceeded(f"shared Anthropic budget reached: ${t:.4f} >= ${cap or CAP_USD:.2f}")
    return t


def record(model: str, usage: dict[str, Any], path: Path | None = None) -> float:
    path = path or LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    usd = cost_usd(model, usage)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "project": PROJECT, "model": model,
           "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
           "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
           "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0), "usd": round(usd, 8)}
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(rec) + "\n")
        fcntl.flock(fh, fcntl.LOCK_UN)
    return usd


def key_from_keychain() -> str | None:
    """Read the API key at runtime from the macOS keychain (never persisted by this code)."""
    import subprocess

    try:
        out = subprocess.run(["security", "find-generic-password", "-s", "anthropic-api-key", "-w"],
                             capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    k = out.stdout.strip()
    return k or None
