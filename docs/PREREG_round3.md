# Preregistration - round 3 (written 2026-11-11, before any round-3 tuning)

## Question
Does a policy agent with (a) native tool calling and (b) fixes aimed at the failure classes seen on the dev
split beat tau2's default agent on the frozen retail test split, when both use the same model and interface?

## Why the baseline is re-run (deviation from "reuse the existing baseline sims")
The existing baseline simulations did **not** use native tools: every model in this repo, including tau2's default
`llm_agent`, went through the same JSON `respond` wrapper. Giving native tools to our agent only would build the
interface change into the comparison in our agent's favour. So both agents are run on the native path, and the
existing wrapper-based results stay as round 2.

## Hypothesis
H1: pass^1(policy_agent_r3, native) - pass^1(llm_agent, native) > 0 on the 40 frozen test tasks.

## Primary metric (single)
Paired difference in pass^1 over the 40 frozen test tasks (`results/splits/frozen_test.json`), 95% percentile
bootstrap over tasks (2,000 resamples, seed 0), both agents on the native tool-calling backend, model
`claude-haiku-5-5` for agent, user simulator and NL grader. H1 is supported only if the CI lower bound is > 0.
Secondary (reported, not decisive): pass^k for k = 2..K, DB-only pass^1, invalid-output rate.

## Trials K (fixed by rule before the test run)
Measure the native cost per simulation on the dev check. K = the largest of {3, 2, 1} such that
2 agents x 40 tasks x K x (measured cost per simulation) <= ($4.40 - ledger total at that moment - $0.10).
The test split is run exactly once, at the end, with the frozen agent; no re-runs except to recover simulations
lost to infrastructure errors (which are never scored).

## What tuning may use
Only the dev split (`results/splits/dev.json`) and the rest of tau2's train split. Test transcripts are not read;
the descriptive test failure *categories* already published in round 2 are not used to choose fixes.
Dev iterations run on the subscription CLI (wrapper interface, $0); one small native dev check measures the
invalid-output rate and the cost per simulation before the test run.

## Freeze
Before the test run, the agent files' sha256 are written to `results/agent_freeze_r3.json` and committed.

## Amendment 1 (2026-11-11, before any round-3 test-split run): free go/no-go gate on dev
Owner rule: no API money for the test run until a $0 dev check passes.
- Dev comparison: `policy_agent_r3` vs `llm_agent` on the 40 `devplus` tasks (`results/splits/devplus.json`:
  the 30 dev tasks + 10 more from tau2's train split, none in the frozen test set), k=2, both on the same
  interface (subscription CLI, JSON wrapper - the only $0 path), at most 4 concurrent `claude -p`.
- **GO** only if the paired pass^1 lift is >= +8 pts AND its bootstrap 95% CI lower bound is > 0. Otherwise
  iterate on dev ($0); if no GO after reasonable effort, stop and report without any API test run.
- Dev results are recorded in `results/round3_summary.json` whatever they are.
- Native-interface dev checks already run before this amendment (baseline on 10 dev tasks, $0.08) are reported
  as diagnostics only, not as part of the gate.
