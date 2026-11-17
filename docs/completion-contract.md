# Completion evidence

The voice interface reports whether a response process finished. The work item's
acceptance evidence determines whether the requested task succeeded. A process
exit, transcript, or green response indicator does not verify a business outcome.

`stream_end` carries the actual `exitCode`, optional `signal`, `resultReceived`,
and nullable `resultFailed`. A response is finished only when exit code is zero,
a terminal result was received, and that result explicitly reports success.
Nonzero exits, signals, and terminal errors are failures. Missing evidence and
legacy messages are unverified. `taskVerified` remains false on this transport.

Desktop and mobile history preserve the completion facts so reopening a session
cannot turn failure or missing evidence into success. Interrupted or ambiguous
work needs destination reconciliation before another action is attempted. The
interface does not automatically retry a business operation or grant additional
tool authority.

The remote interface keeps one active turn on its owning session. Session changes
and new requests wait until that turn finishes or its cancellation settles. A
lost connection records an unknown outcome and releases the session controls;
reconnection never replays the request automatically.

Task ownership, acceptance criteria, approvals, receipts, cost and owner effort
belong in the existing orchestrator and entity records. The dashboard remains a
view and control surface. Missing cost, time, and verification data cannot be
inferred from conversation length or a finished response.

Run the offline regression suite with `npm test --prefix companion`. It uses
injected processes and does not start the companion, contact a provider, or
execute a user task. Installed clients and companion processes must load the
updated source before they can emit the new completion facts.
