# Design: support-agent-evals

## 1. Problem

Customer-support agents fail in ways that averages hide: they skip identity checks, act before the customer says
yes, refund the wrong item, or leak another customer's data on one run out of four. The hard problem here is
**measuring and improving a conversational, tool-using agent honestly** - with an external referee, a frozen test
split, repeated trials, and a loop that turns production failures into regression tests.

The support agent is the vehicle. The product is the evaluation and monitoring loop.

## 2. Components

```
                +------------------------- tau2-bench (pinned, unmodified) ------------------------+
                |  retail env + tools + policy + tasks + evaluator + UserSimulator                  |
                +----------------^-------------------------------^----------------------------------+
                                 | litellm.completion("claudecli/<model>")
             bench/provider.py   |  CustomLLM: OpenAI-format messages+tools -> strict JSON -> tool_calls
                                 v
   llm/  LLMClient  { claude-cli (subprocess `claude -p --output-format json --json-schema`) | anthropic | replay }
                 \__ cache (sha256 of backend+model+system+prompt+schema+sample) + cost ledger

   bench/agents.py   baseline = tau2 llm_agent (default prompt)    policy_agent = planner prompt + guards (flags)
   bench/run.py      our batch runner: tasks x trials, <=4 workers, writes results/tau2/<run>/sims.jsonl.gz
   bench/metrics.py  pass^k (tau2 formula), task-cluster bootstrap CIs, paired differences

   service/          FastAPI: chat API, SQLite store seeded from the same retail db.json, production tools
                     (ownership, confirmation, idempotency, limits), injection guard, handoff queue, tracing
   web/widget/       TypeScript chat widget (esbuild bundle, no framework)

   scenarios/        100 own scenarios (50 self, 50 codex-blind), holdout flags
   flywheel/         label failures (codebook + LLM) -> embed + cluster -> regression cases -> gate
   judge/            rubric judge + calibration (2 independent graders + judge, kappa, CIs)
   dashboard/        static HTML built from results/*.json (GitHub Pages)
```

## 3. Benchmark integration (tau2-bench retail)

- **Pinned, unmodified.** `third_party/tau2-bench` is cloned at a recorded commit by `make init` and installed as a
  path dependency. `scripts/check_tau2_pristine.py` hashes the retail domain files and the pinned commit; any
  modification fails the check. Scores are only comparable to tau2's own setup if tools/policy/tasks are untouched.
- **Hook.** tau2 calls `litellm.completion()` for both the agent and the user simulator. LiteLLM supports custom
  providers (`litellm.custom_provider_map` + `CustomLLM`). We register provider `claudecli`; model strings become
  `claudecli/claude-haiku-5-5`. No tau2 file changes. Our runner registers the provider and our agent factory, then
  calls tau2's public Layer-2 API (`build_text_orchestrator` + `run_simulation`) per (task, trial).
- **Tool-call emulation.** The CLI has no native function calling. The provider renders tool JSON schemas into the
  system prompt and asks for a JSON object `{"message": str|null, "tool_calls": [{"name", "arguments"}]}` enforced by
  `--json-schema`. The adapter validates the object, maps it to OpenAI `tool_calls` (ids are deterministic:
  `call_<sha8>`), and if the model returns both text and a tool call it keeps the tool call (logged as
  `both_text_and_tool`). The user simulator gets the same adapter with no tools. The adapter is identical for
  baseline and our agent, so it cannot favour one of them.
- **Trials and the cache.** The cache key includes a `sample` index = trial number, passed through
  `llm_args={"metadata": {"trial": t}}`. Without it, trial 2 would replay trial 1 byte-for-byte and pass^k would be
  meaningless. Same (task, trial) under two agent versions shares the user simulator's first reply (common random
  numbers - lowers variance of the paired difference).
- **Models.** Agent and user simulator both `claude-haiku-5-5` via the subscription CLI (sonnet only if the budget
  allows). The leaderboard uses gpt-4.1 as user simulator and the full 114 tasks, so our numbers are **not**
  leaderboard-comparable; README/EVAL list every difference.
- **Splits.** Frozen test = tau2's official retail `test` split (40 tasks), chosen by the benchmark authors - not by
  me. Hashed + committed before the first tuning run. Dev = a fixed random 30-task subset of tau2's `train` split
  (seed recorded); ablations and prompt iteration only touch dev.
- **Budget (measured first).** Before committing to k, one task is timed. Planned upper bound: 40 test tasks x k=3 x 2
  agents = 240 simulations; at <= 4 concurrent `claude -p` processes. If one task takes > 4 min, k drops to 2.
- **Metrics.** pass^k per tau2: for task with c successes in n trials, pass^k = C(c,k)/C(n,k), averaged over tasks.
  95% CI = percentile bootstrap over tasks (2,000 resamples, seed 0). Baseline vs agent: paired bootstrap of the
  per-task difference, plus counts of tasks that flip.

## 4. Our agent (`policy_agent`)

Starts from tau2's `LLMAgent` (same tools, same policy text) and adds, each behind a flag for ablations:

| flag | change | why it should matter |
|---|---|---|
| `planner` | system prompt that restates the policy as a checklist (authenticate -> look up -> plan -> list details -> wait for "yes" -> act once per order with all items) | small models skip steps under long policies |
| `arg_check` | validate tool args against the JSON schema and known ids from earlier tool results (order ids, item ids, payment method ids); on failure the model gets the error and retries (max 2) without the call reaching the env | wrong ids are the cheapest failure to stop |
| `confirm_gate` | a write tool is only emitted if the latest user turn is an explicit affirmation after the agent listed the action; otherwise the agent is re-prompted to ask for confirmation | policy requires explicit "yes"; acting early is irreversible |
| `identity_gate` | no order/user-detail lookup or write before a successful `find_user_id_*`; writes only on orders whose `user_id` matches the authenticated user (from tool results, not model text) | other-customer access |
| `handoff` | out-of-scope requests -> `transfer_to_human_agents` with a structured summary (customer, intent, actions done, reason) | clean escalation |

Guards are code, not prompt text: they inspect tool results in the agent state. Ablation = full agent minus one
flag, on dev, k=1 (budget permitting k=2).

## 5. Production-shaped service (separate score family)

- **Store.** SQLite (WAL) seeded from the same retail `db.json` (users, orders, products). Tables: `users`, `orders`,
  `order_items`, `refunds`, `idempotency_keys`, `pending_actions`, `handoff_tickets`, `conversations`, `messages`,
  `spans`. A committed fixture (`tests/fixtures/retail_small.json`, ~30 users) keeps tests offline and small.
- **Identity.** A session is either *authenticated by channel* (logged-in widget user) or *anonymous*; anonymous must
  verify via email or name+zip. `verified_user_id` is set only by the server from a successful lookup tool result.
- **Write path.** Agent proposes a write -> server stores a `pending_action` (payload hash) and returns a
  confirmation card to the customer -> only `POST /confirm {action_id}` from the customer executes it. The executor
  checks: ownership, order status, item not already refunded/exchanged, amount <= auto-approval limit (default
  $500/request, $1,000/customer/day) else creates a handoff ticket. Executed under `BEGIN IMMEDIATE` with a unique
  constraint on `(order_id, item_id)` in `refunds` so concurrent requests cannot double-refund.
- **Idempotency.** Every write carries `Idempotency-Key`; table stores key, request hash, response. Replay with the
  same hash returns the stored response; different hash -> 409.
- **Injection guard.** (1) pattern + heuristic scorer on user text and on string fields of tool outputs; flagged text
  is wrapped as quoted data and recorded as a guard span; (2) the structural defence is that writes need B1+B2 which
  the model cannot forge. The guard is measured on the injection scenarios, not assumed.
- **Handoff queue.** Tickets with structured summary, priority, status (`open -> claimed -> resolved`), API for agents.
- **Tracing.** Spans per conversation: `llm` (latency, input/output tokens, cost), `tool` (latency, args, ok/error),
  `guard` (decision). `GET /v1/conversations/{id}/trace` returns them plus a latency breakdown.
- **Widget.** TypeScript, esbuild bundle, renders messages, confirmation cards (Confirm / Cancel), handoff banner,
  and a "trace" link. Served from `/widget`.

## 6. Eval flywheel

1. **Traces in:** failed tau2 simulations (test + dev) and failed own-scenario runs.
2. **Label:** fixed codebook (derived by open coding the first 15 failures by hand, then frozen); an LLM labeller
   gets transcript + evaluator diff (expected vs actual actions / DB) and returns `{category, first_bad_turn,
   root_cause}`. Codebook includes `user_sim_error` so benchmark noise is not blamed on the agent.
3. **Cluster:** embed `root_cause` text (local `fastembed` bge-small, cached) -> agglomerative clustering; report
   cluster purity vs codebook labels and inspect clusters with no matching category.
4. **Regress:** each labelled failure -> regression case = conversation prefix up to `first_bad_turn` + a checkable
   property of the agent's next step (`must_not_call`, `must_call`, `must_ask_confirmation`, `must_not_mention`).
   Running a case is one agent step (cheap). Live runs are cached; CI replays them.
5. **Gate:** CI runs deterministic guard cases for real and replays LLM cases; the gate fails if pass rate < the
   recorded threshold or if the committed regression report's agent fingerprint does not match the current agent
   (forces a live refresh after prompt changes). A known-bad agent fixture must fail the gate (tested).
6. **Track:** failure counts per category per agent version -> dashboard.

## 7. Own scenarios and the judge

- 100 scenarios, YAML-like JSON: `id, author, split, category, customer (user id in DB), user_instructions, checks`.
  50 by me; 50 by codex in a separate read-only directory containing only the data-model description, the policy,
  the scenario schema and a 30-user DB excerpt (no agent code, no prompt, none of my scenarios). Categories: normal
  returns/exchanges/cancels, refund fraud, other-customer data, prompt injection, angry customers, out-of-policy.
- Holdout: 20 of mine + 20 of codex's, chosen by a seeded shuffle and committed before any agent run on scenarios.
- Scoring: deterministic checks first (refund rows, statuses, forbidden tools, handoff ticket, leak string match),
  LLM judge for the rubric (policy adherence, correct refusal, tone).
- Calibration: 30 conversations sampled stratified by (deterministic pass/fail x category) with seed 7. Graders:
  me and codex, each blind to the judge's output and to each other; the judge (claude-sonnet) grades the same 30.
  Report Cohen's kappa + raw agreement, bootstrap CIs, and every disagreement. Neither grader is a human customer
  support expert; this is stated, and an owner re-grade is listed as a follow-up.

## 8. Offline / CI story

`SE_LLM_BACKEND=replay` makes every LLM call a cache lookup; a miss raises. `make check` = ruff + mypy (service +
llm) + pytest + widget type-check, all offline. tau2 simulations are not re-run in CI; CI recomputes metrics from the
committed `results/tau2/*/sims.jsonl.gz` and checks them against the summary files.

## 9. Non-goals

Shopify, Zendesk, Twilio, email channel, K8s, voice. Fine-tuning the agent model (ADR).
