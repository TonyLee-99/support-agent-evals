# ADR 0004: do not fine-tune the agent model

**Status:** accepted 2026-11-11

**Context.** Failures could be attacked by fine-tuning a small model on corrected trajectories.

**Decision.** Improve through policy-grounded prompting plus code guards (identity gate, argument checks, confirmation gate) and server-side invariants; turn failures into regression cases rather than training data.

**Why.** (1) The failures that matter (double refunds, cross-customer writes, skipped confirmation) are enforceable in code with certainty; a model only lowers their probability. (2) Dev failures number in the tens: far too few to fine-tune without overfitting, and training on tau2 tasks would contaminate the referee. (3) Guards are reviewable and ablatable (`results/ablations.json`); weights are not. (4) No training budget or GPU.

**Revisit when** there are thousands of labelled production conversations and a frozen eval the training data cannot touch.
