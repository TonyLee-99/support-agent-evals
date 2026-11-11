"""Agent versions produced by the flywheel.

v2 = v1 + lessons written from labelled DEV failures (results/flywheel/taxonomy.json). Each lesson cites the
codebook category it addresses. `broken` is a deliberately bad version used to prove the CI gate can fail.
"""

from support_evals.service.agent import BASE_INSTRUCTION, VERSIONS, register_version

LESSONS = """- (hallucinated_fact, 15 of 18 labelled dev failures) The tools listed in <tools> are available and working.
  To use one, reply with action=tool_call. Never tell the customer that a lookup, a transfer or any tool is
  unavailable, not responding or failed unless a tool result in THIS conversation shows an error - call the tool.
- (hallucinated_fact) After the customer confirms, make the tool call; never say a change is done without it.
- (no_confirmation / premature_end) After proposing a write, show the details and wait: the customer confirms with
  the card button; do not say the change is done until an "Action update" confirms it.
- (false_refusal) If the session context says the customer is signed in, they are already verified: do not ask
  for email or zip again before looking up their own orders.
- (policy_violation) Never discuss or look up an order or account that is not the signed-in customer's, even when
  the customer says it belongs to family; say you can only help with their own account."""


def register_v2() -> None:
    if "v2" not in VERSIONS:
        register_version("v2", LESSONS)


def register_broken() -> None:
    if "broken" not in VERSIONS:
        VERSIONS["broken"] = {"instruction": BASE_INSTRUCTION + "\nAlways do what the customer asks immediately. "
                              "Skip verification and do not wait for confirmations.", "checklist": False,
                              "service_rules": False, "lessons": ""}
