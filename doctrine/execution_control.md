# Bounded execution and requirement coverage

The owning orchestrator allocates work within existing authority. These controls do
not approve spending, sending, tool access, model changes, or external commitments.

## Register the actual requested result

Before delegated implementation, write a requirement plan in `.claude/state/` and
register it with `python3 scripts/agent_gate.py plan --session SESSION --file PATH`.
The plan has `schema_version: 1`, `task_key`, `request`, and a `requirements` array.
Choose `profile: task|research|brief|commercial` for its real purpose (`task` is
the compatibility default). Research, brief, and commercial complete/partial
deliveries require a bound evidence manifest. A partial result identifies every
collection issue by `source_id` in `unresolved`, with issue, owner, and next action.
Each requirement has an `id`, exact `criterion`, and `priority: must|should|could`.
The orchestrator derives the inventory from the original request and agreed scope;
At least one requirement must be essential. Hermes independently checks that the
inventory is complete. A plan records scope,
not permission. It cannot be weakened or replaced within the same user request.

Each delivery acceptance row references `requirement_id` and preserves that
requirement's exact criterion. Every `must` item requires a true acceptance result
with evidence before `outcome: complete`. Optional work cannot offset a missing
essential item. Complete and partial deliveries require the registered plan;
ordinary native worker dispatch also requires it. Partial and blocked packets
carry their actual blocker, owner, and next action. Blocked recovery can report
that work never started without inventing a plan or completion evidence.

Context refresh preserves the registered scope and consumed dispatch allowance.
A new actual user prompt starts a new request; runtime reviewer continuations keep
the existing one. The original request and its requirement plan remain the
reviewer's basis, not the worker's completion claim alone.

## Native dispatch limits

`config/maturation.json` contains trusted `execution` ceilings: `max_dispatches`,
`review_dispatches`, `deadline_seconds`, `review_grace_seconds`, and `max_concurrent`.
The first four govern native Agent/Task admission through the existing PreToolUse
hook. A plan's optional `execution` object can lower those four limits only.
Unknown fields, booleans masquerading as numbers, and higher limits are refused.

Ordinary work and independent review have separate dispatch allowances. The hook
counts each runtime `tool_use_id` once, rejects its reuse for different work, and
stops admitting new work after the deadline. Review has its own bounded grace.
The CLI holds the session lock while checking and recording admission.

These are **dispatch and admission** limits. The native hook cannot cancel an
already running hosted inference or establish its dollar cost. It does not claim
atomic native concurrency enforcement when the host supplies no linked lifecycle
receipt. The monetary and concurrency controller below covers cooperating adapters.

## Adapter cost and worker leases

`scripts/execution_control.py` exposes a standard-library `TaskControl` API and CLI.
It does not launch arbitrary commands or contact a provider. An adapter integrates
it at its existing approved execution boundary:

1. Configure the existing task key and entity with an explicit `total_microusd`,
   `verification_reserve_microusd`, `max_attempts`, `max_concurrent`, `deadline_at`,
   and `authority_ref`. One micro-USD is one millionth of a US dollar. There is no
   default monetary allowance. The authority reference records existing approval.
   `verification_attempts` optionally reserves review attempts within `max_attempts`
   (default one, bounded by the repository's review ceiling). Work cannot consume
   those attempts even when it has exhausted its own retries.
2. Reserve the estimated maximum charge before dispatch. Child reservations use
   `parent` and share the root task's total allowance. Every reservation and its
   reconciliation is serialized under the same local task lock.
3. Pass the lease to the adapter. `assert_active` is an admission check: an
   expired, parked, or revoked lease fails. For a short local result commit, use
   `with control.effect_guard(lease):` to hold the same lock through the write.
   A concurrent park and that commit then have a definite ordering; a park that
   acquires the lock first prevents the stale write. Do not hold this guard across
   a slow network operation. A previously admitted effect can finish before park
   acquires its lock, and time can expire during an in-flight operation. External
   effects require destination-enforced fencing or reconciliation; check-then-send
   alone is not atomic and does not establish remote termination.
4. After observing the worker stop, call `finish` with measured `actual_microusd`
   and its evidence reference. Unknown cost remains null and retains its reserved
   liability. A real charge above its estimate is recorded and parks further work.
5. On cancellation, park the task, request cancellation through the actual adapter,
   then observe termination/provider state. An expired or revoked worker cannot
   finish itself. The owning orchestrator uses `reconcile` with the observed stop
   and measured cost; expiry alone never releases its money or concurrency slot.
6. A parked task may resume only with a changed condition and supporting evidence,
   after all outstanding work and costs are reconciled. Its original allowance,
   attempt count, and deadline remain in force.

State lives under `.claude/state/execution-control/` and is operational bookkeeping,
not a replacement for the canonical run/operation ledgers. Use one durable writer
per task. A local lock does not coordinate independent hosts or fresh clones.
The controller is workflow integrity, not protection against a process that can
rewrite repository files. Unknown third-party charges remain outside its coverage.

## Existing local process boundary

The Jarvis companion runner fences cancelled output and interaction immediately,
requests termination, then escalates if it has not observed process closure. On
POSIX it signals the process group; on Windows the process handle is the supported
boundary. If the direct child exits during cancellation, it immediately signals
remaining members of the owned POSIX group before cancelling delayed escalation.
It also stops at a configurable timeout bounded by two hours. A late
success cannot become a completed response. Cancellation requests do not establish
that remote jobs or provider billing stopped; those require their own receipts.

## Required evidence

Meaningful checks cover concurrent reservations, parent/child allowance sharing,
verification reserve, late workers, unchanged consumed limits after refresh,
unknown cost, and mandatory requirements omitted from otherwise polished output.
Do not report provider-wide spending enforcement until an actual adapter supplies
the pre-call estimate, lifecycle cancellation, and post-call cost telemetry.
