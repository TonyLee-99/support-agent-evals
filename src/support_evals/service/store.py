"""SQLite store on the tau2 retail data model (users, orders, products) plus production tables.

Why SQLite: one file, real transactions (`BEGIN IMMEDIATE` serialises writers), unique constraints - enough to
prove the money invariants (no double refund, idempotent replays) without running a database server.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from support_evals.llm.cache import ROOT

FIXTURE = ROOT / "tests" / "fixtures" / "retail_small.json"
FULL_DB = ROOT / "third_party" / "tau2-bench" / "data" / "tau2" / "domains" / "retail" / "db.json"

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products (product_id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
  doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refunds (
  refund_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, item_id TEXT NOT NULL, user_id TEXT NOT NULL,
  amount REAL NOT NULL, payment_method_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
  action_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(order_id, item_id));  -- the double-refund invariant lives in the schema, not in code
CREATE TABLE IF NOT EXISTS exchanges (
  order_id TEXT NOT NULL, item_id TEXT NOT NULL, new_item_id TEXT NOT NULL, price_difference REAL NOT NULL,
  action_id TEXT NOT NULL, UNIQUE(order_id, item_id));
CREATE TABLE IF NOT EXISTS idempotency_keys (
  principal TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL, response TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')), PRIMARY KEY (principal, key));
CREATE TABLE IF NOT EXISTS pending_actions (
  action_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, principal TEXT NOT NULL, tool TEXT NOT NULL,
  payload TEXT NOT NULL, payload_hash TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL,
  expires_at REAL NOT NULL, result TEXT);
CREATE TABLE IF NOT EXISTS handoff_tickets (
  ticket_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, customer TEXT, intent TEXT, actions_taken TEXT,
  reason TEXT NOT NULL, priority TEXT NOT NULL, status TEXT NOT NULL, claimed_by TEXT, resolution TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS conversations (
  conversation_id TEXT PRIMARY KEY, principal TEXT, identified_user TEXT, state TEXT NOT NULL,
  agent_version TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS messages (
  conversation_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL, doc TEXT NOT NULL,
  PRIMARY KEY (conversation_id, seq));
CREATE TABLE IF NOT EXISTS spans (
  conversation_id TEXT NOT NULL, seq INTEGER NOT NULL, turn INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
  latency_ms REAL NOT NULL, doc TEXT NOT NULL);
"""


def load_seed(path: Path | None = None) -> dict[str, Any]:
    p = path or FIXTURE  # the 30-user excerpt keeps scenario runs and tests on the same, committed data
    return json.loads(p.read_text())


class Store:
    """Thread-safe: one sqlite connection per thread, all sharing the same database file."""

    def __init__(self, path: Path | str, seed: dict[str, Any] | None = None, overlay: dict[str, Any] | None = None):
        self.path = str(path)
        self._tls = threading.local()
        conn = self.conn()
        conn.executescript(SCHEMA)
        if seed is not None and not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            self._seed(seed, overlay or {})

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._tls, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA busy_timeout=30000")
            self._tls.conn = c
        return c

    def _seed(self, seed: dict[str, Any], overlay: dict[str, Any]) -> None:
        users = {**seed["users"], **overlay.get("users", {})}
        orders = {**seed["orders"], **overlay.get("orders", {})}
        products = {**seed["products"], **overlay.get("products", {})}
        c = self.conn()
        c.execute("BEGIN")
        c.executemany("INSERT INTO users VALUES (?, ?)", [(k, json.dumps(v)) for k, v in users.items()])
        c.executemany("INSERT INTO products VALUES (?, ?)", [(k, json.dumps(v)) for k, v in products.items()])
        c.executemany("INSERT INTO orders (order_id, user_id, status, doc) VALUES (?, ?, ?, ?)",
                      [(k, v["user_id"], v["status"], json.dumps(v)) for k, v in orders.items()])
        c.execute("COMMIT")

    # --- reads --------------------------------------------------------------------------------------------
    def user(self, user_id: str) -> dict[str, Any] | None:
        r = self.conn().execute("SELECT doc FROM users WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(r["doc"]) if r else None

    def order(self, order_id: str) -> dict[str, Any] | None:
        r = self.conn().execute("SELECT doc, status FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not r:
            return None
        d = json.loads(r["doc"])
        d["status"] = r["status"]
        return d

    def product(self, product_id: str) -> dict[str, Any] | None:
        r = self.conn().execute("SELECT doc FROM products WHERE product_id=?", (product_id,)).fetchone()
        return json.loads(r["doc"]) if r else None

    def all_users(self):
        for r in self.conn().execute("SELECT doc FROM users"):
            yield json.loads(r["doc"])

    def product_types(self) -> dict[str, str]:
        out = {}
        for r in self.conn().execute("SELECT doc FROM products"):
            d = json.loads(r["doc"])
            out[d["name"]] = d["product_id"]
        return dict(sorted(out.items()))

    def refunds(self, order_id: str | None = None) -> list[dict[str, Any]]:
        q, a = ("SELECT * FROM refunds WHERE order_id=?", (order_id,)) if order_id else ("SELECT * FROM refunds", ())
        return [dict(r) for r in self.conn().execute(q, a)]
