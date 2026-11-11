from .conftest import A_DELIVERED, say, tool


def test_trace_has_llm_tool_spans_and_totals(client, hdr, llm):
    llm.push(tool("get_order_details", order_id=A_DELIVERED), say("Your order was delivered."))
    cid = client.post("/v1/conversations", json={}, headers=hdr()).json()["conversation_id"]
    client.post(f"/v1/conversations/{cid}/messages", json={"text": f"status of {A_DELIVERED}?"}, headers=hdr())
    tr = client.get(f"/v1/conversations/{cid}/trace", headers=hdr()).json()
    kinds = [s["kind"] for s in tr["spans"]]
    assert kinds.count("llm") == 2 and kinds.count("tool") == 1 and "guard" in kinds
    t = tr["totals"]
    assert t["llm_calls"] == 2 and t["input_tokens"] == 2400 and t["output_tokens"] == 80
    assert abs(t["cost_usd"] - 0.0004) < 1e-9 and t["tool_calls"] == 1
    tool_span = next(s for s in tr["spans"] if s["kind"] == "tool")
    assert tool_span["name"] == "get_order_details" and tool_span["ok"] is True and tool_span["latency_ms"] >= 0
    assert all("turn" in s for s in tr["spans"])


def test_confirmation_span_records_outcome(client, hdr, llm):
    from .conftest import A_ITEM, A_PM
    llm.push(tool("return_delivered_order_items", order_id=A_DELIVERED, item_ids=[A_ITEM], payment_method_id=A_PM),
             say("Please confirm."), say("Done."))
    cid = client.post("/v1/conversations", json={}, headers=hdr()).json()["conversation_id"]
    card = [x for x in client.post(f"/v1/conversations/{cid}/messages", json={"text": "return"}, headers=hdr()).json()["replies"]
            if x["role"] == "confirmation"][0]
    client.post(f"/v1/conversations/{cid}/actions/{card['action_id']}/confirm", headers={**hdr(), "Idempotency-Key": "k"})
    span = next(s for s in client.get(f"/v1/conversations/{cid}/trace", headers=hdr()).json()["spans"] if s["name"] == "confirm")
    assert span["ok"] is True and span["status"] == "executed" and span["refund_amount"] == 289.69
