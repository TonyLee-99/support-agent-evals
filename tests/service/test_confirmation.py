import time

from .conftest import A_DELIVERED, A_ITEM, A_PM, A, propose_return, say, tool


def _start(client, hdr):
    return client.post("/v1/conversations", json={}, headers=hdr()).json()["conversation_id"]


def test_write_tool_creates_card_not_effect(client, hdr, llm, store):
    llm.push(tool("return_delivered_order_items", order_id=A_DELIVERED, item_ids=[A_ITEM], payment_method_id=A_PM),
             say("I've prepared the return. Please confirm."))
    cid = _start(client, hdr)
    r = client.post(f"/v1/conversations/{cid}/messages", json={"text": "return the item"}, headers=hdr()).json()
    card = [x for x in r["replies"] if x["role"] == "confirmation"][0]
    assert "$289.69" in card["summary"] and store.refunds() == []
    # Typing "yes" is not a confirmation: only the confirm endpoint executes.
    llm.push(say("Please use the Confirm button."))
    client.post(f"/v1/conversations/{cid}/messages", json={"text": "YES do it now"}, headers=hdr())
    assert store.refunds() == []
    llm.push(say("Done - your return is requested."))
    url = f"/v1/conversations/{cid}/actions/{card['action_id']}/confirm"
    assert client.post(url, headers=hdr()).status_code == 400  # Idempotency-Key required
    ok = client.post(url, headers={**hdr(), "Idempotency-Key": "abc"})
    assert ok.status_code == 200 and len(store.refunds()) == 1
    again = client.post(url, headers={**hdr(), "Idempotency-Key": "abc"})
    assert again.status_code == 200 and again.json()["replies"] == [] and len(store.refunds()) == 1
    assert client.post(url, headers={**hdr(), "Idempotency-Key": "other"}).status_code == 409


def test_anonymous_cannot_confirm(client, hdr, llm, tools):
    cid = client.post("/v1/conversations", json={}).json()["conversation_id"]
    assert client.post(f"/v1/conversations/{cid}/actions/act_x/confirm",
                       headers={"Idempotency-Key": "k"}).status_code == 401


def test_expired_action_rejected(tools, store):
    act = propose_return(tools)
    store.conn().execute("UPDATE pending_actions SET expires_at=? WHERE action_id=?", (time.time() - 1, act["action_id"]))
    import pytest

    from support_evals.service.tools import Conflict
    with pytest.raises(Conflict):
        tools.confirm(A, act["action_id"], "k")
    assert store.refunds() == []


def test_tampered_payload_rejected(tools, store):
    act = propose_return(tools)
    store.conn().execute("UPDATE pending_actions SET payload=replace(payload, ?, ?) WHERE action_id=?",
                         (A_ITEM, "5111440845", act["action_id"]))
    import pytest

    from support_evals.service.tools import Conflict
    with pytest.raises(Conflict):
        tools.confirm(A, act["action_id"], "k")
    assert store.refunds() == []


def test_decline_records_and_blocks_execution(tools, store):
    act = propose_return(tools)
    tools.cancel_action(A, act["action_id"])
    import pytest

    from support_evals.service.tools import Conflict
    with pytest.raises(Conflict):
        tools.confirm(A, act["action_id"], "k")


def test_kill_switch_blocks_new_writes(tools, monkeypatch):
    import pytest

    from support_evals.service.tools import ToolError
    monkeypatch.setenv("SE_WRITES_ENABLED", "0")
    with pytest.raises(ToolError) as e:
        propose_return(tools)
    assert e.value.code == "writes_disabled"
