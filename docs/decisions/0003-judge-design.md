# ADR 0003: judge design - deterministic checks first, frozen-rubric LLM judge second, calibrated

**Status:** accepted 2026-11-11

**Decision.** Score own scenarios with deterministic checks on final state and trace (refund rows, order status, forbidden tools, leak strings, handoff ticket). Use an LLM judge (Haiku, rubric frozen in `judge/RUBRIC.md` before grading) only for what code cannot see: invented facts, refusal quality, tone. Calibrate it on a seeded stratified sample against two independent graders (a fresh Claude grader that never saw the judge prompt or outputs, and ChatGPT via codex), reporting raw agreement, Cohen's kappa with bootstrap CIs and every disagreement.

**Rejected.** (a) Judge-only scoring: unverifiable and self-referential (Claude grading Claude). (b) Reporting kappa alone: at n=10 kappa is unstable; raw agreement + CI + the disagreement list are more honest.

**Consequences.** Neither grader is a human support expert; a human re-grade is listed as an owner follow-up. Codex authored half the scenarios and also grades: agreement is also split by scenario author.
