import json

import pytest
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage
from tau2.domains.retail.environment import get_environment

from support_evals.bench.agents import ALL_FLAGS, PolicyAgent, has_fresh_affirmation, is_affirmation, make_factory

ORDER = {"order_id": "#W1", "user_id": "u1", "status": "delivered", "items": [
    {"item_id": "i1", "product_id": "p1", "price": 10.0}], "payment_history": [{"payment_method_id": "card_1"}]}
PRODUCT = {"product_id": "p1", "variants": {"i2": {"item_id": "i2", "available": True},
                                            "i3": {"item_id": "i3", "available": False}}}


def call(name, args, cid):
    return AssistantMessage(role="assistant", content=None, tool_calls=[ToolCall(id=cid, name=name, arguments=args)])


def result(cid, content):
    return ToolMessage(id=cid, role="tool", content=content if isinstance(content, str) else json.dumps(content))


@pytest.fixture(scope="module")
def agent():
    env = get_environment()
    return PolicyAgent(tools=env.get_tools(), domain_policy=env.get_policy(), llm="claudecli/x", llm_args={})


def authed(extra=()):
    return [call("find_user_id_by_email", {"email": "a@b.c"}, "c0"), result("c0", "u1"),
            call("get_order_details", {"order_id": "#W1"}, "c1"), result("c1", ORDER),
            call("get_product_details", {"product_id": "p1"}, "c2"), result("c2", PRODUCT), *extra]


RETURN = ("return_delivered_order_items", {"order_id": "#W1", "item_ids": ["i1"], "payment_method_id": "card_1"})


def test_factory_drops_task():
    env = get_environment()
    a = make_factory(frozenset(ALL_FLAGS))(env.get_tools(), env.get_policy(), llm="x", llm_args={}, task="SECRET")
    assert "SECRET" not in a.system_prompt and not hasattr(a, "task")


def test_identity_gate_blocks_lookup_before_auth(agent):
    msg = call("get_order_details", {"order_id": "#W1"}, "x")
    assert "not authenticated" in agent.check(msg, [UserMessage(role="user", content="hi")], None)


def test_identity_gate_blocks_other_users_order(agent):
    other = dict(ORDER, user_id="someone_else")
    h = [call("find_user_id_by_email", {"email": "a"}, "c0"), result("c0", "u1"),
         call("get_order_details", {"order_id": "#W1"}, "c1"), result("c1", other),
         UserMessage(role="user", content="yes")]
    assert "different user" in agent.check(call(*RETURN, "x"), h, None)


def test_confirm_gate_requires_fresh_yes(agent):
    h = authed([UserMessage(role="user", content="return it please")])
    assert "confirmation" in agent.check(call(*RETURN, "x"), h, None)
    h_yes = authed([UserMessage(role="user", content="Yes, go ahead.")])
    assert agent.check(call(*RETURN, "x"), h_yes, None) is None
    # the same yes cannot authorise a second write
    h_used = h_yes + [call(*RETURN, "w1"), result("w1", ORDER)]
    assert "confirmation" in agent.check(call(*RETURN, "y"), h_used, None)


def test_arg_check_catches_wrong_ids_and_status(agent):
    h = authed([UserMessage(role="user", content="yes")])
    bad_item = call("return_delivered_order_items", {"order_id": "#W1", "item_ids": ["zzz"], "payment_method_id": "card_1"}, "x")
    assert "not in order" in agent.check(bad_item, h, None)
    bad_pm = call("return_delivered_order_items", {"order_id": "#W1", "item_ids": ["i1"], "payment_method_id": "paypal_9"}, "x")
    assert "payment method" in agent.check(bad_pm, h, None)
    cancel = call("cancel_pending_order", {"order_id": "#W1", "reason": "no longer needed"}, "x")
    assert "pending" in agent.check(cancel, h, None)
    unavailable = call("exchange_delivered_order_items", {"order_id": "#W1", "item_ids": ["i1"], "new_item_ids": ["i3"],
                                                           "payment_method_id": "card_1"}, "x")
    assert "not available" in agent.check(unavailable, h, None)
    missing = call("get_order_details", {}, "x")
    assert "missing argument" in agent.check(missing, h, None)


def test_flags_off_means_no_guard():
    env = get_environment()
    a = PolicyAgent(tools=env.get_tools(), domain_policy=env.get_policy(), llm="x", llm_args={}, flags=frozenset())
    assert a.check(call(*RETURN, "x"), [], None) is None
    assert "<checklist>" not in a.system_prompt


@pytest.mark.parametrize("text,ok", [("yes", True), ("Yes please proceed", True), ("No, wait", False),
                                     ("I'm not sure", False), ("what are the options?", False)])
def test_affirmation(text, ok):
    assert is_affirmation(text) is ok


def test_fresh_affirmation_allows_lookup_after_yes():
    h = [UserMessage(role="user", content="yes"), call("get_product_details", {"product_id": "p1"}, "c"), result("c", PRODUCT)]
    assert has_fresh_affirmation(h)
