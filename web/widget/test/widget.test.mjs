import assert from "node:assert/strict";
import { test } from "node:test";
import { Api, idempotencyKey, summarizeTrace } from "../dist/widget.mjs";

test("idempotency key is stable per action (double click reuses it)", () => {
  assert.equal(idempotencyKey("conv_1", "act_9"), idempotencyKey("conv_1", "act_9"));
  assert.notEqual(idempotencyKey("conv_1", "act_9"), idempotencyKey("conv_1", "act_8"));
});

test("trace summary shows latency, tokens and cost", () => {
  const s = summarizeTrace({ spans: [], totals: { llm_calls: 3, llm_ms: 4200, tool_calls: 2, tool_ms: 12,
    input_tokens: 9000, output_tokens: 300, cost_usd: 0.00123, guard_flags: 1 } });
  assert.match(s, /3 LLM calls · 4\.2 s model · 2 tools · 12 ms tools · 9300 tokens · \$0\.0012 · 1 guard flags/);
});

test("api sends session token and idempotency key headers", async () => {
  const seen = [];
  globalThis.fetch = async (url, init) => { seen.push({ url, init }); return { ok: true, json: async () => ({ replies: [], state: "open" }) }; };
  const api = new Api("http://x");
  api.token = "u.sig";
  await api.act("c1", "a1", true, "c1:a1:confirm");
  assert.equal(seen[0].url, "http://x/v1/conversations/c1/actions/a1/confirm");
  assert.equal(seen[0].init.headers["X-Session-Token"], "u.sig");
  assert.equal(seen[0].init.headers["Idempotency-Key"], "c1:a1:confirm");
});
