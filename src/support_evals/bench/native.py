"""Native tool calling on the Anthropic Messages API, as a LiteLLM provider `claudenative/<model>` (round 3).

Removes the round-2 interface confound: instead of one JSON `respond` wrapper, tau2's tools are passed as real
Messages-API tools and the model answers with `tool_use` blocks. Used for BOTH agents, so the comparison stays fair.
Same guarantees as the other backends: shared budget check before every call (llm/budget.py), ledger record after,
response cache keyed by the full request + trial, offline replay under SE_LLM_BACKEND=replay.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from collections import Counter
from typing import Any

import httpx
import litellm
from litellm import CustomLLM
from litellm.types.utils import ChatCompletionMessageToolCall, Function

from support_evals.llm import budget
from support_evals.llm.base import LLMError, ReplayMiss
from support_evals.llm.cache import DiskCache, cache_key

API_URL = "https://api.anthropic.com/v1/messages"
STATS: Counter[str] = Counter()
_lock = threading.Lock()
_cache = DiskCache()


def bump(k: str) -> None:
    with _lock:
        STATS[k] += 1


def _text(c: Any) -> str:
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    return "\n".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")


def to_anthropic(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """OpenAI-format chat -> (system blocks, alternating user/assistant messages)."""
    system: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        role, text = m.get("role"), _text(m.get("content"))
        if role == "system":
            if not out:
                system.append({"type": "text", "text": text})
            else:  # mid-conversation note (e.g. a guard's feedback): delivered as user-side text
                add("user", [{"type": "text", "text": f"[system note] {text}"}])
        elif role == "assistant":
            blocks: list[dict[str, Any]] = [{"type": "text", "text": text}] if text.strip() else []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", tc)
                args = fn.get("arguments") or "{}"
                blocks.append({"type": "tool_use", "id": tc["id"], "name": fn["name"],
                               "input": json.loads(args) if isinstance(args, str) else args})
            if blocks:
                add("assistant", blocks)
        elif role == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": text or "(empty)"}])
        elif text.strip():
            add("user", [{"type": "text", "text": text}])
    if not out or out[0]["role"] != "user":  # the API needs a user turn first; tau2 opens with the agent's greeting
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation start)"}]})
    if system:
        system[-1]["cache_control"] = {"type": "ephemeral"}
    return system, out


def to_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    out = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
            "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}} for t in tools]
    out[-1]["cache_control"] = {"type": "ephemeral"}  # tools + system are the stable, cached prefix
    return out


def call(model: str, body: dict[str, Any], sample: Any, role: str) -> dict[str, Any]:
    key = cache_key(model=model, system=json.dumps(body.get("system"), sort_keys=True),
                    prompt=json.dumps({"messages": body["messages"], "tools": body.get("tools")}, sort_keys=True),
                    schema=None, sample=sample, backend="anthropic_native")
    hit = _cache.get(key)
    if hit is not None:
        bump(f"{role}:cached")
        return hit
    if os.environ.get("SE_LLM_BACKEND") == "replay":
        raise ReplayMiss(f"native replay miss {key[:12]}")
    budget.check()
    hdr = {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01",
           "content-type": "application/json"}
    last = ""
    for attempt in range(4):
        t0 = time.time()
        try:
            with httpx.Client(timeout=180.0, headers=hdr) as h:
                r = h.post(API_URL, json=body)
        except httpx.HTTPError as e:
            last = type(e).__name__
            time.sleep(2 ** attempt + random.random())
            continue
        if r.status_code in (429, 500, 502, 503, 529):
            last = f"HTTP {r.status_code}"
            time.sleep(min(30, 2 ** attempt * 2) + random.random())
            continue
        if r.status_code != 200:
            raise LLMError(f"anthropic HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        usd = budget.record(model, data.get("usage", {}))
        resp = {"content": data.get("content", []), "stop_reason": data.get("stop_reason"),
                "usage": data.get("usage", {}), "cost_usd": round(usd, 8), "latency_s": round(time.time() - t0, 3)}
        _cache.put(key, resp, meta={"tag": f"tau2native:{role}", "backend": "anthropic_native"})
        bump(f"{role}:live")
        return resp
    raise LLMError(f"anthropic native failed: {last}")


class ClaudeNativeProvider(CustomLLM):
    def completion(self, model, messages, api_base, custom_prompt_dict, model_response, print_verbose, encoding,
                   api_key, logging_obj, optional_params, acompletion=None, litellm_params=None, logger_fn=None,
                   headers=None, timeout=None, client=None):
        from support_evals.bench.provider import USAGE_LIMIT, _tls

        meta = (litellm_params or {}).get("metadata") or {}
        trial, role = meta.get("trial", 0), meta.get("role", "unknown")
        system, msgs = to_anthropic(messages)
        body: dict[str, Any] = {"model": model, "max_tokens": 8000, "messages": msgs}
        if system:
            body["system"] = system
        tools = to_tools(optional_params.get("tools"))
        if tools:
            body["tools"] = tools
            # retail policy: at most one tool call at a time - enforced the API's own way, identically for both agents
            body["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        resp = None
        for attempt in range(3):
            try:
                r = call(model, body, trial if attempt == 0 else f"{trial}:retry{attempt}", role)
            except LLMError as e:
                if isinstance(e, budget.BudgetExceeded):
                    USAGE_LIMIT.set()  # stop the run; never fall back to another backend mid-comparison
                if not hasattr(_tls, "errors"):
                    _tls.errors = []
                _tls.errors.append(repr(e)[:300])
                raise
            bump(f"{role}:calls")
            uses = [b for b in r["content"] if b.get("type") == "tool_use"]
            text = "".join(b.get("text", "") for b in r["content"] if b.get("type") == "text").strip()
            if uses or text:
                resp = (r, uses, text)
                break
            bump(f"{role}:invalid_empty")  # no text and no tool call (e.g. only thinking, or max_tokens)
        if resp is None:
            bump(f"{role}:invalid_failed")
            if not hasattr(_tls, "errors"):
                _tls.errors = []
            _tls.errors.append("native: no valid turn after retries")
            raise LLMError("native: no valid turn after retries")
        r, uses, text = resp
        msg = model_response.choices[0].message
        if uses:
            if text:
                bump(f"{role}:text_dropped_with_tool")  # tau2 forbids text + tool in one turn
            msg.content = None
            msg.tool_calls = [ChatCompletionMessageToolCall(id=u["id"], type="function", function=Function(
                name=u["name"], arguments=json.dumps(u.get("input") or {}))) for u in uses]
            if len(uses) > 1:
                bump(f"{role}:parallel_tool_calls")
            model_response.choices[0].finish_reason = "tool_calls"
        else:
            msg.content = text
            model_response.choices[0].finish_reason = "stop"
        u = r.get("usage", {})
        model_response.model = model
        model_response.usage = litellm.Usage(prompt_tokens=u.get("input_tokens", 0), completion_tokens=u.get(
            "output_tokens", 0), total_tokens=u.get("input_tokens", 0) + u.get("output_tokens", 0))
        return model_response
