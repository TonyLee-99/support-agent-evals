"""Races run with real threads against one SQLite file (separate connections), not mocks."""

import threading

import pytest

from support_evals.service import tools as tools_mod
from support_evals.service.tools import Conflict, Forbidden

from .conftest import A_DELIVERED, A_ITEM, C_I1, C_I2, C_O1, C_O2, C_PM1, C_PM2, A, C, propose_return


def _race(fns):
    results, barrier = [None] * len(fns), threading.Barrier(len(fns))

    def run(i, fn):
        barrier.wait()
        try:
            results[i] = ("ok", fn())
        except (Conflict, Forbidden) as e:
            results[i] = ("conflict", str(e))

    ts = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(fns)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    return results


def test_32_concurrent_refunds_same_item_exactly_one(tools, store):
    acts = [propose_return(tools, conv=f"c{i}") for i in range(32)]
    res = _race([lambda a=a, i=i: tools.confirm(A, a["action_id"], f"key-{i}") for i, a in enumerate(acts)])
    assert sum(1 for k, _ in res if k == "ok") == 1
    assert len(store.refunds(A_DELIVERED)) == 1
    assert store.conn().execute("SELECT version FROM orders WHERE order_id=?", (A_DELIVERED,)).fetchone()[0] == 1


def test_same_key_race_single_effect_same_response(tools, store):
    act = propose_return(tools)
    res = _race([lambda: tools.confirm(A, act["action_id"], "same-key") for _ in range(16)])
    assert all(k == "ok" for k, _ in res)
    assert len({r["action_id"] for _, r in res}) == 1 and sum(1 for _, r in res if not r.get("replayed")) == 1
    assert len(store.refunds()) == 1


def test_concurrent_different_items_respect_daily_limit(tools, store, monkeypatch):
    monkeypatch.setattr(tools_mod, "DAILY_REFUND_LIMIT", 400.0)
    a1 = propose_return(tools, C, C_O1, (C_I1,), C_PM1)
    a2 = propose_return(tools, C, C_O2, (C_I2,), C_PM2)
    res = _race([lambda: tools.confirm(C, a1["action_id"], "k1"), lambda: tools.confirm(C, a2["action_id"], "k2")])
    assert sorted(r["status"] for _, r in res) == ["escalated", "executed"]
    assert sum(r["amount"] for r in store.refunds()) <= 400.0


def test_confirm_vs_decline_race_exactly_one_outcome(tools, store):
    act = propose_return(tools)
    res = _race([lambda: tools.confirm(A, act["action_id"], "k"), lambda: tools.cancel_action(A, act["action_id"])])
    assert sum(1 for k, _ in res if k == "ok") == 1
    status = store.conn().execute("SELECT status FROM pending_actions WHERE action_id=?",
                                  (act["action_id"],)).fetchone()[0]
    assert (status == "executed") == (len(store.refunds()) == 1)


def test_crash_before_commit_rolls_back_and_retry_succeeds(store):
    crashing = tools_mod.Tools(store, fault=lambda p: (_ for _ in ()).throw(RuntimeError("crash")) if p == "before_commit" else None)
    act = propose_return(crashing)
    with pytest.raises(RuntimeError):
        crashing.confirm(A, act["action_id"], "k1")
    assert store.refunds() == [] and store.order(A_DELIVERED)["status"] == "delivered"
    r = tools_mod.Tools(store).confirm(A, act["action_id"], "k1")  # client retries with the same key
    assert r["status"] == "executed" and len(store.refunds()) == 1
    assert A_ITEM in {x["item_id"] for x in store.refunds()}
