# ADR 0005: SQLite with one serialised transaction for the money path

**Status:** accepted 2026-11-11

**Decision.** The confirm path runs in one `BEGIN IMMEDIATE` transaction: idempotency lookup, action state (pending, unexpired, single-use, payload hash), ownership, order status, already-refunded check, limits, effects, idempotency record. `UNIQUE(order_id, item_id)` on refunds makes a double refund a constraint violation, not a code path. Tested with 32 real threads on one file.

**Rejected.** Postgres now: one more service to run for a demo; the same transaction shape ports directly (SERIALIZABLE or SELECT ... FOR UPDATE). Application-level locks: break across processes.
