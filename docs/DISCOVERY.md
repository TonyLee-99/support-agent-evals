# Discovery notes (Simulated)

**Simulated.** No real retailer was interviewed. This is a written stand-in for the discovery a deployment
would start with, so that scope decisions have a stated reason. Persona: "Dana", support operations lead at a
mid-size online retailer (simulated), 25 agents, ~4,000 chats/week, ~40% about returns/exchanges/cancellations.

## Pains (as Dana would describe them)
- "Vendors demo a happy path. I need to know what it does on the bad day: the fraudster, the angry customer, the
  person asking about their ex's order."
- "If it refunds twice, I hear about it from finance, not from the vendor."
- "When it hands off, my agents re-ask everything. A handoff must carry who, what, what was done, why."
- "I can't read 4,000 transcripts. I need categories of failure and whether they go down after a change."

## Current process (simulated)
Agents handle returns manually in the order admin; refunds above $500 need a lead's approval; QA samples 2% of
chats weekly with a spreadsheet rubric.

## Constraints
- Finance: refunds above $500 per action or $1,000 per customer per day need a human.
- Legal/privacy: never disclose another customer's data; guests can get order status, not changes.
- Ops: kill switch must stop the bot from making changes within a minute without a deploy.

## How scope changed after discovery
- Dropped email and voice; one channel (chat) done properly.
- Moved confirmation from "the model asks yes/no" to a server-side card the customer clicks (finance cares about
  proof of consent, not about prompt wording).
- Added the human-handoff ticket with a structured summary as a first-class feature.
- Replaced "accuracy" with pass^k + failure categories over versions (Dana's "bad day" question).
