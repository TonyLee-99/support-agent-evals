# Evaluation

Two score families, never mixed: **benchmark** (tau2-bench retail: the agent method in Sierra's environment) and
**service acceptance** (deterministic tests of the production stack). A third, **own scenarios**, measures the
service agent on adversarial conversations. All numbers below come from files in `results/` (a test diffs the
README against them).

## 1. Datasets and provenance

| set | who wrote it | size used | frozen how |
|---|---|---|---|
| tau2 retail test split | Sierra (benchmark authors) | all 40 tasks, k=3 (earlier: 10 tasks, k=2) | `results/splits/frozen_test.json` (task hashes) + `test_order.json` (seeded order), committed before any tuning |
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

**tau2 retail, full official test split (40 tasks, k=3, Anthropic API, `results/tau2_test_summary.json`).**
| agent | pass^1 [CI] | pass^2 [CI] | pass^3 [CI] | DB-only pass^1 | sims kept / planned |
|---|---|---|---|---|---|
| baseline `llm_agent` | 83% [73, 92] | 74% [62, 87] | 69% [54, 82] | 83% | 119 / 120 |
| `policy_agent` | 80% [71, 89] | 71% [58, 83] | 67% [51, 82] | 81% | 119 / 120 |

Paired pass^1 difference -2 pts, CI [-8, +4]; 6 tasks better, 7 worse, 27 same. pass^2 -3 pts [-14, +8];
pass^3 -5 pts [-18, +11] (38 tasks with 3 trials for both). **No measurable lift.** The preregistered 10-task x
2-trial run (`results/tau2_test10_summary.json`: 85% vs 70%, +15 pts, CI [0, +30]) was a seeded random subset
drawn before tuning; its 20 simulations per agent are reused unchanged inside the 40-task run (same cache keys).
The other 30 tasks favour the baseline slightly, so the earlier lift was small-sample noise. Each agent lost one
simulation to an adapter failure (INC-9): discarded, not scored, task left out of pass^3 only.
Why the guards did not help here: the confirmation gate fired 34 times in 119 policy-agent simulations, but
tau2's simulated customers confirm readily, so blocking an early write mostly adds a turn. 17 of the 44 failed test
simulations are labelled `user_sim_error` (`results/flywheel/test_taxonomy_descriptive.json`, descriptive only). Dev (10 tasks, k=1, CLI):
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

**Test-split failures (descriptive only, after freeze; `results/flywheel/test_labels_descriptive.jsonl`).**
44 failed simulations in the 40-task run (baseline 21, policy agent 23). Labels: user_sim_error 17, incomplete_action
9, false_refusal 7, hallucinated_fact 3, wrong_info 2, wrong_args 2, harness_error 2, policy_violation 1,
premature_end 1. Largest embedding cluster (19): returns/exchanges handled partly or refused. Reported, not used for
tuning; they would feed the next version only.

**Judge calibration (`results/judge_calibration.json`).** n=10; judge vs each grader 90% agreement (CI 70-100%), kappa 0.74
(CI 0.00-1.00); graders agree 100%. Per dimension: policy 90%, goal 90%, tone 100%. One disagreement (judge
stricter on refusal wording), resolved for the graders. Graders are a fresh Claude grader and ChatGPT (codex);
neither is a human support expert, and codex also wrote half the scenarios.

**Regression gate (`results/regression/report_*.json`).** 17 service cases x 3 fresh draws, all must pass:
v1 0.71, v2 0.65, v3 0.94, broken 0.53 -> gate ships v3.

## 5. Sample size, CI width, how to scale
Measured: at 10 tasks the pass^1 CI half-width was ~20 points; at 40 tasks x 3 trials it is ~9 points, and the
paired-difference CI is [-8, +4]. Cost: the 40 x 3 run cost $2.05 on Haiku 5.5 (about $0.01 per simulation; 200 new
simulations, the 40 from the 10 x 2 run replayed from cache). K was set to 3, not 4, to stay under the $2.80
budget for both agents. `make tau2 N=40 K=4` adds the fourth trial (~$0.85 more). Scenarios: `make scenarios SCEN_N=50` runs all 100.
