import pytest

from support_evals.service import tools as tools_mod
from support_evals.service.tools import Conflict

from .conftest import (
    A_DELIVERED,
    A_ITEM2,
    A_PENDING,
    C_I1,
    C_I2,
    C_O1,
    C_O2,
    C_PM1,
    C_PM2,
    A,
    C,
    propose_return,
    session,
)


def test_same_key_same_request_replays_without_second_effect(tools, store):
    act = propose_return(tools)
    r1 = tools.confirm(A, act["action_id"], "key-1")
    r2 = tools.confirm(A, act["action_id"], "key-1")
    assert r1["status"] == "executed" and r2["replayed"] is True and r2["refund_amount"] == r1["refund_amount"]
    assert len(store.refunds(A_DELIVERED)) == 1


def test_same_key_different_request_conflicts(tools):
    a1 = propose_return(tools)
    a2 = propose_return(tools, items=(A_ITEM2,))
    tools.confirm(A, a1["action_id"], "key-1")
    with pytest.raises(Conflict):
        tools.confirm(A, a2["action_id"], "key-1")


def test_executed_action_cannot_run_again_with_new_key(tools, store):
    act = propose_return(tools)
    tools.confirm(A, act["action_id"], "k1")
    with pytest.raises(Conflict):
        tools.confirm(A, act["action_id"], "k2")
    assert len(store.refunds()) == 1


def test_second_action_on_same_item_is_rejected(tools, store):
    a1, a2 = propose_return(tools), propose_return(tools)
    tools.confirm(A, a1["action_id"], "k1")
    with pytest.raises(Conflict):
        tools.confirm(A, a2["action_id"], "k2")
    assert len(store.refunds()) == 1


def test_refund_over_auto_limit_escalates(tools, store):
    act = tools.propose(session(A), "cancel_pending_order", {"order_id": A_PENDING, "reason": "no longer needed"})
    assert act["refund_amount"] > tools_mod.AUTO_REFUND_LIMIT
    r = tools.confirm(A, act["action_id"], "k1")
    assert r["status"] == "escalated" and r["ticket_id"].startswith("tkt_")
    assert store.refunds() == [] and store.order(A_PENDING)["status"] == "pending"
    t = store.conn().execute("SELECT * FROM handoff_tickets").fetchone()
    assert t["priority"] == "high" and "auto-approval" in t["reason"]


def test_daily_limit(tools, store, monkeypatch):
    monkeypatch.setattr(tools_mod, "DAILY_REFUND_LIMIT", 400.0)
    r1 = tools.confirm(C, propose_return(tools, C, C_O2, (C_I2,), C_PM2)["action_id"], "k1")  # 256.14
    r2 = tools.confirm(C, propose_return(tools, C, C_O1, (C_I1,), C_PM1)["action_id"], "k2")  # +157.61 > 400
    assert r1["status"] == "executed" and r2["status"] == "escalated"
    assert sum(r["amount"] for r in store.refunds()) == pytest.approx(256.14)


def test_return_is_pending_receipt_not_money_sent(tools, store):
    tools.confirm(A, propose_return(tools)["action_id"], "k1")
    assert store.refunds()[0]["status"] == "pending_receipt"
    assert store.order(A_DELIVERED)["status"] == "return requested"
