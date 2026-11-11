# Evaluation

Two score families, never mixed: **benchmark** (tau2-bench retail: the agent method in Sierra's environment) and
**service acceptance** (deterministic tests of the production stack). A third, **own scenarios**, measures the
service agent on adversarial conversations. All numbers below come from files in `results/` (a test diffs the
README against them).

## 1. Datasets and provenance

| set | who wrote it | size used | frozen how |
|---|---|---|---|
| tau2 retail test split | Sierra (benchmark authors) | 10 of 40 tasks (`N` knob), k=2 | `results/splits/frozen_test.json` (task hashes) + `test_order.json` (seeded order), committed before any tuning |
| tau2 retail dev | Sierra; subset chosen by seed | 10 of 30 dev tasks, k=1 | `results/splits/dev.json`, disjoint from test |
| own scenarios | 50 me, 50 ChatGPT via codex, blind (`scenarios/PROVENANCE.md`) | 20 run (10+10, `run_order.json`) | holdout 20+20 drawn before any agent ran on scenarios (`holdout.json`, file hashes) |
| judge calibration | sample of scenario conversations | 10 | seed 7, stratified by deterministic outcome |
| regression cases | generated from labelled dev failures, human-reviewed | see `results/regression/cases.jsonl` | only dev sources (test enforces) |

## 2. Setup (what differs from the leaderboard)
Agent and user simulator: `claude-haiku-5-5`. NL-assertion grader: `claude-haiku-5-5` (tau2 default is
gpt-5.4-mini, not reachable here). Test runs use the Anthropic API (one `respond` tool, cached system prefix);
dev runs use the subscription CLI (`--json-schema`). Baseline and final agent always share a backend within a
comparison. No leaderboard comparability is claimed.

## 3. Metrics
- pass^k (tau2): per task C(c,k)/C(n,k), averaged; **pass^1 is the preregistered primary metric**.
- 95% CIs: percentile bootstrap over tasks (2,000 resamples). Paired difference: bootstrap of per-task
  differences, plus counts of tasks better/worse.
- DB-only pass^1 (judge-free) reported next to the full reward.
- Scenarios: deterministic pass rate (all scenario checks hold), judge pass rate, leak rate, handoff rate,
  server-blocked write attempts, cost/latency from traces.

## 4. Results

**tau2 retail test subset (10 tasks, k=2, Anthropic API, `results/tau2_test_summary.json`).**
| agent | pass^1 [CI] | pass^2 [CI] | DB-only pass^1 | discarded sims (infra, re-run) |
|---|---|---|---|---|
| baseline `llm_agent` | 70% [50, 90] | 50% [20, 80] | 75% | 2 |
| `policy_agent` | 85% [65, 100] | 80% [50, 100] | 85% | 2 |

Paired pass^1 difference +15 pts, CI [0, +30]; 3 tasks better, 0 worse, 7 same. Dev (10 tasks, k=1, CLI):
60% -> 80% (`results/tau2_dev_summary.json`). Ablations (dev, exploratory): minus checklist 60%, minus
confirmation gate 70%, minus arg_check / identity_gate / handoff 80% (= full). Guard-only variants share every LLM
draw with the full agent until a guard fires (identical prompts -> identical cache keys), so their differences
isolate the guard; most guards never fired on these 10 tasks.

**Own scenarios (20 = 10 self + 10 codex; 11 holdout; `results/scenarios_summary.json`).** Deterministic pass
baseline 90%, v1 90%, v2 95%, v3 100%; judge pass 35%, 40%, 75%, 80%. Holdout (n=11): v1 100% / 45%, v3 100% / 73%.
Leak rate 0% everywhere. Mean per conversation (v3): 7.5 LLM calls, 38 s model time, $0.0068 list price.

**Error analysis (`results/flywheel/taxonomy.json`).** 22 labelled dev failures; 18 `hallucinated_fact`, mostly
invented tool outages -> traced to the CLI adapter (INCIDENTS INC-8), fixed in v3. Remaining tau2 dev failures:
`incomplete_action` (exchanges submitted before a return on the same order; one product id checked instead of all).

**Judge calibration (`results/judge_calibration.json`).** n=10; judge vs each grader 90% agreement, kappa 0.74
(CI 0.00-1.00); graders agree 100%. Per dimension: policy 90%, goal 90%, tone 100%. One disagreement (judge
stricter on refusal wording), resolved for the graders. Graders are a fresh Claude grader and ChatGPT (codex);
neither is a human support expert, and codex also wrote half the scenarios.

**Regression gate (`results/regression/report_*.json`).** 17 service cases x 3 fresh draws, all must pass:
v1 0.71, v2 0.65, v3 0.94, broken 0.53 -> gate ships v3.

## 5. Sample size, CI width, how to scale
With 10 tasks a single task flip moves pass^1 by 5-10 points; CI half-widths are ~15-20 points. To halve the CI
width, quadruple the tasks: `make tau2 N=40 K=4` (the full official test split; ~160 simulations per agent,
~$0.01 each on Haiku API: $0.46 bought 40 kept + discarded simulations). Scenarios: `make scenarios SCEN_N=50` runs all 100.
