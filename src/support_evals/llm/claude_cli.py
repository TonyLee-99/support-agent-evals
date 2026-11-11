"""Claude through the headless Claude Code CLI (owner's subscription; there is no API key on this machine).

Each call strips Claude Code's own system prompt, tools, MCP servers and settings, so the request is only our
system prompt + our prompt. Structured output uses `--json-schema` (the CLI validates and returns
`structured_output`). The CLI reports usage and a list-price-equivalent `total_cost_usd`, which we record.
"""

from __future__ import annotations

import fcntl
import json
import os
import random
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from support_evals.llm.base import LLMError, LLMResponse, ReplayMiss
from support_evals.llm.cache import ROOT, DiskCache, cache_key

SANDBOX = ROOT / ".llm_sandbox"  # empty cwd so no CLAUDE.md / project settings leak into requests
LOCK_DIR = Path(os.environ.get("SE_LOCK_DIR", str(ROOT / "data" / "locks")))
MAX_PROCS = int(os.environ.get("SE_MAX_CLAUDE_PROCS", "4"))

_local_sem = threading.Semaphore(MAX_PROCS)


@contextmanager
def process_slot():
    """Cross-process limit on concurrent `claude -p` processes (file locks), plus an in-process semaphore."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    with _local_sem:
        while True:
            for i in range(MAX_PROCS):
                fh = open(LOCK_DIR / f"slot{i}.lock", "w")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    fh.close()
                    continue
                try:
                    yield i
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                    fh.close()
                return
            time.sleep(0.2 + random.random() * 0.3)


def is_usage_limit(text: str) -> bool:
    t = text.lower()
    return any(s in t for s in ("usage limit", "rate limit", "limit reached", "resets at", "out of extra usage", "hit your",
                                "-hour limit", "weekly limit"))


class UsageLimitError(LLMError):
    """Subscription usage limit hit: callers should checkpoint and stop, not retry forever."""


class ClaudeCLIClient:
    backend = "claude_cli"

    def __init__(self, model: str = "claude-haiku-5-5", timeout_s: float = 300.0, max_retries: int = 3,
                 cache: DiskCache | None = None, strict_replay: bool = False):
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.cache = cache or DiskCache()
        self.strict_replay = strict_replay
        self.live_calls = 0

    def _cmd(self, system: str, schema: dict[str, Any] | None) -> list[str]:
        cmd = [
            "claude", "-p",
            "--model", self.model,
            "--output-format", "json",
            "--system-prompt", system,
            "--tools", "",
            "--strict-mcp-config",
            "--setting-sources", "",
            "--no-session-persistence",
            "--disable-slash-commands",
        ]
        if schema is not None:
            cmd += ["--json-schema", json.dumps(schema)]
        return cmd

    def complete(self, system: str, prompt: str, *, schema: dict[str, Any] | None = None, sample: Any = 0,
                 tag: str = "") -> LLMResponse:
        # Temperature is not exposed by the CLI; the default sampling applies, so different `sample` values differ.
        key = cache_key(model=self.model, system=system, prompt=prompt, schema=schema, sample=sample)
        hit = self.cache.get(key)
        if hit is not None:
            return LLMResponse.from_dict(hit, cached=True)
        if self.strict_replay:
            raise ReplayMiss(f"{self.model}: key {key[:12]} not cached (tag={tag})")
        SANDBOX.mkdir(exist_ok=True)
        last_err = ""
        for attempt in range(self.max_retries + 1):
            t0 = time.time()
            out: dict[str, Any] = {}
            stderr = ""
            try:
                with process_slot():
                    t0 = time.time()
                    proc = subprocess.run(self._cmd(system, schema), input=prompt, cwd=SANDBOX,
                                          capture_output=True, text=True, timeout=self.timeout_s)
                stderr = proc.stderr
                out = json.loads(proc.stdout) if proc.stdout.strip() else {}
            except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
                last_err = f"{type(e).__name__}: {e}"
            latency = time.time() - t0
            if out and not out.get("is_error") and out.get("subtype") == "success":
                u = out.get("usage", {})
                structured = out.get("structured_output")
                if schema is not None and structured is None:
                    try:
                        structured = json.loads(out.get("result", ""))
                    except json.JSONDecodeError:
                        last_err = "schema requested but no structured_output"
                        continue
                resp = LLMResponse(
                    text=out.get("result", "") or "",
                    model=self.model,
                    structured=structured,
                    input_tokens=u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0),
                    output_tokens=u.get("output_tokens", 0),
                    cache_read_tokens=u.get("cache_read_input_tokens", 0),
                    cache_write_tokens=u.get("cache_creation_input_tokens", 0),
                    cost_usd=float(out.get("total_cost_usd", 0.0)),
                    latency_s=round(latency, 3),
                    meta={"num_turns": out.get("num_turns"), "attempts": attempt + 1},
                )
                self.live_calls += 1
                self.cache.put(key, resp.to_dict(), meta={"tag": tag, "backend": self.backend})
                return resp
            msg = f"{out.get('result', '')} {stderr}" if out else stderr
            if is_usage_limit(str(msg)):
                raise UsageLimitError(f"claude CLI usage limit: {str(msg)[:300]}")
            if out:
                last_err = f"is_error={out.get('is_error')} subtype={out.get('subtype')} result={str(out.get('result'))[:300]}"
            elif not last_err:
                last_err = f"stderr={stderr[:300]}"
            time.sleep(min(60, (2 ** attempt) * 4) + random.random() * 2)  # backoff with jitter
        raise LLMError(f"claude CLI failed after {self.max_retries + 1} attempts: {last_err}")


def cli_version() -> str:
    try:
        return subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def sandbox_path() -> Path:
    return SANDBOX
