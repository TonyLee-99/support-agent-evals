from support_evals.bench.native import to_anthropic, to_tools


def test_conversion_alternates_and_maps_tool_use():
    msgs = [{"role": "system", "content": "POLICY"}, {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "order #W1"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "function": {
                "name": "get_order_details", "arguments": '{"order_id": "#W1"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"status": "pending"}'},
            {"role": "system", "content": "blocked: confirm first"}]
    system, out = to_anthropic(msgs)
    assert system[0]["text"] == "POLICY" and system[-1]["cache_control"] == {"type": "ephemeral"}
    assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant", "user"]
    assert out[3]["content"][0] == {"type": "tool_use", "id": "call_1", "name": "get_order_details",
                                    "input": {"order_id": "#W1"}}
    assert out[4]["content"][0]["type"] == "tool_result" and "[system note]" in out[4]["content"][1]["text"]


def test_tools_conversion_caches_prefix():
    t = to_tools([{"type": "function", "function": {"name": "f", "description": "d",
                                                     "parameters": {"type": "object", "properties": {}}}}])
    assert t[0]["input_schema"]["type"] == "object" and t[-1]["cache_control"] == {"type": "ephemeral"}
