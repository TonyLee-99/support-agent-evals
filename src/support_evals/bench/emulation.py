"""Tool-call emulation: OpenAI-format chat (messages + tools) <-> one strict-JSON completion.

The CLI backend has no native function calling, so the whole conversation is rendered into one prompt and the
model must answer with a JSON object constrained by a schema. The schema makes "send text" and "call one tool"
mutually exclusive by construction (retail policy: one tool call at a time, never text and a call together).
The same adapter serves the baseline, our agent and the user simulator, so it cannot favour one of them, but
results are specific to this interface (see docs/EVAL.md).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_WITH_TOOLS = """\
<interface>
You reply through a JSON interface. Every reply is exactly one JSON object:
- To send a message to the other party: {"action": "message", "message": "<text>"}
- To call ONE tool: {"action": "tool_call", "tool_call": {"name": "<tool name>", "arguments": {...}}}
Never combine text and a tool call. The value of "message" is plain text (never JSON).
Tool results come back in the next turn.
Available tools (OpenAI function schemas):
<tools>
{tools}
</tools>
</interface>"""

# Added after INC-8: through the CLI the model sometimes tried a *native* tool call first, got "No such tool
# available" from the CLI, and then told the customer the tool was down. Kept opt-in so that the frozen tau2 runs
# keep their exact prompts (and stay replayable).
NATIVE_TOOLS_NOTE = """
These tools are NOT native function calls in this environment. The only way to call one is the JSON reply with
action "tool_call". If anything says "No such tool available", that refers to native calls: ignore it and use the
JSON reply. Never tell the other party that a tool is unavailable."""

PROTOCOL_NO_TOOLS = """\
<interface>
You reply through a JSON interface. Every reply is exactly one JSON object: {"message": "<text>"}
The value of "message" is the plain text you want to say (never JSON).
</interface>"""


def tools_schema(tool_names: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["message", "tool_call"]},
            "message": {"type": "string"},
            "tool_call": {
                "type": "object",
                "properties": {"name": {"type": "string", "enum": tool_names}, "arguments": {"type": "object"}},
                "required": ["name", "arguments"],
            },
        },
        "required": ["action"],
    }


TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
}


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multipart content: keep text parts only
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content)


def render(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
           native_tools_note: bool = False) -> tuple[str, str]:
    """Return (system, prompt). Leading system messages form the system prompt; the rest is a transcript."""
    sys_parts: list[str] = []
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        sys_parts.append(_content_text(messages[i].get("content")))
        i += 1
    if tools:
        sys_parts.append(PROTOCOL_WITH_TOOLS.replace("{tools}", json.dumps(tools, indent=1, sort_keys=True)))
        if native_tools_note:
            sys_parts.append(NATIVE_TOOLS_NOTE)
    else:
        sys_parts.append(PROTOCOL_NO_TOOLS)
    lines = [
        "Conversation so far, oldest first. [user] is the other party, [assistant] is you, "
        "[tool result] is the output of your tool call.",
        "",
    ]
    for m in messages[i:]:
        role = m.get("role")
        text = _content_text(m.get("content"))
        if role == "assistant":
            if text:
                lines += ["[assistant]", text]
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", tc)
                lines += [f"[assistant tool_call id={tc.get('id')}]", f"{fn.get('name')} {fn.get('arguments')}"]
        elif role == "tool":
            lines += [f"[tool result id={m.get('tool_call_id')}]", text]
        elif role == "system":
            lines += ["[system]", text]
        else:
            lines += ["[user]", text]
        lines.append("")
    lines.append("Write your next reply as one JSON object.")
    return "\n\n".join(sys_parts), "\n".join(lines)


class AdapterError(ValueError):
    """The model's JSON did not form a valid turn."""


def unwrap(msg: str) -> tuple[str, bool]:
    """Models sometimes double-encode: message = '{"message": "..."}'. Unwrap once and report the repair."""
    t = msg.strip()
    if t.startswith("{") and t.endswith("}"):
        try:
            inner = json.loads(t)
        except json.JSONDecodeError:
            return msg, False
        if isinstance(inner, dict) and isinstance(inner.get("message"), str) and inner["message"].strip():
            return inner["message"], True
    return msg, False


@dataclass
class ParsedTurn:
    content: str | None
    tool_call: dict[str, Any] | None  # {"id", "name", "arguments"}
    raw: dict[str, Any] = field(default_factory=dict)
    repaired: bool = False


def call_id(name: str, arguments: dict[str, Any], salt: str) -> str:
    h = hashlib.sha256(f"{salt}|{name}|{json.dumps(arguments, sort_keys=True)}".encode()).hexdigest()
    return f"call_{h[:12]}"


def parse(obj: dict[str, Any] | None, tool_names: list[str] | None, salt: str) -> ParsedTurn:
    if not isinstance(obj, dict):
        raise AdapterError("reply is not a JSON object")
    if not tool_names:
        msg = obj.get("message")
        if not isinstance(msg, str) or not msg.strip():
            raise AdapterError("empty message")
        text, rep = unwrap(msg)
        return ParsedTurn(content=text, tool_call=None, raw=obj, repaired=rep)
    action = obj.get("action")
    if action == "tool_call":
        tc = obj.get("tool_call")
        if isinstance(tc, str):  # double-encoded tool call (seen on the API, INC-9): decode once
            try:
                tc = json.loads(tc)
            except json.JSONDecodeError:
                pass
        if not isinstance(tc, dict) or tc.get("name") not in tool_names:
            raise AdapterError(f"invalid tool_call: {str(tc)[:200]}")
        args = tc.get("arguments")
        if not isinstance(args, dict):
            raise AdapterError("tool arguments must be an object")
        return ParsedTurn(content=None, tool_call={"id": call_id(tc["name"], args, salt), "name": tc["name"],
                                                   "arguments": args}, raw=obj)
    if action == "message":
        msg = obj.get("message")
        if not isinstance(msg, str) or not msg.strip():
            raise AdapterError("action=message with empty message")
        text, rep = unwrap(msg)
        return ParsedTurn(content=text, tool_call=None, raw=obj, repaired=rep)
    raise AdapterError(f"unknown action {action!r}")
