"""FastAPI chat service: conversations, confirmation cards, handoff queue, traces, widget.

Auth model (see docs/DESIGN.md section 9):
- Customers carry `X-Session-Token`, an HMAC-signed `user_id` issued by the host site's login (here
  `/v1/demo/login`, which stands in for the retailer's real login). No token = anonymous session: it may be
  identified by name+zip / email (tau2's policy - simulated identification) for read-only help, never for writes.
- Ops endpoints (handoff queue) need `X-Ops-Token` (env SE_OPS_TOKEN).
Tokens travel in headers, not cookies, so there is no CSRF surface on /confirm.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from support_evals.service.agent import Engine
from support_evals.service.store import Store, load_seed
from support_evals.service.tools import Conflict, Forbidden

WIDGET_DIR = Path(__file__).resolve().parents[3] / "web" / "widget"


def _secret() -> bytes:
    return os.environ.get("SE_SESSION_SECRET", "dev-only-secret-change-me").encode()


def sign(user_id: str) -> str:
    mac = hmac.new(_secret(), user_id.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{user_id}.{mac}"


def verify(token: str | None) -> str | None:
    if not token:
        return None
    user_id, _, mac = token.rpartition(".")
    good = hmac.new(_secret(), user_id.encode(), hashlib.sha256).hexdigest()[:32]
    if not user_id or not hmac.compare_digest(mac, good):
        raise HTTPException(401, "invalid session token")
    return user_id


class NewConversation(BaseModel):
    agent_version: str = "v3"  # the version the regression gate passed


class UserText(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Claim(BaseModel):
    agent: str


class Resolve(BaseModel):
    resolution: str


class Login(BaseModel):
    user_id: str


def create_app(store: Store | None = None, engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="support-agent-evals service", version="0.1.0")
    st = store or Store(os.environ.get("SE_DB", "data/service.sqlite"), seed=load_seed())
    eng = engine or Engine(st)

    def principal(x_session_token: str | None = Header(default=None)) -> str | None:
        return verify(x_session_token)

    def ops(x_ops_token: str | None = Header(default=None)) -> None:
        if not x_ops_token or not hmac.compare_digest(x_ops_token, os.environ.get("SE_OPS_TOKEN", "ops-dev")):
            raise HTTPException(401, "ops token required")

    def own(cid: str, who: str | None) -> dict[str, Any]:
        try:
            conv = eng.conversation(cid)
        except KeyError:
            raise HTTPException(404, "conversation not found") from None
        if conv["principal"] != who:  # conversations (and their traces) are only visible to their owner
            raise HTTPException(404, "conversation not found")
        return conv

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        st.conn().execute("SELECT 1")
        return {"status": "ok"}

    @app.post("/v1/demo/login")
    def demo_login(body: Login) -> dict[str, str]:
        if os.environ.get("SE_DEMO_LOGIN", "1") != "1":
            raise HTTPException(404)
        if st.user(body.user_id) is None:
            raise HTTPException(404, "unknown user")
        return {"session_token": sign(body.user_id)}

    @app.post("/v1/conversations")
    def new_conversation(body: NewConversation, who: str | None = Depends(principal)) -> dict[str, Any]:
        cid = eng.create_conversation(who, body.agent_version, sample=uuid.uuid4().hex[:8])
        return {"conversation_id": cid, "messages": eng.messages(cid), "authenticated": who is not None}

    @app.get("/v1/conversations/{cid}")
    def get_conversation(cid: str, who: str | None = Depends(principal)) -> dict[str, Any]:
        conv = own(cid, who)
        visible = [m for m in eng.messages(cid) if m["role"] in ("user", "assistant") and m.get("content")]
        return {"conversation_id": cid, "state": conv["state"], "messages": visible}

    @app.post("/v1/conversations/{cid}/messages")
    def post_message(cid: str, body: UserText, who: str | None = Depends(principal)) -> dict[str, Any]:
        own(cid, who)
        out = eng.user_message(cid, body.text)
        return {"replies": out, "state": eng.conversation(cid)["state"]}

    def _action(cid: str, action_id: str, confirmed: bool, who: str | None, key: str | None) -> dict[str, Any]:
        own(cid, who)
        if who is None:
            raise HTTPException(401, "sign in to confirm actions")
        if confirmed and not key:
            raise HTTPException(400, "Idempotency-Key header required")
        try:
            out = eng.action_event(cid, action_id, confirmed, key or f"decline:{action_id}")
        except Forbidden as e:
            raise HTTPException(403, str(e)) from None
        except Conflict as e:
            raise HTTPException(409, str(e)) from None
        return {"replies": out, "state": eng.conversation(cid)["state"]}

    @app.post("/v1/conversations/{cid}/actions/{action_id}/confirm")
    def confirm(cid: str, action_id: str, who: str | None = Depends(principal),
                idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
        return _action(cid, action_id, True, who, idempotency_key)

    @app.post("/v1/conversations/{cid}/actions/{action_id}/decline")
    def decline(cid: str, action_id: str, who: str | None = Depends(principal)) -> dict[str, Any]:
        return _action(cid, action_id, False, who, None)

    @app.get("/v1/conversations/{cid}/trace")
    def trace(cid: str, who: str | None = Depends(principal)) -> dict[str, Any]:
        own(cid, who)
        return eng.trace(cid)

    # --- handoff queue (ops) ----------------------------------------------------------------------------
    @app.get("/v1/handoffs", dependencies=[Depends(ops)])
    def list_handoffs(status: str = "open") -> list[dict[str, Any]]:
        rows = st.conn().execute("SELECT * FROM handoff_tickets WHERE status=? ORDER BY created_at", (status,))
        return [dict(r) for r in rows]

    @app.post("/v1/handoffs/{tid}/claim", dependencies=[Depends(ops)])
    def claim(tid: str, body: Claim) -> dict[str, Any]:
        cur = st.conn().execute("UPDATE handoff_tickets SET status='claimed', claimed_by=? WHERE ticket_id=? AND "
                                "status='open'", (body.agent, tid))
        if cur.rowcount != 1:
            raise HTTPException(409, "ticket not open")
        return {"ticket_id": tid, "status": "claimed", "claimed_by": body.agent}

    @app.post("/v1/handoffs/{tid}/resolve", dependencies=[Depends(ops)])
    def resolve(tid: str, body: Resolve) -> dict[str, Any]:
        cur = st.conn().execute("UPDATE handoff_tickets SET status='resolved', resolution=? WHERE ticket_id=? AND "
                                "status='claimed'", (body.resolution, tid))
        if cur.rowcount != 1:
            raise HTTPException(409, "ticket must be claimed first")
        return {"ticket_id": tid, "status": "resolved"}

    # --- widget -------------------------------------------------------------------------------------------
    @app.get("/widget", response_class=HTMLResponse)
    def widget_page() -> str:
        return (WIDGET_DIR / "index.html").read_text()

    @app.get("/widget/{name}")
    def widget_asset(name: str) -> FileResponse:
        allowed = {"widget.js": WIDGET_DIR / "dist" / "widget.js", "widget.css": WIDGET_DIR / "widget.css"}
        if name not in allowed or not allowed[name].exists():
            raise HTTPException(404)
        return FileResponse(allowed[name])

    app.state.engine = eng
    app.state.store = st
    return app
