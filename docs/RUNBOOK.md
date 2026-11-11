# Runbook

## Run locally
```
make init                      # tau2 at pinned commit, uv sync, widget deps
SE_DB=data/service.sqlite uv run uvicorn --factory support_evals.service.app:create_app --port 8000
open http://localhost:8000/widget
```
Env: `SE_SESSION_SECRET` (HMAC for session tokens), `SE_OPS_TOKEN` (handoff queue), `SE_LLM_BACKEND`
(`claude_cli` | `anthropic` | `replay`), `SE_DEMO_LOGIN=0` in any real deployment.

## Deploy / rollback
Single container, one SQLite file on a volume (move to Postgres before multi-instance). Deploy = new image +
`make check` in CI. Rollback = previous image; the schema is additive (`CREATE TABLE IF NOT EXISTS`). Agent prompt
versions are code: rolling back the image rolls back the prompt; `agent_version` is stored per conversation.

## Alerts and first response
| alert | first check | action |
|---|---|---|
| server-blocked writes spike | `GET /v1/conversations/{id}/trace` for recent chats; look for `guard` spans | if injection campaign: kill switch writes; add scenarios |
| handoff rate spike | handoff tickets `reason` field | model/provider outage? check `llm` span errors; if prompt regression, roll back |
| LLM errors / latency | trace `llm` spans `latency_ms`; provider status | backend fails over CLI<->API only in eval; in prod, show "connecting you to a human" |
| budget ledger near cap | `results/ledger_summary.json`, `~/.agent-budget/anthropic_ledger.jsonl` | API backend refuses at cap and falls back |

## Recovery
- Crash mid-confirmation: the confirm transaction is atomic; the client retries with the same Idempotency-Key and
  gets the stored response (tested: `test_crash_before_commit_rolls_back_and_retry_succeeds`).
- Stuck pending actions expire after 15 minutes; the customer must re-confirm a fresh proposal.

## Eval operations
- Re-run regression gate after any prompt change: `make regress && make gate`.
- Benchmark at a different size: `make tau2 N=40 K=4` (cost scales ~linearly; ~$0.01 per simulation on Haiku API).
- Rate limit on the CLI: runners stop and record `discarded` sims; re-run the same command to resume.
