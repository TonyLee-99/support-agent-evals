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
(filled from results/ at the end of the build - see README and `results/*.json`)

## 5. Sample size, CI width, how to scale
With 10 tasks a single task flip moves pass^1 by 5-10 points; CI half-widths are ~15-20 points. To halve the CI
width, quadruple the tasks: `make tau2 N=40 K=4` (the full official test split; ~160 simulations per agent,
~$0.02 each on Haiku API at measured cost). Scenarios: `make scenarios SCEN_N=50` runs all 100.
