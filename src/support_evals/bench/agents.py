"""Our tau2 agent: tau2's LLMAgent + a policy-checklist prompt + code guards, each behind a flag (for ablations).

Information boundary: guards only read the public tool schemas, the policy text and the messages visible in this
conversation (tool results the agent itself received). The factory drops tau2's `task` argument, so no expected
actions or evaluator data can reach the agent (tests/test_agent_guards.py checks this).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from tau2.agent.llm_agent import AGENT_INSTRUCTION, LLMAgent
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.utils.llm_utils import generate

from support_evals.prompts import PLANNER_INSTRUCTION, PLANNER_R3_EXTRA

ALL_FLAGS = ("planner", "arg_check", "confirm_gate", "identity_gate", "handoff")
R3_FLAGS = ("planner_r3", "claim_check")  # round 3 additions (opt-in; round-2 agents keep their exact prompts)

WRITE_TOOLS = {
    "cancel_pending_order", "exchange_delivered_order_items", "modify_pending_order_address",
    "modify_pending_order_items", "modify_pending_order_payment", "modify_user_address",
    "return_delivered_order_items",
}
AUTH_TOOLS = {"find_user_id_by_email", "find_user_id_by_name_zip"}
NO_AUTH_NEEDED = AUTH_TOOLS | {"calculate", "transfer_to_human_agents"}
PENDING_ONLY = {"cancel_pending_order", "modify_pending_order_address", "modify_pending_order_items",
                "modify_pending_order_payment"}
DELIVERED_ONLY = {"exchange_delivered_order_items", "return_delivered_order_items"}
TRANSFER_TEXT = "YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON."
MAX_GUARD_RETRIES = 2



SYSTEM_PROMPT = """
<instructions>
{agent_instruction}
</instructions>
<checklist>
{planner}
</checklist>
<policy>
{domain_policy}
</policy>
""".strip()

_AFFIRM = re.compile(
    r"\b(yes|yeah|yep|yup|confirm(ed)?|proceed|go ahead|correct|sure|ok(ay)?|please do|do it|that'?s right|"
    r"sounds good|absolutely|affirmative)\b", re.I)
_NEGATE = re.compile(r"\b(no|not|don'?t|wait|hold on|stop|cancel that|actually|instead|change)\b", re.I)


def is_affirmation(text: str | None) -> bool:
    if not text:
        return False
    return bool(_AFFIRM.search(text)) and not _NEGATE.search(text.split(".")[0])


def has_fresh_affirmation(history: list[Any]) -> bool:
    """Latest user turn is an explicit yes, and no write has consumed it since (one yes = one write)."""
    for m in reversed(history):
        if isinstance(m, AssistantMessage) and m.tool_calls and any(t.name in WRITE_TOOLS for t in m.tool_calls):
            return False
        if isinstance(m, UserMessage):
            return is_affirmation(m.content)
    return False


_DONE = re.compile(r"\b(has|have) been (cancel+ed|returned|exchanged|modified|updated|processed|changed)\b|"
                   r"\bi(?:'ve| have) (cancel+ed|returned|exchanged|modified|updated|processed|changed)\b|"
                   r"\b(is|are) now (cancel+ed|updated|modified)\b", re.I)


def _claims_done(text: str | None) -> bool:
    return bool(text and _DONE.search(text))


def _write_since_user(history: list[Any]) -> bool:
    """A write tool call (with its result) happened after the latest user message."""
    for m in reversed(history):
        if isinstance(m, UserMessage):
            return False
        if isinstance(m, AssistantMessage) and m.tool_calls and any(t.name in WRITE_TOOLS for t in m.tool_calls):
            return True
    return False


@dataclass
class Observed:
    """What the agent has seen in this conversation (tool results only, never hidden state)."""

    user_id: str | None = None
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    products: dict[str, dict[str, Any]] = field(default_factory=dict)
    payment_ids: set[str] = field(default_factory=set)


def observe(messages: list[Any]) -> Observed:
    obs = Observed()
    calls: dict[str, ToolCall] = {}
    for m in messages:
        if isinstance(m, AssistantMessage) and m.tool_calls:
            for tc in m.tool_calls:
                calls[tc.id] = tc
        tool_msgs = m.tool_messages if isinstance(m, MultiToolMessage) else [m] if isinstance(m, ToolMessage) else []
        for tm in tool_msgs:
            tc = calls.get(tm.id)
            if tc is None or tm.error or not tm.content:
                continue
            if tc.name in AUTH_TOOLS and not tm.content.startswith("Error"):
                obs.user_id = tm.content.strip().strip('"')
                continue
            try:
                data = json.loads(tm.content)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if tc.name == "get_user_details" and "user_id" in data:
                obs.users[data["user_id"]] = data
                obs.payment_ids |= set((data.get("payment_methods") or {}).keys())
            elif "order_id" in data and "items" in data:
                obs.orders[data["order_id"]] = data
                obs.payment_ids |= {p.get("payment_method_id") for p in data.get("payment_history", [])}
            elif tc.name == "get_product_details" and "product_id" in data:
                obs.products[data["product_id"]] = data
    return obs


def _schema_errors(call: ToolCall, schema: dict[str, Any]) -> list[str]:
    params = schema.get("function", {}).get("parameters", {})
    props = params.get("properties", {})
    errs = [f"missing argument '{r}'" for r in params.get("required", []) if r not in call.arguments]
    errs += [f"unknown argument '{k}'" for k in call.arguments if k not in props]
    types = {"string": str, "array": list, "object": dict, "number": (int, float), "integer": int, "boolean": bool}
    for k, v in call.arguments.items():
        t = props.get(k, {}).get("type")
        if isinstance(t, str) and t in types and not isinstance(v, types[t]):
            errs.append(f"argument '{k}' should be {t}")
    return errs


class PolicyAgent(LLMAgent):
    def __init__(self, *args: Any, flags: frozenset[str] = frozenset(ALL_FLAGS), **kwargs: Any):
        super().__init__(*args, **kwargs)
        unknown = set(flags) - set(ALL_FLAGS) - set(R3_FLAGS)
        if unknown:
            raise ValueError(f"unknown flags {unknown}")
        self.flags = frozenset(flags)
        self._schemas = {t.name: t.openai_schema for t in self.tools}
        self.guard_log: list[dict[str, Any]] = []

    @property
    def system_prompt(self) -> str:
        if "planner" not in self.flags:
            return super().system_prompt
        planner = PLANNER_INSTRUCTION + ("\n" + PLANNER_R3_EXTRA if "planner_r3" in self.flags else "")
        return SYSTEM_PROMPT.format(agent_instruction=AGENT_INSTRUCTION, planner=planner,
                                    domain_policy=self.domain_policy)

    # --- guards -------------------------------------------------------------------------------------------
    def check(self, msg: AssistantMessage, history: list[Any], incoming: Any) -> str | None:
        """Return a reason string if the proposed message must not be emitted."""
        if not msg.tool_calls:
            if "claim_check" in self.flags and _claims_done(msg.content) and not _write_since_user(history):
                return ("you said an action is done, but no write tool call has returned since the user's last "
                        "message; call the tool first (after confirmation) or do not claim it")
            return None
        call = msg.tool_calls[0]
        obs = observe(history)
        if "arg_check" in self.flags and call.name in self._schemas:
            errs = _schema_errors(call, self._schemas[call.name])
            if errs:
                return f"invalid arguments for {call.name}: " + "; ".join(errs)
        if "identity_gate" in self.flags and call.name not in NO_AUTH_NEEDED:
            if obs.user_id is None:
                return "the user is not authenticated yet; authenticate via email or name + zip first"
            uid = call.arguments.get("user_id")
            if uid is not None and uid != obs.user_id:
                return f"user_id {uid} is not the authenticated user; you may only help the authenticated user"
            oid = call.arguments.get("order_id")
            if oid in obs.orders and obs.orders[oid].get("user_id") != obs.user_id:
                return f"order {oid} belongs to a different user; deny this request"
        if call.name in WRITE_TOOLS:
            if "arg_check" in self.flags:
                reason = self._write_arg_check(call, obs)
                if reason:
                    return reason
            if "confirm_gate" in self.flags:
                if not has_fresh_affirmation(history):
                    return ("no explicit user confirmation for this write; list the action details and ask the user "
                            "to confirm with 'yes' first")
        return None

    def _write_arg_check(self, call: ToolCall, obs: Observed) -> str | None:
        a = call.arguments
        oid = a.get("order_id")
        if call.name == "modify_user_address":
            return None
        if oid not in obs.orders:
            return f"order {oid} has not been looked up; call get_order_details and check its status first"
        order = obs.orders[oid]
        status = order.get("status", "")
        if call.name in PENDING_ONLY and status != "pending":
            return f"order {oid} has status '{status}'; this action needs a 'pending' order"
        if call.name in DELIVERED_ONLY and status != "delivered":
            return f"order {oid} has status '{status}'; this action needs a 'delivered' order"
        order_items = {i["item_id"]: i for i in order.get("items", [])}
        for iid in a.get("item_ids", []) or []:
            if iid not in order_items:
                return f"item {iid} is not in order {oid}"
        new_ids = a.get("new_item_ids") or []
        if new_ids:
            if len(new_ids) != len(a.get("item_ids", [])):
                return "item_ids and new_item_ids must have the same length"
            for old, new in zip(a.get("item_ids", []), new_ids, strict=False):
                pid = order_items[old]["product_id"]
                prod = obs.products.get(pid)
                if prod is None:
                    return f"call get_product_details for product {pid} before choosing a new item"
                var = (prod.get("variants") or {}).get(new)
                if var is None:
                    return f"item {new} is not a variant of product {pid}"
                if not var.get("available", False):
                    return f"item {new} is not available"
        pm = a.get("payment_method_id")
        if pm is not None and pm not in obs.payment_ids:
            return f"payment method {pm} has not been seen in the user's profile or order; look it up first"
        return None

    def _fallback(self, reason: str) -> AssistantMessage:
        if "not authenticated" in reason:
            text = "Before I can help, I need to verify your identity. Could you share your email, or your full name and zip code?"
        elif "confirmation" in reason:
            text = "Before I make this change, please confirm: would you like me to proceed with the action we discussed? (yes/no)"
        else:
            text = "I'm sorry, I can't complete that request as stated. Could you clarify the details?"
        return AssistantMessage(role="assistant", content=text)

    def _generate_next_message(self, message: Any, state: Any) -> AssistantMessage:
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)
        if "handoff" in self.flags and isinstance(message, ToolMessage) and self._last_call_name(state) == \
                "transfer_to_human_agents":
            return AssistantMessage(role="assistant", content=TRANSFER_TEXT)  # exact text the policy requires
        feedback: list[Any] = []
        for attempt in range(MAX_GUARD_RETRIES + 1):
            msg = generate(model=self.llm, tools=self.tools, messages=state.system_messages + state.messages + feedback,
                           call_name="agent_response", **self.llm_args)
            reason = self.check(msg, state.messages, message)
            if reason is None:
                return msg
            call = msg.tool_calls[0]
            self.guard_log.append({"turn": len(state.messages), "attempt": attempt, "tool": call.name,
                                   "reason": reason})
            feedback = [SystemMessage(role="system", content=(
                f"Your proposed tool call {call.name}({json.dumps(call.arguments)}) was blocked by a policy check: "
                f"{reason}. Choose a different next step."))]
        return self._fallback(reason)

    @staticmethod
    def _last_call_name(state: Any) -> str | None:
        for m in reversed(state.messages):
            if isinstance(m, AssistantMessage) and m.tool_calls:
                return m.tool_calls[-1].name
        return None


def make_factory(flags: frozenset[str]):
    def factory(tools, domain_policy, **kwargs):
        kwargs.pop("task", None)  # never let the agent see the task / expected actions
        return PolicyAgent(tools=tools, domain_policy=domain_policy, llm=kwargs.get("llm"),
                           llm_args=kwargs.get("llm_args"), flags=flags)
    return factory


def agent_name(flags: frozenset[str]) -> str:
    if set(flags) == set(ALL_FLAGS):
        return "policy_agent"
    if not flags:
        return "policy_agent__none"
    return "policy_agent__minus_" + "_".join(sorted(set(ALL_FLAGS) - set(flags)))


def register_agents() -> None:
    from tau2.registry import registry

    variants = [frozenset(ALL_FLAGS)] + [frozenset(ALL_FLAGS) - {f} for f in ALL_FLAGS]
    named = {agent_name(f): f for f in variants}
    r3 = frozenset(ALL_FLAGS) | set(R3_FLAGS)
    named["policy_agent_r3"] = r3
    named["policy_agent_r3__no_confirm_gate"] = r3 - {"confirm_gate"}
    named["policy_agent_r3__no_claim_check"] = r3 - {"claim_check"}
    for name, flags in named.items():
        if registry.get_agent_factory(name) is None:
            registry.register_agent_factory(make_factory(flags), name)
