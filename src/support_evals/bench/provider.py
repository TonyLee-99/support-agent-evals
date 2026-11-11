"""LiteLLM custom provider `claudecli/<model>`: lets tau2 (unmodified) call Claude through our LLMClient.

tau2 calls `litellm.completion()` for the agent, the user simulator and the NL-assertion judge. Registering a
`CustomLLM` is LiteLLM's documented extension point, so no tau2 file changes. The trial index and the caller role
arrive via `metadata` in llm_args and go into the cache key (`sample`), so trials are independent draws and every
run is replayable offline.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from typing import Any

import litellm
from litellm import CustomLLM
from litellm.types.utils import ChatCompletionMessageToolCall, Function

from support_evals.bench.emulation import TEXT_SCHEMA, AdapterError, parse, render, tools_schema
from support_evals.llm import LLMError, get_client
from support_evals.llm.budget import BudgetExceeded
from support_evals.llm.claude_cli import UsageLimitError

MAX_ADAPTER_RETRIES = 2

STATS: Counter[str] = Counter()
USAGE_LIMIT = threading.Event()
# Per-thread infra error counter: a simulation that hit a backend failure is an infrastructure fault, not an agent
# failure, and is excluded + re-run instead of being scored as 0.
_tls = threading.local()


def reset_infra_errors() -> None:
    _tls.errors = []


def infra_errors() -> list[str]:
    return list(getattr(_tls, "errors", []))
_stats_lock = threading.Lock()


def bump(key: str, n: int = 1) -> None:
    with _stats_lock:
        STATS[key] += n


def _first_json_object(text: str) -> str:
    """Keep only the first JSON object when the grader appends a second one (INC-5/INC-9 'Extra data').
    Valid JSON passes through unchanged, so recorded runs replay identically."""
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return text
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return text
    bump("unknown:first_json_object")
    return json.dumps(obj)


class ClaudeCLIProvider(CustomLLM):
    def completion(self, model, messages, api_base, custom_prompt_dict, model_response, print_verbose, encoding,
                   api_key, logging_obj, optional_params, acompletion=None, litellm_params=None, logger_fn=None,
                   headers=None, timeout=None, client=None):
        meta = (litellm_params or {}).get("metadata") or {}
        trial = meta.get("trial", 0)
        role = meta.get("role", "unknown")
        tools = optional_params.get("tools") or None
        tool_names = [t["function"]["name"] for t in tools] if tools else None
        system, prompt = render(messages, tools)
        schema = tools_schema(tool_names) if tool_names else TEXT_SCHEMA
        llm = get_client(model)
        t0 = time.time()
        totals = {"in": 0, "out": 0, "cost": 0.0}
        turn = None
        last_err: Exception | None = None
        for attempt in range(MAX_ADAPTER_RETRIES + 1):
            # attempt>0 uses a different sample key so a bad cached reply is not replayed forever
            sample: Any = trial if attempt == 0 else f"{trial}:retry{attempt}"
            try:
                resp = llm.complete(system, prompt, schema=schema, sample=sample, tag=f"tau2:{role}")
            except LLMError as e:
                if isinstance(e, (UsageLimitError, BudgetExceeded)):
                    USAGE_LIMIT.set()  # stop the whole run cleanly; never switch backend mid-comparison
                if not hasattr(_tls, "errors"):
                    _tls.errors = []
                _tls.errors.append(repr(e)[:300])
                raise
            totals["in"] += resp.input_tokens
            totals["out"] += resp.output_tokens
            totals["cost"] += resp.cost_usd
            bump(f"{role}:calls")
            bump(f"{role}:live" if not resp.cached else f"{role}:cached")
            try:
                turn = parse(resp.structured, tool_names, salt=f"{trial}|{len(messages)}")
                if turn.repaired:
                    bump(f"{role}:adapter_unwrapped")
                break
            except AdapterError as e:
                last_err = e
                bump(f"{role}:adapter_invalid")
        if turn is None:
            bump(f"{role}:adapter_failed")
            # Interface failure, not agent behaviour: the simulation is discarded and re-run (count reported).
            if not hasattr(_tls, "errors"):
                _tls.errors = []
            _tls.errors.append(f"adapter_failed: {last_err}")
            raise AdapterError(f"no valid turn after retries: {last_err}")

        msg = model_response.choices[0].message
        if turn.tool_call:
            tc = turn.tool_call
            msg.content = None
            msg.tool_calls = [ChatCompletionMessageToolCall(
                id=tc["id"], type="function",
                function=Function(name=tc["name"], arguments=json.dumps(tc["arguments"])))]
            model_response.choices[0].finish_reason = "tool_calls"
        else:
            msg.content = turn.content
            if role == "unknown" and turn.content:
                msg.content = _first_json_object(turn.content)  # tau2's NL grader json.loads the raw text
            model_response.choices[0].finish_reason = "stop"
        model_response.model = model
        model_response.usage = litellm.Usage(prompt_tokens=totals["in"], completion_tokens=totals["out"],
                                             total_tokens=totals["in"] + totals["out"])
        model_response._hidden_params["se_cost_usd"] = totals["cost"]
        model_response._hidden_params["se_latency_s"] = round(time.time() - t0, 3)
        return model_response


_registered = False


def register() -> None:
    """Idempotently register the provider and zero-cost pricing (real list-price cost is in our ledger)."""
    global _registered
    if _registered:
        return
    from support_evals.bench.native import ClaudeNativeProvider

    litellm.custom_provider_map = [{"provider": "claudecli", "custom_handler": ClaudeCLIProvider()},
                                   {"provider": "claudenative", "custom_handler": ClaudeNativeProvider()}]
    litellm.register_model({
        f"{p}/{m}": {"input_cost_per_token": 0.0, "output_cost_per_token": 0.0, "litellm_provider": p, "mode": "chat"}
        for m in ("claude-haiku-5-5", "claude-sonnet-5-5") for p in ("claudecli", "claudenative")
    })
    _registered = True
