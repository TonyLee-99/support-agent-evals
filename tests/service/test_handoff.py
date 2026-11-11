from .conftest import A, tool

OPS = {"X-Ops-Token": "ops-dev"}


def test_transfer_creates_structured_ticket_and_queue_flow(client, hdr, llm, store):
    llm.push(tool("transfer_to_human_agents",
                  summary=f"customer: {A}; intent: dispute a charge; done: looked up order; reason: billing dispute"))
    cid = client.post("/v1/conversations", json={}, headers=hdr()).json()["conversation_id"]
    r = client.post(f"/v1/conversations/{cid}/messages", json={"text": "I want to dispute a charge"}, headers=hdr()).json()
    assert r["state"] == "handed_off"
    assert r["replies"][-1]["content"] == "YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON."
    assert client.get("/v1/handoffs").status_code == 401
    q = client.get("/v1/handoffs", headers=OPS).json()
    assert len(q) == 1 and q[0]["customer"] == A and q[0]["intent"] == "dispute a charge"
    assert q[0]["reason"] == "billing dispute" and q[0]["actions_taken"] == "looked up order"
    tid = q[0]["ticket_id"]
    assert client.post(f"/v1/handoffs/{tid}/resolve", json={"resolution": "x"}, headers=OPS).status_code == 409
    assert client.post(f"/v1/handoffs/{tid}/claim", json={"agent": "sam"}, headers=OPS).json()["status"] == "claimed"
    assert client.post(f"/v1/handoffs/{tid}/claim", json={"agent": "kim"}, headers=OPS).status_code == 409
    assert client.post(f"/v1/handoffs/{tid}/resolve", json={"resolution": "refunded"}, headers=OPS).status_code == 200
    # once handed off, the bot stays out of the conversation
    after = client.post(f"/v1/conversations/{cid}/messages", json={"text": "hello?"}, headers=hdr()).json()
    assert "human agent" in after["replies"][0]["content"]


def test_unstructured_summary_kept_as_reason(client, hdr, llm):
    llm.push(tool("transfer_to_human_agents", summary="customer is upset about a late delivery"))
    cid = client.post("/v1/conversations", json={}, headers=hdr()).json()["conversation_id"]
    client.post(f"/v1/conversations/{cid}/messages", json={"text": "where is my stuff"}, headers=hdr())
    t = client.get("/v1/handoffs", headers=OPS).json()[0]
    assert t["reason"] == "customer is upset about a late delivery" and t["customer"] == A
