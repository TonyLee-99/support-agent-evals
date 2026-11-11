"""Service test fixtures: fresh SQLite store per test and a scripted LLM (no network)."""

import json

import pytest
from fastapi.testclient import TestClient

from support_evals.llm.base import LLMResponse
from support_evals.service import agent as agent_mod
from support_evals.service.app import create_app, sign
from support_evals.service.store import Store, load_seed
from support_evals.service.tools import Session, Tools

A = "aarav_santos_2259"          # delivered #W8528674, pending #W9672333
A_DELIVERED = "#W8528674"
A_ITEM = "4716977452"            # $289.69
A_ITEM2 = "5111440845"           # $48.55
A_PENDING = "#W9672333"          # total > $500 -> escalates on cancel
A_PM = "paypal_7664977"
C = "anya_patel_3710"           # two delivered orders: #W6174054 (item 8590708195 $157.61), #W6131421 ($256.14)
C_O1, C_I1, C_O2, C_I2, C_PM1, C_PM2 = "#W6174054", "8590708195", "#W6131421", "6509212169", "gift_card_6566420", \
    "credit_card_4142574"
B = "aarav_santos_4279"
B_ORDER = "#W8309293"


class ScriptedLLM:
    """Returns queued structured replies; records prompts. A callable entry gets the prompt and returns a reply."""

    backend, model = "scripted", "claude-haiku-5-5"

    def __init__(self):
        self.queue, self.prompts = [], []

    def push(self, *replies):
        self.queue.extend(replies)

    def complete(self, system, prompt, *, schema=None, sample=0, tag=""):
        self.prompts.append(prompt)
        r = self.queue.pop(0) if self.queue else {"action": "message", "message": "How else can I help?"}
        if callable(r):
            r = r(prompt)
        return LLMResponse(text=json.dumps(r), model=self.model, structured=r, input_tokens=1200, output_tokens=40,
                           cost_usd=0.0002, latency_s=0.01)


def tool(name, **args):
    return {"action": "tool_call", "tool_call": {"name": name, "arguments": args}}


def say(text):
    return {"action": "message", "message": text}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "svc.sqlite", seed=load_seed())


@pytest.fixture
def tools(store):
    return Tools(store)


@pytest.fixture
def llm(monkeypatch):
    s = ScriptedLLM()
    monkeypatch.setattr(agent_mod, "get_client", lambda model: s)
    return s


@pytest.fixture
def client(store, llm):
    return TestClient(create_app(store=store))


@pytest.fixture
def hdr():
    def h(user=A):
        return {"X-Session-Token": sign(user)}
    return h


def session(user=A, conv="conv_test"):
    return Session(conversation_id=conv, principal=user)


def propose_return(tools, user=A, order=A_DELIVERED, items=(A_ITEM,), pm=A_PM, conv="conv_test"):
    return tools.propose(session(user, conv), "return_delivered_order_items",
                         {"order_id": order, "item_ids": list(items), "payment_method_id": pm})
