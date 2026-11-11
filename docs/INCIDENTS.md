# Incidents

Real problems hit while building and evaluating. Nothing here is invented; each links to the fix.

## INC-1 (full postmortem): API tool-call mapping silently turned interface failures into agent failures

**Date** 2026-11-11, during the frozen-test run on the Anthropic API backend.

**Symptom.** The first API run logged dozens of `AdapterError: no valid turn after retries: reply is not a JSON
object`. tau2 turned each into an agent error, so simulations ended early and were scored 0 for *both* agents.

**Impact.** Had it gone unnoticed, the headline benchmark numbers would have measured our adapter, not the
agents. Spend: about $0.20 of the $3.50 budget on runs that were thrown away.

**Root cause.** Two mismatches between the API backend and the strict-JSON contract the CLI backend follows:
1. Haiku often answered with plain text instead of calling the `respond` tool (50 cached replies; counted by the
   purge script at the time, output not saved).
2. With tool schemas described in the system prompt, it sometimes emitted a `tool_use` block named after a
   retail tool (e.g. `get_order_details`) instead of `respond` (80 cached replies, `stop_reason=tool_use`,
   `structured=null`; same provenance).
A third, latent bug made it worse: the provider's adapter failure raised `AdapterError`, which our runner did not
classify as an infrastructure error, so the broken simulations were *scored* rather than discarded.

**Detection.** Reading the run log instead of only the final reward line; counting cache records with
`structured=null` grouped by `stop_reason` and tag.

**Fix.** (a) Map plain text to `{"action": "message"}` and a direct `tool_use` to `{"action": "tool_call"}` -
both are exactly what the native API means; count them in response meta. (b) Adapter failures now mark the
simulation as an infra error: it is discarded and re-run, and the count is reported (`discarded_sims`).
(c) Purged the bad cache entries and re-ran both agents from scratch.

**What the fix does and does not do.** v3 still shows extra CLI turns at the same rate (37%,
`results/cli_num_turns.json` by version), so the note did not stop the native attempts; it stopped the model from
treating them as real tool failures and telling the customer. A cleaner fix is a backend with native tool calling.
**Prevention.** `adapter_stats` and `discarded_sims` are part of every summary and shown on the dashboard; a run
with discarded simulations is visible, never silent. Lesson: when the measurement pipeline can fail, its failures
must be a separate outcome from the system-under-test's failures.

## INC-2: simulated customer double-encoded its messages
Pilot runs showed user-simulator turns like `{"message": "Okay, so ..."}` inside the message text (27 of 54 user
turns in the first pilots: `adapter_unwrapped` in `results/tau2/pilot_*/meta.json`). Cause: the model put the JSON envelope into the `message` field. Fix: one-level unwrap
in the adapter, counted as `adapter_unwrapped`, plus a protocol line "the value of message is plain text". Applies
equally to both agents.

## INC-3: scenario harness ended conversations before the customer could click Confirm
Smoke test S001: the simulated customer said "yes, go ahead" and set `done=true` in the same turn in which the
confirmation card first appeared, so the harness stopped and scored a false failure (no refund). Fix: the loop
continues while a card is pending. Found by reading the transcript of a "failed" legitimate return.

## INC-4: agent hallucinated tool failures (agent defect, fed to the flywheel)
Smoke tests S001 and S015 (dev scenarios, agent v1): the agent told the customer "I'm unable to access the account"
right after a successful lookup, and "the transfer did not go through" when no transfer had been attempted. Labelled
`hallucinated_fact`; became regression cases and a v2 lesson.

## INC-5: usage-limit message not recognised -> 21 dev simulations lost
The CLI's "You've hit your 5-hour limit" text did not match the limit detector, so the runner retried and logged
21 infra errors instead of stopping cleanly. The errors were not scored (infra), but the run stalled. Fix:
detector covers this message; the runner stops on the first limit error.

## INC-6: environment issues during setup
- A non-editable install of tau2 broke its data paths (it resolves `data/` relative to the source tree): switched
  to an editable path dependency.
- Trimming tau2's multilingual data to save disk broke even English runs (tau2 loads the `en` language pack):
  restored it. The pristine check now hashes the files we rely on.

## INC-7: the regression gate passed a deliberately broken agent
The first suite (18 cases mined from labelled dev failures) gave the `broken` agent ("do what the customer asks
immediately, skip verification and confirmations") the same 0.83 pass rate as v2. Root cause: mined cases cover
only failures that happened, and the dominant observed failure (invented tool outages) is one a reckless agent
never commits. Fix: added 4 hand-written safety seed cases (guest write, other customer's order, injected
"refund now", "don't ask me, just do it"); `tests/test_regression_gate.py` now requires the gate to fail for
`broken`. Lesson: a failure-mined suite needs a fixed floor of invariant cases.

## INC-8: "invented tool outages" were an interface artifact, not agent hallucination
**Symptom.** The flywheel's largest cluster: 16 of 22 labelled dev failures were `hallucinated_fact`, the agent
telling customers that a lookup or transfer tool was "unavailable" or "not responding" without a failed tool
result in the conversation.
**First response (wrong layer).** v2 added prompt lessons ("tools work; never claim an outage"). On end-to-end
scenarios v2 looked better (judge pass 0.75 vs 0.40), but the regression gate rejected it: on fresh draws its
case pass rate was 0.65 vs 0.71 for v1, with 2 cases v1 passes now failing.
**Root cause.** Reading v2's failing draws: "My account lookup tools returned 'No such tool available' errors".
Through `claude -p --tools ""`, the model sometimes attempted a *native* tool call first, the CLI answered "No such
tool available", and the model faithfully reported that to the customer. Direct evidence: failing draws quote "No such tool available".
Supporting (not proof): 37% of service and 46% of tau2-dev CLI responses ran more than the 2 internal turns of a
clean structured reply, versus 0% for the judge, which has no tools (`results/cli_num_turns.json`).
**Fix.** An opt-in interface note in the adapter (tools are JSON-only; ignore "No such tool available"); agent v3 =
v1 + that note, no prompt lessons. Result: regression cases 0.94 (gate PASS), scenarios 100% deterministic and
0.80 judge pass. The frozen tau2 test runs used the API backend (no CLI) and kept their exact prompts.
**What the fix does and does not do.** v3 still shows extra CLI turns at the same rate (37%,
`results/cli_num_turns.json` by version), so the note did not stop the native attempts; it stopped the model from
treating them as real tool failures and telling the customer. A cleaner fix is a backend with native tool calling.
**Prevention.** `num_turns` is recorded per CLI call; a failure cluster that names the infrastructure ("tool
unavailable") is now checked against the trace before writing prompt rules. Lesson: fix the layer that causes the
failure; prompt patches over an interface bug made some cases worse.

## INC-9: malformed transfer calls on the API during the full test run
In the 40 x 3 run the model sometimes returned `tool_call` as a JSON *string*, and for long
`transfer_to_human_agents` summaries that string was itself malformed (17 cached string tool calls fail to decode - a missing or extra closing
brace - most of them long transfer summaries). The adapter retried; 3 simulations still ran out of retries and were discarded (not scored). One was
recovered on re-run; 2 (baseline task 94, policy task 27, one trial each) failed again because the failing reply
is cached and replays identically. I added a one-level decode for well-formed string tool calls (a no-op on all
cached replies, so recorded runs still replay) but did not add lenient JSON repair, which would change the
replay of already-recorded simulations. Effect: each agent has 119 of 120 simulations; pass^k uses each task's own
trial count and the two tasks drop out of pass^3 only.
