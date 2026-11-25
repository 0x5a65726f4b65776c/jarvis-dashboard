# Repository agent instructions

Read `CLAUDE.md` and `doctrine/agent_delivery.md` before repository work. They define
standing authority, domain boundaries, learning and final delivery contract 2026-11-21.1.
Use the current authoritative agent definition and its declared skills; check
`config/agent_fidelity.json` with `python3 scripts/check_agent_fidelity.py` after changes.

Workers return evidence, outcome, owner/next action and a learning disposition to the
owning orchestrator. The owning orchestrator and independent Hermes reviewer are the
final check before results reach Nick. Preserve domain payloads and tool permissions.
Record corrections in their authoritative home, verify the installed change, and track
later behavior and recurrence. Do not claim that a recorded lesson is validated learning.

Supported Claude Code surfaces use the checked-in hooks; run the opt-in probe in
`doctrine/agent_delivery.md` to observe actual dispatch and refusal. Offline self-tests
do not attest deployment, and unsupported surfaces remain explicitly unverified.
Other runtimes must call the same context/review protocol and supply independent
reviewer events before claiming equivalent enforcement;
a file on disk is not proof that a running client loaded it. After an authorized
instruction change, regenerate the manifest and explicitly refresh runtime context.
