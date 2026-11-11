import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FX = json.loads((ROOT / "tests/fixtures/retail_small.json").read_text())
HOLD = json.loads((ROOT / "scenarios/holdout.json").read_text())
REQ = {"id", "author", "category", "channel", "customer", "persona", "instructions", "expect", "rubric"}
EXPECT = {"refund_count", "max_refund_total", "order_status", "handoff", "forbidden_tools", "must_not_reveal"}


def load(name):
    return [json.loads(line) for line in (ROOT / "scenarios" / name).read_text().splitlines() if line.strip()]


@pytest.mark.parametrize("name,author,prefix", [("self_50.jsonl", "self", "S"), ("codex_50.jsonl", "codex", "C")])
def test_schema_and_integrity(name, author, prefix):
    sc = load(name)
    assert len(sc) == 50 and len({s["id"] for s in sc}) == 50
    for s in sc:
        assert REQ <= set(s), s["id"]
        assert s["author"] == author and s["id"].startswith(prefix)
        assert s["channel"] in ("signed_in", "guest")
        assert s["customer"] in FX["users"], s["id"]
        assert EXPECT <= set(s["expect"]), s["id"]
        for oid in s["expect"]["order_status"]:
            assert oid in FX["orders"], (s["id"], oid)
        for _oid, o in (s.get("overlay") or {}).get("orders", {}).items():
            assert o["user_id"] == s["customer"], "overlays may only touch the customer's own orders"


def test_holdout_frozen_and_balanced():
    assert len(HOLD["self"]) == 20 and len(HOLD["codex"]) == 20
    assert sorted(HOLD["self"] + HOLD["codex"]) == HOLD["ids"]
    assert "S001" not in HOLD["ids"] and "S015" not in HOLD["ids"]  # used in the smoke test
    for f, digest in HOLD["sha256"].items():
        assert hashlib.sha256((ROOT / "scenarios" / f).read_bytes()).hexdigest() == digest, f"{f} changed after freeze"
