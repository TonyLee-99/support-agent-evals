# PRD: support agent + eval flywheel

**Problem.** A retailer wants an AI chat agent to handle returns, exchanges and cancellations. The blocker is not
building the bot; it is knowing whether it is safe and getting better: averages hide one-in-four failures, the
team has no external referee, and every prompt change is a guess.

**Users.** (1) Customers in the web chat. (2) Support agents working the human handoff queue. (3) The support
ops lead who decides how much traffic the bot gets. (4) The engineer who changes the agent.

**What we ship.**
1. A chat service (FastAPI + TypeScript widget) on the retail data model, where money and identity rules are
   enforced by the server: ownership, explicit confirmation card, idempotent writes, refund limits, handoff queue,
   injection guard, per-conversation traces.
2. An evaluation loop: external benchmark (tau2-bench retail, unmodified) + own adversarial scenarios (half
   written blind by another model) + calibrated LLM judge + failure taxonomy + auto-generated regression cases +
   a CI gate + a dashboard.

**Success metrics.**
- Benchmark: pass^1 and pass^k on a frozen test subset, final agent vs tau2's default agent, with paired CIs.
- Safety: zero double refunds and zero cross-customer writes in the deterministic service tests (hard gate).
- Scenarios: deterministic-check pass rate and judge pass rate by agent version; leak rate; handoff rate.
- Operations: cost and latency per conversation from traces.

**Non-goals.** Shopify/Zendesk/Twilio integrations, email or voice channels, K8s, fine-tuning the agent model.

**Acceptance criteria.** `docs/ACCEPTANCE.md` (fixed before building; PASS/FAIL table at the end).
