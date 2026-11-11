"""Build tests/fixtures/retail_small.json: 30 users (with all their orders) + all 50 products from tau2 retail db.json.

Users are picked with a fixed seed among users who have both a pending and a delivered order, so every scenario
type (cancel, return, exchange) is possible. The same excerpt is the only DB view given to the blind scenario writer.
"""

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "third_party/tau2-bench/data/tau2/domains/retail/db.json"
OUT = ROOT / "tests/fixtures/retail_small.json"

db = json.loads(SRC.read_text())
by_user: dict[str, list[dict]] = {}
for o in db["orders"].values():
    by_user.setdefault(o["user_id"], []).append(o)
eligible = sorted(u for u, os in by_user.items()
                  if {"pending", "delivered"} <= {o["status"] for o in os})
chosen = sorted(random.Random(7).sample(eligible, 30))
out = {
    "source": "tau2-bench retail db.json @ c06b07dd (MIT); 30-user excerpt, seed 7",
    "users": {u: db["users"][u] for u in chosen},
    "orders": {o["order_id"]: o for u in chosen for o in by_user[u]},
    "products": db["products"],
}
OUT.write_text(json.dumps(out, indent=1))
print(len(eligible), "eligible;", len(out["users"]), "users,", len(out["orders"]), "orders")
