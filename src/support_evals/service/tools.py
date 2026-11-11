"""Production tool layer. Reads are plain lookups; writes go propose -> customer confirm -> execute.

Invariants enforced here (not in the prompt), each tested in tests/service/:
- ownership: a write only touches orders of the server-verified principal (channel session), never a user the
  model claims or "identified" by name+zip;
- confirmation: execution needs a pending action created for the exact canonical payload, confirmed by the same
  principal, unexpired and single-use;
- idempotency: (principal, Idempotency-Key) -> stored response; same key + different request -> conflict;
- no double refund: UNIQUE(order_id, item_id) on refunds, checked inside one BEGIN IMMEDIATE transaction;
- limits: refunds above AUTO_REFUND_LIMIT per action or DAILY_REFUND_LIMIT per customer become handoff tickets.
"""

from __future__ import annotations

import ast
import hashlib
import json
import operator
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from support_evals.service.store import Store

AUTO_REFUND_LIMIT = 500.00
DAILY_REFUND_LIMIT = 1000.00
ACTION_TTL_S = 15 * 60
WRITE_TOOLS = {"cancel_pending_order", "return_delivered_order_items", "exchange_delivered_order_items"}
CANCEL_REASONS = {"no longer needed", "ordered by mistake"}


class ToolError(Exception):
    """Returned to the model as an error tool result (the conversation continues)."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


class Conflict(Exception):
    """HTTP 409: idempotency key reused with a different request, or action no longer executable."""


class Forbidden(Exception):
    """HTTP 403: principal does not own the resource."""


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@dataclass
class Session:
    conversation_id: str
    principal: str | None  # channel-authenticated user id (signed session token), or None for anonymous
    identified_user: str | None = None  # set by find_user_id_* (simulated identification, tau2 policy)

    @property
    def acting_user(self) -> str | None:
        return self.principal or self.identified_user


_OPS: dict[type, Callable[..., Any]] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                                       ast.Div: operator.truediv, ast.USub: operator.neg}


def safe_calc(expr: str) -> float:
    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Constant) and isinstance(n.value, int | float):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ToolError("only + - * / on numbers are allowed")
    return round(ev(ast.parse(expr, mode="eval").body), 2)


def random_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class CounterIds:
    """Deterministic ids for evaluation runs: prompts then contain no random ids, so runs replay from cache."""

    def __init__(self, seed: str):
        self.seed, self.n = seed, 0

    def __call__(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_{sha(f'{self.seed}|{self.n}')[:12]}"


class Tools:
    def __init__(self, store: Store, fault: Callable[[str], None] | None = None,
                 new_id: Callable[[str], str] = random_id):
        self.store = store
        self.fault = fault or (lambda point: None)  # test hook: raise at a named point to simulate a crash
        self.new_id = new_id

    # --- reads --------------------------------------------------------------------------------------------
    def read(self, s: Session, name: str, args: dict[str, Any]) -> Any:
        if name == "find_user_id_by_email":
            for u in self.store.all_users():
                if u["email"].lower() == str(args.get("email", "")).lower():
                    return self._identify(s, u["user_id"])
            raise ToolError("user not found", "not_found")
        if name == "find_user_id_by_name_zip":
            for u in self.store.all_users():
                if (u["name"]["first_name"].lower() == str(args.get("first_name", "")).lower()
                        and u["name"]["last_name"].lower() == str(args.get("last_name", "")).lower()
                        and u["address"]["zip"] == str(args.get("zip", ""))):
                    return self._identify(s, u["user_id"])
            raise ToolError("user not found", "not_found")
        if name == "calculate":
            return safe_calc(str(args.get("expression", "")))
        if name == "list_all_product_types":
            return self.store.product_types()
        if name == "get_product_details":
            p = self.store.product(str(args.get("product_id")))
            if p is None:
                raise ToolError("product not found", "not_found")
            return p
        if s.acting_user is None:
            raise ToolError("customer is not verified; ask for email or name + zip first", "unauthenticated")
        if name == "get_user_details":
            if args.get("user_id") != s.acting_user:
                raise ToolError("you can only access the verified customer's profile", "forbidden")
            return self.store.user(s.acting_user)
        if name == "get_order_details":
            o = self.store.order(str(args.get("order_id")))
            if o is None:
                raise ToolError("order not found", "not_found")
            if o["user_id"] != s.acting_user:
                # same message as not-found: do not confirm that another customer's order exists
                raise ToolError("order not found", "not_found")
            return o
        raise ToolError(f"unknown tool {name}")

    def _identify(self, s: Session, user_id: str) -> str:
        if s.principal is not None and s.principal != user_id:
            raise ToolError("this session is signed in as a different customer", "forbidden")
        s.identified_user = user_id
        return user_id

    # --- write path ---------------------------------------------------------------------------------------
    def validate_write(self, s: Session, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], str, float]:
        """Check a proposed write against current state. Returns (canonical payload, summary, refund amount)."""
        if os.environ.get("SE_WRITES_ENABLED", "1") == "0":  # kill switch, read per request (no deploy needed)
            raise ToolError("changes are temporarily paused; offer to transfer the customer to a human",
                            "writes_disabled")
        if s.principal is None:
            raise ToolError("changes need a signed-in customer; ask the customer to log in or hand off",
                            "login_required")
        oid = str(args.get("order_id", ""))
        o = self.store.order(oid)
        if o is None or o["user_id"] != s.principal:
            raise ToolError("order not found", "not_found")
        user = self.store.user(s.principal) or {}
        items = {i["item_id"]: i for i in o["items"]}
        if name == "cancel_pending_order":
            if o["status"] != "pending":
                raise ToolError(f"order status is '{o['status']}', only pending orders can be cancelled", "bad_status")
            reason = args.get("reason")
            if reason not in CANCEL_REASONS:
                raise ToolError("reason must be 'no longer needed' or 'ordered by mistake'")
            pm = o["payment_history"][0]["payment_method_id"]
            amount = round(sum(i["price"] for i in o["items"]), 2)
            payload = {"order_id": oid, "reason": reason, "item_ids": sorted(items), "refund_to": pm}
            return payload, f"Cancel order {oid} ({reason}); refund ${amount:.2f} to {pm}", amount
        item_ids = list(args.get("item_ids") or [])
        if not item_ids or len(set(item_ids)) != len(item_ids) or any(i not in items for i in item_ids):
            raise ToolError("item_ids must be distinct items of this order")
        if o["status"] != "delivered":
            raise ToolError(f"order status is '{o['status']}', only delivered orders can be returned or exchanged",
                            "bad_status")
        pm = str(args.get("payment_method_id", ""))
        if pm not in (user.get("payment_methods") or {}):
            raise ToolError("payment method does not belong to this customer")
        if name == "return_delivered_order_items":
            original = {p["payment_method_id"] for p in o["payment_history"]}
            if pm not in original and not pm.startswith("gift_card"):
                raise ToolError("refund must go to the original payment method or an existing gift card")
            amount = round(sum(items[i]["price"] for i in item_ids), 2)
            payload = {"order_id": oid, "item_ids": sorted(item_ids), "payment_method_id": pm}
            return payload, f"Return {len(item_ids)} item(s) from {oid}; refund ${amount:.2f} to {pm}", amount
        if name == "exchange_delivered_order_items":
            new_ids = list(args.get("new_item_ids") or [])
            if len(new_ids) != len(item_ids):
                raise ToolError("new_item_ids must match item_ids one to one")
            diff = 0.0
            for old, new in zip(item_ids, new_ids, strict=True):
                prod = self.store.product(items[old]["product_id"]) or {}
                var = (prod.get("variants") or {}).get(new)
                if var is None or not var.get("available"):
                    raise ToolError(f"{new} is not an available variant of the same product")
                diff += var["price"] - items[old]["price"]
            diff = round(diff, 2)
            payload = {"order_id": oid, "item_ids": item_ids, "new_item_ids": new_ids, "payment_method_id": pm}
            refund = max(0.0, -diff)
            return payload, f"Exchange {len(item_ids)} item(s) in {oid}; price difference ${diff:+.2f} via {pm}", refund
        raise ToolError(f"unknown write tool {name}")

    def propose(self, s: Session, name: str, args: dict[str, Any]) -> dict[str, Any]:
        payload, summary, amount = self.validate_write(s, name, args)
        action_id = self.new_id("act")
        c = self.store.conn()
        c.execute("INSERT INTO pending_actions VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                  (action_id, s.conversation_id, s.principal, name, canonical(payload), sha(canonical(payload)),
                   summary, "pending", time.time() + ACTION_TTL_S))
        return {"status": "awaiting_customer_confirmation", "action_id": action_id, "summary": summary,
                "refund_amount": amount}

    def cancel_action(self, principal: str, action_id: str) -> dict[str, Any]:
        c = self.store.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            r = c.execute("SELECT * FROM pending_actions WHERE action_id=?", (action_id,)).fetchone()
            if r is None or r["principal"] != principal:
                raise Forbidden("not your action")
            if r["status"] != "pending":
                raise Conflict(f"action is {r['status']}")
            c.execute("UPDATE pending_actions SET status='declined' WHERE action_id=?", (action_id,))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return {"status": "declined", "action_id": action_id}

    def confirm(self, principal: str, action_id: str, idem_key: str) -> dict[str, Any]:
        """Execute a confirmed action exactly once. Everything below runs in ONE serialised transaction."""
        request_hash = sha(canonical({"op": "confirm", "action_id": action_id}))
        c = self.store.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            prev = c.execute("SELECT request_hash, response FROM idempotency_keys WHERE principal=? AND key=?",
                             (principal, idem_key)).fetchone()
            if prev is not None:
                c.execute("ROLLBACK")
                if prev["request_hash"] != request_hash:
                    raise Conflict("Idempotency-Key reused with a different request")
                return {**json.loads(prev["response"]), "replayed": True}
            act = c.execute("SELECT * FROM pending_actions WHERE action_id=?", (action_id,)).fetchone()
            if act is None or act["principal"] != principal:
                raise Forbidden("not your action")
            if act["status"] != "pending":
                raise Conflict(f"action is {act['status']}")
            if time.time() > act["expires_at"]:
                c.execute("UPDATE pending_actions SET status='expired' WHERE action_id=?", (action_id,))
                c.execute("COMMIT")
                raise Conflict("action expired; propose it again")
            payload = json.loads(act["payload"])
            if sha(act["payload"]) != act["payload_hash"]:
                raise Conflict("payload changed after proposal")
            response = self._execute(c, principal, act["tool"], payload, action_id, act["conversation_id"])
            self.fault("before_commit")
            c.execute("INSERT INTO idempotency_keys (principal, key, request_hash, response) VALUES (?,?,?,?)",
                      (principal, idem_key, request_hash, canonical(response)))
            c.execute("UPDATE pending_actions SET status=?, result=? WHERE action_id=?",
                      ("escalated" if response["status"] == "escalated" else "executed", canonical(response),
                       action_id))
            c.execute("COMMIT")
            return response
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise

    def _execute(self, c, principal: str, tool: str, p: dict[str, Any], action_id: str, conv: str) -> dict[str, Any]:
        row = c.execute("SELECT * FROM orders WHERE order_id=?", (p["order_id"],)).fetchone()
        if row is None or row["user_id"] != principal:
            raise Forbidden("order not owned by principal")
        order = json.loads(row["doc"])
        items = {i["item_id"]: i for i in order["items"]}
        want = "pending" if tool == "cancel_pending_order" else "delivered"
        if row["status"] != want:
            raise Conflict(f"order is now '{row['status']}'")
        ids = p["item_ids"]
        done = c.execute(f"SELECT item_id FROM refunds WHERE order_id=? AND item_id IN ({','.join('?' * len(ids))})",
                         (p["order_id"], *ids)).fetchall()
        if done:
            raise Conflict(f"already refunded: {[r['item_id'] for r in done]}")
        amount = 0.0 if tool == "exchange_delivered_order_items" else round(sum(items[i]["price"] for i in ids), 2)
        today = c.execute("SELECT COALESCE(SUM(amount),0) s FROM refunds WHERE user_id=? AND created_at >= date('now')",
                          (principal,)).fetchone()["s"]
        if amount > AUTO_REFUND_LIMIT or today + amount > DAILY_REFUND_LIMIT:
            tid = self.new_id("tkt")
            reason = (f"refund ${amount:.2f} exceeds auto-approval limit ${AUTO_REFUND_LIMIT:.0f}"
                      if amount > AUTO_REFUND_LIMIT else
                      f"daily refunds would reach ${today + amount:.2f} > ${DAILY_REFUND_LIMIT:.0f}")
            c.execute("INSERT INTO handoff_tickets (ticket_id, conversation_id, customer, intent, actions_taken, reason,"
                      " priority, status) VALUES (?,?,?,?,?,?,?,?)",
                      (tid, conv, principal, f"{tool} {p['order_id']}", canonical(p), reason, "high", "open"))
            return {"status": "escalated", "ticket_id": tid, "reason": reason}
        new_status = {"cancel_pending_order": "cancelled", "return_delivered_order_items": "return requested",
                      "exchange_delivered_order_items": "exchange requested"}[tool]
        if tool == "exchange_delivered_order_items":
            for old, new in zip(p["item_ids"], p["new_item_ids"], strict=True):
                prod = json.loads(c.execute("SELECT doc FROM products WHERE product_id=?",
                                            (items[old]["product_id"],)).fetchone()["doc"])
                diff = round(prod["variants"][new]["price"] - items[old]["price"], 2)
                c.execute("INSERT INTO exchanges VALUES (?,?,?,?,?)", (p["order_id"], old, new, diff, action_id))
        else:
            pm = p.get("refund_to") or p["payment_method_id"]
            kind = "cancel" if tool == "cancel_pending_order" else "return"
            # cancel refunds are issued now; return refunds wait for the parcel (return requested != money sent)
            status = "issued" if kind == "cancel" else "pending_receipt"
            for i in ids:
                c.execute("INSERT INTO refunds (order_id, item_id, user_id, amount, payment_method_id, kind, status,"
                          " action_id) VALUES (?,?,?,?,?,?,?,?)",
                          (p["order_id"], i, principal, items[i]["price"], pm, kind, status, action_id))
        order["status"] = new_status
        c.execute("UPDATE orders SET status=?, version=version+1, doc=? WHERE order_id=?",
                  (new_status, json.dumps(order), p["order_id"]))
        return {"status": "executed", "order_id": p["order_id"], "new_order_status": new_status,
                "refund_amount": amount, "action_id": action_id}
