# Feedback to product / platform

Things learned while deploying and evaluating that the policy owners or platform teams should change. Each item
names the evidence.

1. **"Authenticate even when the user gives a user id" conflicts with signed-in channels.** The retail policy
   (written for anonymous phone-style support) makes the agent ask a logged-in customer for their email again.
   In smoke scenarios the v1 agent did exactly that before helping (INCIDENTS INC-4 transcripts). Proposal: the
   policy should say "if the channel has authenticated the customer, do not re-verify", and the platform should
   pass the session identity to the agent (we added a session-context line; v2 lesson).
2. **"Explicit user confirmation (yes)" should be a UI event, not a word.** Our gate tests show the model's notion
   of "yes" is fuzzy ("yes, but..." / "sure, what are the options?"). Finance needs proof of consent bound to the
   exact payload. Proposal: confirmation cards with payload hash and expiry (implemented server-side), and the
   policy text should reference them.
3. **Return refunds are "requested", not "refunded".** The policy says the order becomes "return requested" but
   customers (and the simulated customers) hear "refund". Agents should say "refund after we receive the item";
   our store separates `pending_receipt` from `issued` refunds. Suggest the policy states the customer-facing wording.
4. **Refund limits are missing from the policy.** Nothing in the retail policy caps a single refund; our service
   adds $500/action and $1,000/customer/day routed to humans. These belong in the policy so the agent can explain
   them instead of discovering them from a tool error.
5. **Tool-calling interface matters to scores.** On the API, Haiku answered in plain text or called a tool not
   offered as a function in 130 of the first API replies (INC-1). Platform teams comparing models should pin the tool
   interface and report adapter repair rates alongside scores.
