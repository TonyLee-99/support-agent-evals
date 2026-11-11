# Rollout plan

Phases (each needs the previous phase's exit criteria; owner = support ops lead + on-call engineer):

| phase | traffic | bot can write? | exit criteria |
|---|---|---|---|
| 0. Offline | none | n/a | `make check` green; regression gate PASS; service acceptance tests 100%; scenario holdout reviewed |
| 1. Shadow | 100% of return/exchange chats mirrored, humans answer | no (proposals logged only) | proposal matches the human's action in >= 90% of a 200-chat sample; zero policy-violation proposals |
| 2. Limited | 5% -> 25% of signed-in customers | yes, under limits | 2 weeks at each step within guardrails |
| 3. GA | all signed-in; guests read-only | yes | monthly re-calibration of the judge; weekly taxonomy review |

## Guardrail metrics (alert thresholds, per day)
- Policy-violation rate (deterministic: server-blocked write attempts per 100 chats): page if > 2.
- Double-refund count: page on any (should be impossible; it is a schema constraint).
- Handoff rate: investigate if outside 10-35% (too low = bot overreaching, too high = bot useless).
- CSAT proxy: judge `tone` + `goal_handling` pass rate on a 5% sample: investigate if < 85%.
- Cost per conversation (trace totals) and p95 model latency: investigate if > 2x the baseline week.

## Kill switch
- `SE_WRITES_ENABLED=0` (read by the tool layer at request time): write tools return `login_required`-style
  errors -> agent offers a human. Takes effect without deploy (env reload). Full stop: route the widget to the
  human queue at the edge.

## Handover checklist
- Runbook walked through with on-call; dashboards bookmarked; alert routes tested.
- Ops lead owns the scenario set: new incidents become scenarios within a week.
- Judge calibration owner named; next calibration date set.
