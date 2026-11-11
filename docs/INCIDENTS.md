# Incidents

Real problems hit while building and evaluating. Nothing here is invented; each links to the fix.

## INC-1 (full postmortem): API tool-call mapping silently turned interface failures into agent failures

**Date** 2026-11-11, during the frozen-test run on the Anthropic API backend.

**Symptom.** The first API run logged dozens of `AdapterError: no valid turn after retries: reply is not a JSON
object`. tau2 turned each into an agent error, so simulations ended early and were scored 0 for *both* agents.

**Impact.** Had it gone unnoticed, the headline benchmark numbers would have measured our adapter, not the
agents. Spend: about $0.20 of the $3.50 budget on runs that were thrown away.

**Root cause.** Two mismatches between the API backend and the strict-JSON contract the CLI backend follows:
1. Haiku often answered with plain text instead of calling the `respond` tool (50 cached replies).
2. With tool schemas described in the system prompt, it sometimes emitted a `tool_use` block named after a
   retail tool (e.g. `get_order_details`) instead of `respond` (80 cached replies, `stop_reason=tool_use`,
   `structured=null`).
A third, latent bug made it worse: the provider's adapter failure raised `AdapterError`, which our runner did not
classify as an infrastructure error, so the broken simulations were *scored* rather than discarded.

**Detection.** Reading the run log instead of only the final reward line; counting cache records with
`structured=null` grouped by `stop_reason` and tag.

**Fix.** (a) Map plain text to `{"action": "message"}` and a direct `tool_use` to `{"action": "tool_call"}` -
both are exactly what the native API means; count them in response meta. (b) Adapter failures now mark the
simulation as an infra error: it is discarded and re-run, and the count is reported (`discarded_sims`).
(c) Purged the bad cache entries and re-ran both agents from scratch.

**Prevention.** `adapter_stats` and `discarded_sims` are part of every summary and shown on the dashboard; a run
with discarded simulations is visible, never silent. Lesson: when the measurement pipeline can fail, its failures
must be a separate outcome from the system-under-test's failures.

## INC-2: simulated customer double-encoded its messages
Pilot runs showed user-simulator turns like `{"message": "Okay, so ..."}` inside the message text (27 of 54 user
turns in the first pilots). Cause: the model put the JSON envelope into the `message` field. Fix: one-level unwrap
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
