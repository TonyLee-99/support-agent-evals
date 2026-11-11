# ADR 0001: tau2-bench retail as the external referee

**Status:** accepted 2026-11-11

**Context.** Own scenarios are written by the same people who build the agent; scores on them drift toward passing. We need a referee we did not write.

**Decision.** Use sierra-research/tau2-bench (MIT, pinned commit c06b07dd) retail domain, unmodified: its tools, policy, tasks, user simulator and evaluator (DB-state check + NL assertions). Integrate through its public Layer-2 API and a LiteLLM custom provider; no tau2 file is edited (`scripts/check_tau2_pristine.py` hashes them). Test = tau2's own official `test` split, frozen before tuning; a seeded 10-task subset is run by default (`N` knob).

**Rejected.** (a) Writing our own simulated-customer benchmark only: no external validity. (b) Modifying tau2 tools to match our service (ownership, confirmation cards): would make scores incomparable to anyone else's tau2 numbers; instead the service gets its own deterministic acceptance tests and the two score families are reported separately.

**Consequences.** Benchmark numbers say how the agent *method* does in Sierra's environment, not that the service is safe. Model, user simulator, NL grader and subset differ from the leaderboard; every report lists the differences and claims no leaderboard comparability.
