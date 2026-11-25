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

On POSIX hosts, local desktop and companion CLI launches use an owned process
group. Cancellation signals that group and escalates after two seconds if needed;
an exiting leader does not leave its remaining group members running. The macOS
native bridge preserves this ownership through its asynchronous spawn registry.
An execution deadline defaults to two hours and can only be lowered with
`widgets.voiceCommand.executionTimeoutMs` (local desktop) or
`companion.executionTimeoutMs` (companion). Retry and answer-resume launches have
the same bound. A cancellation request cannot undo an effect already completed
or guarantee that deliberately detached descendants or hosted work stopped.
Windows retains direct-child cancellation; process-group parity is POSIX-only.

Task ownership, acceptance criteria, approvals, receipts, cost and owner effort
belong in the existing orchestrator and entity records. The dashboard remains a
view and control surface. Missing cost, time, and verification data cannot be
inferred from conversation length or a finished response.

Run the offline regression suite with `npm test --prefix companion`. It uses
injected processes and short-lived local process fixtures; it does not start a
network server, contact a provider, or execute a user task. Native process tests
also require `cargo test` in `macos/src-tauri` on a supported build host. Installed
clients and companion processes must load the updated source before the changed
runtime behavior is available.
