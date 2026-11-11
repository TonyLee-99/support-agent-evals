# ADR 0002: claude-cli backend + response cache + replay, API key only for the frozen test

**Status:** accepted 2026-11-11

**Context.** No API budget at the start; later a $3.50-capped shared Anthropic key. CI must run with zero LLM calls.

**Decision.** One `LLMClient` interface with three backends: `claude_cli` (`claude -p --output-format json --json-schema`, isolated: empty cwd, no tools, no settings, no MCP, no session), `anthropic` (Haiku 5.5 only, cached system prefix, hard budget check against a shared ledger before every call, falls back to CLI at the cap), `replay` (cache lookup, miss = error). Every live response is cached by sha256(model, system, prompt, schema, sample[, backend]); the trial index is the `sample`, so trials are independent draws yet replayable. The API key is used only for the frozen test subset (baseline and final agent on the same backend); dev iteration uses the CLI.

**Rejected.** (a) Native function calling via the API for everything: budget. (b) A cache key without the trial index: trial 2 would replay trial 1 and pass^k would be meaningless (caught in design). (c) Mixing backends inside one comparison: confounds the interface with the agent.

**Consequences.** Tool calls are emulated through a strict-JSON schema (CLI) or a single `respond` tool (API). Results are specific to this interface; adapter repairs (unwrapped double-encoded messages, plain-text replies mapped to messages, direct tool_use mapped) are counted per run in `adapter_stats`. Measured cost: see `results/ledger_summary.json`.
