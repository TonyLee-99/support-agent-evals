import json

import litellm
import pytest

from support_evals.bench import provider
from support_evals.bench.emulation import AdapterError, parse, render, tools_schema, unwrap
from support_evals.llm.base import LLMResponse

TOOLS = [{"type": "function", "function": {"name": "get_order_details", "parameters": {
    "type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}}]


class FakeLLM:
    backend, model = "fake", "claude-haiku-5-5"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, prompt, *, schema=None, sample=0, tag=""):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema, "sample": sample, "tag": tag})
        return LLMResponse(text="", model=self.model, structured=self.replies.pop(0), input_tokens=7, output_tokens=3)


@pytest.fixture
def fake(monkeypatch):
    provider.register()

    def install(replies):
        f = FakeLLM(replies)
        monkeypatch.setattr(provider, "get_client", lambda model: f)
        return f
    return install


def test_render_roundtrip_contains_tool_calls_and_results():
    msgs = [{"role": "system", "content": "POLICY"}, {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "order #W1"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {
                "name": "get_order_details", "arguments": '{"order_id": "#W1"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"status": "pending"}'}]
    system, prompt = render(msgs, TOOLS)
    assert system.startswith("POLICY") and "get_order_details" in system
    assert prompt.index("[user]") < prompt.index("[assistant tool_call id=c1]") < prompt.index("[tool result id=c1]")
    assert '{"status": "pending"}' in prompt


def test_parse_is_mutually_exclusive_and_strict():
    names = ["get_order_details"]
    t = parse({"action": "tool_call", "tool_call": {"name": "get_order_details", "arguments": {"order_id": "#W1"}},
               "message": "ignored"}, names, "s")
    assert t.content is None and t.tool_call["name"] == "get_order_details"
    for bad in ({"action": "tool_call", "tool_call": {"name": "drop_db", "arguments": {}}},
                {"action": "message", "message": "  "}, {"action": "dance"}, None):
        with pytest.raises(AdapterError):
            parse(bad, names, "s")
    assert tools_schema(names)["properties"]["tool_call"]["properties"]["name"]["enum"] == names


def test_unwrap_double_encoded_message():
    assert unwrap('{"message": "hello"}') == ("hello", True)
    assert unwrap("plain {not json}") == ("plain {not json}", False)


def test_litellm_completion_maps_tool_call_and_passes_trial(fake):
    f = fake([{"action": "tool_call", "tool_call": {"name": "get_order_details", "arguments": {"order_id": "#W1"}}}])
    r = litellm.completion(model="claudecli/claude-haiku-5-5", messages=[{"role": "user", "content": "hi"}],
                           tools=TOOLS, metadata={"trial": 3, "role": "agent"})
    tc = r.choices[0].message.tool_calls[0]
    assert tc.function.name == "get_order_details" and json.loads(tc.function.arguments) == {"order_id": "#W1"}
    assert r.choices[0].message.content is None
    assert f.calls[0]["sample"] == 3 and f.calls[0]["tag"] == "tau2:agent"


def test_adapter_retry_uses_new_sample_key(fake):
    f = fake([{"action": "nonsense"}, {"action": "message", "message": "ok"}])
    r = litellm.completion(model="claudecli/claude-haiku-5-5", messages=[{"role": "user", "content": "hi"}],
                           tools=TOOLS, metadata={"trial": 1, "role": "agent"})
    assert r.choices[0].message.content == "ok"
    assert [c["sample"] for c in f.calls] == [1, "1:retry1"]


def test_user_simulator_path_has_no_tools(fake):
    f = fake([{"message": "I want to return my order"}])
    r = litellm.completion(model="claudecli/claude-haiku-5-5", messages=[{"role": "system", "content": "You are a customer"},
                                                                          {"role": "user", "content": "Hi! How can I help?"}],
                           metadata={"trial": 0, "role": "user"})
    assert r.choices[0].message.content == "I want to return my order"
    assert "action" not in f.calls[0]["schema"]["properties"]


def test_parse_decodes_string_tool_call_once():
    t = parse({"action": "tool_call", "tool_call": '{"name": "get_order_details", "arguments": {"order_id": "#W1"}}'},
              ["get_order_details"], "s")
    assert t.tool_call["name"] == "get_order_details"
