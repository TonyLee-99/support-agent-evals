"""Playwright screenshots of the static dashboard and the widget (served by the app with a scripted LLM)."""

import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "docs" / "img"


def serve_widget(port: int = 0) -> int:
    """Start the real FastAPI app on a temp DB with a deterministic scripted model (no LLM calls)."""
    import socket
    import tempfile

    if not port:
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]

    import uvicorn

    from support_evals.llm.base import LLMResponse
    from support_evals.service import agent as agent_mod
    from support_evals.service.app import create_app
    from support_evals.service.store import Store, load_seed

    script = [
        {"action": "tool_call", "tool_call": {"name": "get_order_details", "arguments": {"order_id": "#W8528674"}}},
        {"action": "tool_call", "tool_call": {"name": "return_delivered_order_items", "arguments": {
            "order_id": "#W8528674", "item_ids": ["5111440845"], "payment_method_id": "paypal_7664977"}}},
        {"action": "message", "message": "I've prepared the return of the LED Light Bulb ($48.55) to your PayPal. "
                                         "Please confirm with the button below."},
        {"action": "message", "message": "Done - the return is requested. You'll get an email with return steps."},
    ]

    class Scripted:
        backend, model = "scripted", "demo"

        def complete(self, system, prompt, **kw):
            r = script.pop(0) if script else {"action": "message", "message": "Anything else?"}
            return LLMResponse(text="", model="demo", structured=r, input_tokens=5200, output_tokens=60,
                               cost_usd=0.0006, latency_s=1.1)

    agent_mod.get_client = lambda model: Scripted()
    db = Path(tempfile.mkdtemp()) / "demo.sqlite"
    app = create_app(store=Store(db, seed=load_seed()))
    threading.Thread(target=lambda: uvicorn.run(app, port=port, log_level="error"), daemon=True).start()
    time.sleep(2)
    return port


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    port = serve_widget()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1360, "height": 900}, device_scale_factor=2)
        pg.goto((ROOT / "docs/site/index.html").as_uri())
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(IMG / "dashboard.png"), full_page=False)
        pg.screenshot(path=str(IMG / "dashboard_full.png"), full_page=True)
        w = b.new_page(viewport={"width": 1200, "height": 760}, device_scale_factor=2)
        w.goto(f"http://127.0.0.1:{port}/widget")
        w.wait_for_selector(".sw-msg")
        w.fill(".sw-input", "I'd like to return the light bulb from #W8528674")
        w.click(".sw-send")
        w.wait_for_selector(".sw-card")
        w.click(".sw-yes")
        w.wait_for_selector(".sw-card-done")
        w.click(".sw-trace-btn")
        w.wait_for_selector(".sw-trace")
        w.screenshot(path=str(IMG / "widget.png"))
        b.close()
    print("screenshots in", IMG)


if __name__ == "__main__":
    main()
