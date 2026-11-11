// Support chat widget: talks to the FastAPI service. No framework; ~250 lines of strict TypeScript.
// Confirmations are buttons that call /confirm with an Idempotency-Key, so a double click cannot refund twice.

export type Role = "user" | "assistant" | "confirmation" | "system";

export interface Reply {
  role: Role;
  content?: string | null;
  action_id?: string;
  summary?: string;
}

export interface Span {
  kind: "llm" | "tool" | "guard";
  name: string;
  latency_ms: number;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
}

export interface Trace {
  spans: Span[];
  totals: { llm_calls: number; llm_ms: number; tool_calls: number; tool_ms: number; input_tokens: number;
    output_tokens: number; cost_usd: number; guard_flags: number };
}

export class Api {
  token: string | null = null;
  constructor(private base: string) {}

  private async call<T>(path: string, init: RequestInit = {}, extra: Record<string, string> = {}): Promise<T> {
    const headers: Record<string, string> = { "content-type": "application/json", ...extra };
    if (this.token) headers["X-Session-Token"] = this.token;
    const r = await fetch(this.base + path, { ...init, headers });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return (await r.json()) as T;
  }

  async login(userId: string): Promise<void> {
    const r = await this.call<{ session_token: string }>("/v1/demo/login", {
      method: "POST", body: JSON.stringify({ user_id: userId }) });
    this.token = r.session_token;
  }

  start(): Promise<{ conversation_id: string; messages: Reply[] }> {
    return this.call("/v1/conversations", { method: "POST", body: "{}" });
  }

  send(cid: string, text: string): Promise<{ replies: Reply[]; state: string }> {
    return this.call(`/v1/conversations/${cid}/messages`, { method: "POST", body: JSON.stringify({ text }) });
  }

  act(cid: string, actionId: string, confirm: boolean, key: string): Promise<{ replies: Reply[]; state: string }> {
    const verb = confirm ? "confirm" : "decline";
    return this.call(`/v1/conversations/${cid}/actions/${actionId}/${verb}`, { method: "POST" },
      confirm ? { "Idempotency-Key": key } : {});
  }

  trace(cid: string): Promise<Trace> {
    return this.call(`/v1/conversations/${cid}/trace`);
  }
}

/** One idempotency key per action: retries and double clicks reuse it. */
export function idempotencyKey(cid: string, actionId: string): string {
  return `${cid}:${actionId}:confirm`;
}

export function summarizeTrace(t: Trace): string {
  const x = t.totals;
  return `${x.llm_calls} LLM calls · ${(x.llm_ms / 1000).toFixed(1)} s model · ${x.tool_calls} tools · ` +
    `${(x.tool_ms).toFixed(0)} ms tools · ${x.input_tokens + x.output_tokens} tokens · $${x.cost_usd.toFixed(4)}` +
    (x.guard_flags ? ` · ${x.guard_flags} guard flags` : "");
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  e.className = cls;
  if (text !== undefined) e.textContent = text; // textContent, never innerHTML: model output is untrusted
  return e;
}

export class Widget {
  private cid = "";
  private log: HTMLElement;
  private input: HTMLInputElement;
  private status: HTMLElement;
  private busy = false;

  constructor(private root: HTMLElement, private api: Api) {
    root.classList.add("sw");
    const head = el("div", "sw-head");
    head.append(el("span", "sw-dot"), el("span", "sw-title", "Support"));
    this.status = el("span", "sw-status", "connecting…");
    head.append(this.status);
    this.log = el("div", "sw-log");
    const form = el("form", "sw-form");
    this.input = el("input", "sw-input");
    this.input.placeholder = "Ask about an order, a return or an exchange";
    const send = el("button", "sw-send", "Send");
    form.append(this.input, send);
    form.addEventListener("submit", (ev) => { ev.preventDefault(); void this.onSend(); });
    const traceBtn = el("button", "sw-trace-btn", "Trace");
    traceBtn.type = "button";
    traceBtn.addEventListener("click", () => void this.showTrace());
    head.append(traceBtn);
    root.append(head, this.log, form);
  }

  async open(userId: string | null): Promise<void> {
    if (userId) await this.api.login(userId);
    const c = await this.api.start();
    this.cid = c.conversation_id;
    this.status.textContent = userId ? `signed in as ${userId}` : "guest (read-only)";
    c.messages.forEach((m) => this.render(m));
  }

  private render(m: Reply): void {
    if (m.role === "confirmation" && m.action_id) {
      const card = el("div", "sw-card");
      card.append(el("div", "sw-card-label", "Please confirm"), el("div", "sw-card-body", m.summary ?? ""));
      const row = el("div", "sw-card-row");
      const yes = el("button", "sw-yes", "Confirm");
      const no = el("button", "sw-no", "Decline");
      const id = m.action_id;
      yes.addEventListener("click", () => void this.onAction(card, id, true));
      no.addEventListener("click", () => void this.onAction(card, id, false));
      row.append(yes, no);
      card.append(row);
      this.log.append(card);
    } else if ((m.role === "user" || m.role === "assistant") && m.content) {
      const transfer = m.content.startsWith("YOU ARE BEING TRANSFERRED");
      this.log.append(el("div", `sw-msg sw-${m.role}${transfer ? " sw-transfer" : ""}`,
        transfer ? "You're being connected to a human agent. Please hold on." : m.content));
    }
    this.log.scrollTop = this.log.scrollHeight;
  }

  private async onSend(): Promise<void> {
    const text = this.input.value.trim();
    if (!text || this.busy) return;
    this.input.value = "";
    this.render({ role: "user", content: text });
    await this.withBusy(async () => {
      const r = await this.api.send(this.cid, text);
      r.replies.forEach((m) => this.render(m));
      this.setState(r.state);
    });
  }

  private async onAction(card: HTMLElement, actionId: string, confirm: boolean): Promise<void> {
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    await this.withBusy(async () => {
      const r = await this.api.act(this.cid, actionId, confirm, idempotencyKey(this.cid, actionId));
      card.classList.add(confirm ? "sw-card-done" : "sw-card-declined");
      r.replies.forEach((m) => this.render(m));
      this.setState(r.state);
    });
  }

  private setState(state: string): void {
    if (state === "handed_off") {
      this.status.textContent = "with a human agent";
      this.root.classList.add("sw-handed-off");
    }
  }

  private async withBusy(fn: () => Promise<void>): Promise<void> {
    this.busy = true;
    const typing = el("div", "sw-typing", "…");
    this.log.append(typing);
    try {
      await fn();
    } catch (e) {
      this.log.append(el("div", "sw-error", `Something went wrong: ${(e as Error).message}`));
    } finally {
      typing.remove();
      this.busy = false;
    }
  }

  private async showTrace(): Promise<void> {
    if (!this.cid) return;
    const t = await this.api.trace(this.cid);
    this.log.append(el("div", "sw-trace", summarizeTrace(t)));
    this.log.scrollTop = this.log.scrollHeight;
  }
}

declare global {
  interface Window { SupportWidget: { mount: (root: HTMLElement, base: string, userId: string | null) => Widget } }
}

if (typeof window !== "undefined") {
  window.SupportWidget = {
    mount(root, base, userId) {
      const w = new Widget(root, new Api(base));
      void w.open(userId);
      return w;
    },
  };
}
