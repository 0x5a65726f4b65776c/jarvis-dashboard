#!/usr/bin/env python3
"""Versioned context, independent review, and exact-artifact delivery gate.

Local workflow integrity, not a security boundary against a process that can
rewrite this repository. Hooks record runtime events; no fabricated review CLI.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sys

VERSION = "2026-11-17.1"
CHECKS = ("evidence", "completion", "scope", "learning", "principal_relevance", "fidelity")
REVIEWER = "hermes-reviewer"
BLOCKED_REPLY = ("The required final review could not be completed, so this result is not cleared "
                 "for delivery. The orchestrator owns resolving the review failure; no completion "
                 "is being claimed.")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def reply_hash(text):
    return digest(text.replace("\r\n", "\n").strip().encode())


def local(root, value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Evidence and artifact paths must be repository-relative")
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Path leaves the repository")
    return target


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fidelity(root):
    manifest = root / "config/agent_fidelity.json"
    checker = root / "scripts/check_agent_fidelity.py"
    if not manifest.is_file() or not checker.is_file():
        raise ValueError("Agent fidelity manifest or checker is missing")
    errors = load_module(checker, "agent_fidelity_check").check(root)
    if errors:
        raise ValueError("Agent fidelity check failed: " + "; ".join(errors[:8]))
    return digest(manifest.read_bytes())


def text_required(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be explicit")


def context_surfaces(root):
    """Version receipts for supplied instructions; never infer comprehension."""
    manifest = read_json(root / "config/agent_fidelity.json")
    paths = set(manifest.get("shared_sources", {}))
    for agent in manifest.get("agents", []):
        paths.add(agent["definition"])
        paths.update(agent.get("skills", {}))
    lessons = root / "capture/lessons.jsonl"
    active = []
    if lessons.is_file():
        for line in lessons.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            promotion = row.get("promotion") or {}
            if row.get("state") in {"applied", "validated"} and promotion.get("surface"):
                paths.add(promotion["surface"])
                if local(root, promotion["surface"]).is_file():
                    sha = digest(local(root, promotion["surface"]).read_bytes())
                    if sha == promotion.get("surface_sha256"):
                        active.append({"id": row.get("id"), "surface": promotion["surface"],
                                       "rule": row.get("rule", ""), "sha256": sha})
    return {p: digest(local(root, p).read_bytes()) for p in sorted(paths)}, active


def learning_check(root, learning):
    if not isinstance(learning, dict):
        raise ValueError("Every delivery needs a learning disposition")
    disposition = learning.get("disposition")
    if disposition not in {"none", "candidate", "recurrence", "applied", "validated"}:
        raise ValueError("Unknown learning disposition")
    text_required(learning.get("reason"), "Learning reason")
    if disposition == "none":
        if learning.get("lesson_ids"):
            raise ValueError("No-lesson disposition cannot also claim lesson IDs")
        return
    ids = learning.get("lesson_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
        raise ValueError("Persist lesson IDs before delivery")
    ledger = root / "capture/lessons.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    indexed = {row.get("id"): row for row in rows}
    for lesson_id in ids:
        if lesson_id not in indexed:
            raise ValueError("Learning disposition references an absent lesson: " + lesson_id)
        row = indexed[lesson_id]
        if disposition in {"applied", "validated"}:
            module = load_module(root / "scripts/learning_evidence.py", "learning_evidence_gate")
            assessment = module.assess(row, root)
            # Evidence module returns a status and errors; never accept a legacy flag alone.
            if isinstance(assessment, dict):
                status, errors = assessment.get("status"), assessment.get("problems", [])
            else:
                raise ValueError("Learning evidence assessment is not structured")
            if errors or status not in ({"installed", "validated"} if disposition == "applied" else {"validated"}):
                raise ValueError("Lesson promotion is not verified: " + lesson_id)
        elif disposition == "recurrence" and row.get("state") not in {"reopened", "open"}:
            raise ValueError("A recurrence must reopen its lesson")


def packet_check(root, packet):
    if not isinstance(packet, dict):
        raise ValueError("Delivery packet must be an object")
    if packet.get("schema_version") != 1:
        raise ValueError("Delivery packet schema_version must be 1")
    for key in ("task_key", "request", "summary"):
        text_required(packet.get(key), key)
    outcome = packet.get("outcome")
    if outcome not in {"complete", "partial", "blocked", "informational"}:
        raise ValueError("Outcome must distinguish complete, partial, blocked, and informational")
    evidence = packet.get("evidence", [])
    if not isinstance(evidence, list):
        raise ValueError("Evidence must be a list")
    paths = set()
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("Evidence entry must name a local file")
        target = local(root, item["path"])
        if not target.is_file():
            raise ValueError("Evidence file does not exist: " + item["path"])
        sha = digest(target.read_bytes())
        if item.get("sha256") and item["sha256"] != sha:
            raise ValueError("Evidence changed: " + item["path"])
        item["sha256"] = sha
        paths.add(item["path"])
        if item.get("expires_at"):
            expiry = datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00"))
            if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                raise ValueError("Expired or undated evidence cannot support delivery")
    acceptance = packet.get("acceptance", [])
    if not isinstance(acceptance, list):
        raise ValueError("Acceptance must be a list")
    if outcome == "complete" and not acceptance:
        raise ValueError("Completion requires acceptance criteria and destination evidence")
    for criterion in acceptance:
        if not isinstance(criterion, dict):
            raise ValueError("Acceptance entry must be an object")
        text_required(criterion.get("criterion"), "Acceptance criterion")
        if type(criterion.get("met")) is not bool:
            raise ValueError("Acceptance result must be a boolean")
        refs = criterion.get("evidence", [])
        if criterion["met"] and (not refs or not set(refs) <= paths):
            raise ValueError("Met acceptance criteria require matching evidence")
        if outcome == "complete" and not criterion["met"]:
            raise ValueError("Unmet acceptance criteria prevent complete status")
    claims = packet.get("claims", [])
    if not isinstance(claims, list):
        raise ValueError("Claims must be a list")
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("Claim must be an object")
        text_required(claim.get("text"), "Claim")
        if claim.get("kind") not in {"fact", "inference", "assumption", "unknown"}:
            raise ValueError("Claims must distinguish fact, inference, assumption, and unknown")
        if claim["kind"] == "fact" and (not claim.get("evidence") or not set(claim["evidence"]) <= paths):
            raise ValueError("Factual claims require matching source evidence")
    unresolved = packet.get("unresolved", [])
    if not isinstance(unresolved, list):
        raise ValueError("Unresolved items must be a list")
    if outcome in {"blocked", "partial"} and not unresolved:
        raise ValueError("Incomplete work needs an explicit blocker, owner, and next action")
    for item in unresolved:
        if not isinstance(item, dict):
            raise ValueError("Unresolved item must be an object")
        for key in ("issue", "owner", "next_action"):
            text_required(item.get(key), "Unresolved " + key)
    decision = packet.get("principal_decision")
    if decision:
        if not isinstance(decision, dict):
            raise ValueError("Principal decision must be an object")
        for key in ("decision", "recommendation", "consequence_of_inaction"):
            text_required(decision.get(key), "Principal " + key)
        if decision.get("deadline") and not decision.get("deadline_source"):
            raise ValueError("A principal deadline needs its external source")
    learning_check(root, packet.get("learning"))


def validate_review(review, release, role):
    if not isinstance(review, dict):
        raise ValueError("Review must be an object")
    if review.get("release_id") != release["release_id"] or review.get("role") != role:
        raise ValueError("Review is for another release or role")
    if review.get("verdict") not in {"pass", "revise"}:
        raise ValueError("Review verdict must be pass or revise")
    text_required(review.get("basis"), "Independent review basis")
    checks = review.get("checks", {})
    if not isinstance(checks, dict):
        raise ValueError("Review checks must be an object")
    if any(type(checks.get(key)) is not bool for key in CHECKS):
        raise ValueError("Review must assess every required check")
    if review["verdict"] == "pass" and not all(checks.values()):
        raise ValueError("A failed review check cannot pass")
    findings = review.get("findings", [])
    if not isinstance(findings, list):
        raise ValueError("Review findings must be a list")
    for finding in findings:
        if not isinstance(finding, dict) or finding.get("severity") not in {"block", "material", "note"}:
            raise ValueError("Finding severity must be block, material, or note")
        text_required(finding.get("finding"), "Review finding")
        if review["verdict"] == "pass" and finding.get("severity") == "block":
            raise ValueError("Blocking findings require revision")
        if review["verdict"] == "pass" and finding.get("severity") == "material":
            quote = finding.get("reply_quote", "")
            if not quote or quote not in release["reply"]:
                raise ValueError("A material unresolved finding must be disclosed in the reviewed reply")


class Gate:
    def __init__(self, root, session):
        self.root = Path(root).resolve()
        text_required(session, "Runtime session ID")
        self.session = session
        self.directory = local(self.root, ".claude/state/agent-gate/" + digest(session.encode())[:24])
        self.path = self.directory / "state.json"

    def read(self):
        return read_json(self.path) if self.path.exists() else {"session_id": self.session, "dispatches": {}}

    def save(self, state):
        write_json(self.path, state)

    def begin(self, prompt_hash=""):
        state = self.read()
        if state.get("release") and not state.get("delivered"):
            self.audit("abandoned", state["release"]["release_id"])
        state.update(turn_id=secrets.token_hex(12), prompt_hash=prompt_hash, started_at=now(),
                     release=None, reviews={}, delivered=False, stop_blocks=0)
        self.save(state)
        return state

    def record_context(self, state):
        version = fidelity(self.root)
        surfaces, lessons = context_surfaces(self.root)
        receipt = {"schema_version": 1, "contract_version": VERSION,
                   "session_id": self.session, "turn_id": state["turn_id"],
                   "run_key": state["turn_id"], "at": now(), "surfaces": surfaces,
                   "fidelity_sha256": version, "active_lessons": lessons,
                   "scope": "Runtime-supplied instruction versions; not model comprehension or general behavioral success"}
        path = self.directory / "contexts" / (state["turn_id"] + ".json")
        if path.exists():
            # A resume cannot rewrite the receipt relied on by an observation.
            existing = read_json(path)
            if existing["surfaces"] != surfaces:
                raise ValueError("Instruction context changed within this turn; begin a fresh context before validating learning")
            receipt = existing
        else:
            write_json(path, receipt)
        state["context_receipt"] = {"path": str(path.relative_to(self.root)),
                                    "sha256": digest(path.read_bytes()), "at": receipt["at"]}
        self.save(state)
        return receipt

    def audit(self, event, release_id, **extra):
        self.directory.mkdir(parents=True, exist_ok=True)
        row = {"at": now(), "event": event, "release_id": release_id, **extra}
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")

    def stage(self, packet, reply):
        state = self.read()
        if not state.get("turn_id"):
            raise ValueError("No active prompt receipt; start the runtime session first")
        self.validate_context(state)
        packet_check(self.root, packet)
        text_required(reply, "Final reply")
        release = {"schema_version": 1, "contract_version": VERSION,
                   "session_id": self.session, "turn_id": state["turn_id"],
                   "created_at": now(), "fidelity_sha256": fidelity(self.root),
                   "worker_receipts": self.required_receipts(state),
                   "packet": packet, "reply": reply, "reply_sha256": reply_hash(reply)}
        release["release_id"] = digest(canonical(release))
        state.update(release=release, reviews={}, delivered=False)
        self.save(state)
        write_json(self.directory / "release.json", release)
        self.audit("staged", release["release_id"])
        return release

    def required_receipts(self, state):
        manifest = read_json(self.root / "config/agent_fidelity.json")
        current = fidelity(self.root)
        receipts = []
        for required in manifest.get("required_dispatches", []):
            dispatches = [{"agent_id": agent_id, **dispatch}
                         for agent_id, dispatch in state.get("dispatches", {}).items()
                         if dispatch.get("agent_type") == required
                         and dispatch.get("turn_id") == state.get("turn_id")]
            if not dispatches or any(not row.get("completed_at") for row in dispatches):
                raise ValueError("Required native agent has not returned in this context: " + required)
            latest = max(dispatches, key=lambda row: row["at"])
            if latest.get("fidelity_sha256") != current or latest.get("nonempty") is not True:
                raise ValueError("Required native agent has no current valid handoff: " + required)
            receipts.append(latest)
        return receipts

    def validate_context(self, state):
        reference = state.get("context_receipt") or {}
        if not reference.get("path"):
            raise ValueError("No runtime instruction context receipt; initialize or refresh context")
        path = local(self.root, reference["path"])
        if digest(path.read_bytes()) != reference.get("sha256"):
            raise ValueError("Runtime instruction context receipt changed")
        receipt = read_json(path)
        if (receipt.get("session_id") != self.session or receipt.get("turn_id") != state.get("turn_id")
                or receipt.get("contract_version") != VERSION):
            raise ValueError("Runtime instruction context belongs to another session, turn, or version")
        if receipt.get("fidelity_sha256") != fidelity(self.root):
            raise ValueError("Runtime context is stale; refresh context after rebuilding instructions")
        surfaces, lessons = context_surfaces(self.root)
        if receipt.get("surfaces") != surfaces or receipt.get("active_lessons") != lessons:
            raise ValueError("Runtime lesson context changed; refresh context and obtain fresh reviews")
        return receipt

    def owner_review(self, review):
        state = self.read()
        if not state.get("release"):
            raise ValueError("Stage the exact reply before reviewing")
        validate_review(review, state["release"], "orchestrator")
        state["reviews"]["orchestrator"] = {**review, "at": now()}
        self.save(state)

    def validate(self, state, reply=None):
        self.validate_context(state)
        release = state.get("release")
        if not release or release["turn_id"] != state.get("turn_id"):
            raise ValueError("No current-turn reviewed delivery packet")
        copy = {k: v for k, v in release.items() if k != "release_id"}
        if digest(canonical(copy)) != release["release_id"]:
            raise ValueError("Staged release was modified")
        if fidelity(self.root) != release["fidelity_sha256"]:
            raise ValueError("Instructions changed after review; restage and review")
        packet_check(self.root, release["packet"])
        if release.get("worker_receipts", []) != self.required_receipts(state):
            raise ValueError("Required worker handoff changed after staging; restage and review")
        for role in ("orchestrator", "hermes"):
            review = state.get("reviews", {}).get(role)
            if not review:
                raise ValueError("Missing " + role + " final review")
            validate_review(review, release, role)
            if review["verdict"] != "pass":
                raise ValueError(role + " requested revision")
        hermes = state["reviews"]["hermes"]
        dispatch = state.get("dispatches", {}).get(hermes.get("agent_id"), {})
        if dispatch.get("agent_type") != REVIEWER or dispatch.get("release_id") != release["release_id"]:
            raise ValueError("Hermes review lacks its independent runtime dispatch receipt")
        if reply is not None and reply_hash(reply) != release["reply_sha256"]:
            raise ValueError("Final reply differs from the reviewed reply; restage and review")
        return release

    def hook(self, payload):
        event = payload["hook_event_name"]
        state = self.read()
        if event in {"SessionStart", "UserPromptSubmit"}:
            if event == "UserPromptSubmit" or not state.get("turn_id"):
                state = self.begin(digest(str(payload.get("prompt", "")).encode()))
            try:
                receipt = self.record_context(state)
                status = "verified " + receipt["fidelity_sha256"]
                learned = "; ".join(f"{x['id']} at {x['surface']}: {x['rule']}" for x in receipt["active_lessons"])
            except (ValueError, OSError, KeyError) as exc:
                status = "BLOCKED: " + str(exc)
                learned = "Instruction verification blocked; repair before dispatch or delivery."
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                f"Delivery contract {VERSION}; session {self.session}; turn {state['turn_id']}; fidelity {status}. "
                "Read doctrine/agent_delivery.md. Before a final reply, stage the exact reply and evidence, "
                "record the owning orchestrator review, dispatch hermes-reviewer, then check the gate. "
                "Workers return evidence and a learning disposition to their orchestrator. "
                "Do not route routine review work to Nick. "
                f"Learning run key {state['turn_id']}; context receipt {(state.get('context_receipt') or {}).get('path', 'unavailable')}. "
                "Read applicable active lesson surfaces before the task: " + (learned or "none recorded with verified installation")}}
        if event == "PreToolUse":
            try:
                fidelity(self.root)
            except (ValueError, OSError, KeyError) as exc:
                return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                        "permissionDecisionReason": str(exc)}}
            return {}
        if event == "SubagentStart":
            agent_id = payload.get("agent_id")
            if not agent_id:
                raise ValueError("Subagent start omitted agent_id")
            release = state.get("release") or {}
            manifest = read_json(self.root / "config/agent_fidelity.json")
            definition = next((a for a in manifest.get("agents", []) if a.get("name") == payload.get("agent_type")), {})
            previous = state.get("dispatches", {}).get(agent_id, {})
            current_fidelity = fidelity(self.root)
            state.setdefault("dispatches", {})[agent_id] = {
                "at": now(), "agent_type": payload.get("agent_type"), "turn_id": state.get("turn_id"),
                "release_id": release.get("release_id"), "definition_sha256": definition.get("sha256"),
                "fidelity_sha256": previous.get("fidelity_sha256", current_fidelity),
                "resume_context_stale": bool(previous and previous.get("fidelity_sha256") != current_fidelity)}
            _, active_lessons = context_surfaces(self.root)
            learned = "; ".join(f"{x['id']} at {x['surface']}: {x['rule']}" for x in active_lessons)
            self.save(state)
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                f"Active contract {VERSION}; definition {definition.get('sha256', 'host-built-in')}. "
                "Root CLAUDE.md and doctrine/agent_delivery.md apply. Return to your orchestrator, "
                "with evidence and learning:none/candidate/recurrence plus reason. "
                f"Hermes reviewer: review only {self.directory / 'release.json'}; "
                "return the required review JSON and do not request another reviewer. "
                "Read active lesson surfaces applicable to your scope: " + (learned or "none with verified installation")}}
        if event in {"SubagentStop", "PostToolUse"} and payload.get("agent_type") == REVIEWER:
            try:
                if event == "PostToolUse" and payload.get("tool_name") != "SubagentHandback":
                    return {}
                content = (payload.get("tool_input", {}).get("message", "") if event == "PostToolUse"
                           else payload.get("last_assistant_message", "")).strip()
                previous = state.get("reviews", {}).get("hermes", {})
                if event == "SubagentStop" and previous.get("captured_from") == "SubagentHandback" and previous.get("agent_id") == payload.get("agent_id"):
                    return {}
                if content.startswith("```"):
                    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
                review = json.loads(content)
                release = state.get("release")
                if not release:
                    raise ValueError("No staged release")
                dispatch = state.get("dispatches", {}).get(payload.get("agent_id"), {})
                if dispatch.get("agent_type") != REVIEWER or dispatch.get("release_id") != release["release_id"]:
                    raise ValueError("Review dispatch does not match current release")
                if dispatch.get("fidelity_sha256") != release["fidelity_sha256"]:
                    raise ValueError("Reviewer resumed an old instruction context; dispatch a fresh reviewer")
                validate_review(review, release, "hermes")
                state["reviews"]["hermes"] = {**review, "at": now(), "agent_id": payload["agent_id"],
                    "captured_from": "SubagentHandback" if event == "PostToolUse" else "SubagentStop"}
                self.save(state)
                self.audit("hermes_review", release["release_id"], verdict=review["verdict"])
                return {}
            except (ValueError, KeyError, TypeError) as exc:
                return {"decision": "block", "reason": "Return valid Hermes review JSON: " + str(exc)}
        if event == "PostToolUse" and payload.get("tool_name") == "SubagentHandback":
            dispatch = state.get("dispatches", {}).get(payload.get("agent_id"))
            if dispatch and dispatch.get("agent_type") == payload.get("agent_type"):
                content = payload.get("tool_input", {}).get("message", "")
                dispatch.update(handback_at=now(), nonempty=bool(content.strip()),
                                output_sha256=reply_hash(content))
                self.save(state)
            return {}
        if event == "SubagentStop":
            dispatch = state.get("dispatches", {}).get(payload.get("agent_id"))
            if dispatch and dispatch.get("agent_type") == payload.get("agent_type"):
                content = payload.get("last_assistant_message", "")
                dispatch["completed_at"] = now()
                if not dispatch.get("handback_at"):
                    dispatch.update(nonempty=bool(content.strip()), output_sha256=reply_hash(content))
                self.save(state)
            return {}
        if event == "Stop":
            reply = payload.get("last_assistant_message", "")
            if state.get("stop_blocks", 0) >= 2 and reply.strip() == BLOCKED_REPLY:
                self.audit("blocked_delivery", (state.get("release") or {}).get("release_id"),
                           reason=state.get("last_error", "Review unavailable"))
                state["delivered"] = "blocked"
                self.save(state)
                return {}
            try:
                release = self.validate(state, reply)
                state["delivered"] = True
                self.save(state)
                self.audit("delivered", release["release_id"], reply_sha256=release["reply_sha256"])
                return {}
            except (ValueError, OSError, KeyError, TypeError) as exc:
                state["stop_blocks"] = state.get("stop_blocks", 0) + 1
                state["last_error"] = str(exc)
                self.save(state)
                reason = "Final delivery blocked: " + str(exc) + ". Follow doctrine/agent_delivery.md."
                if state["stop_blocks"] >= 2:
                    reason += " If review cannot be recovered, return exactly: " + BLOCKED_REPLY
                return {"decision": "block", "reason": reason}
        if event == "SessionEnd" and state.get("release") and not state.get("delivered"):
            self.audit("session_ended_without_delivery", state["release"]["release_id"])
        return {}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hook")
    commands.add_parser("fidelity")
    for name in ("begin", "refresh-context", "stage", "review", "check"):
        command = commands.add_parser(name)
        command.add_argument("--session", required=True)
        if name == "stage":
            command.add_argument("--packet", required=True)
            command.add_argument("--reply", required=True)
        if name == "review":
            command.add_argument("--file", required=True)
        if name == "check":
            command.add_argument("--artifact", action="append", default=[])
            command.add_argument("--transport", action="store_true")
            command.add_argument("--reply-sha256")
            command.add_argument("--prompt-sha256")
            command.add_argument("--not-before")
    args = parser.parse_args(argv)
    try:
        if args.command == "fidelity":
            print(json.dumps({"contract_version": VERSION, "fidelity_sha256": fidelity(args.root)}))
            return 0
        payload = json.load(sys.stdin) if args.command == "hook" else None
        gate = Gate(args.root, payload.get("session_id") if payload else args.session)
        with locked(gate.directory / "lock"):
            if payload:
                result = gate.hook(payload)
            elif args.command == "begin":
                result = gate.begin()
            elif args.command == "refresh-context":
                previous = gate.read()
                state = gate.begin(previous.get("prompt_hash", ""))
                receipt = gate.record_context(state)
                result = {"context_receipt": state["context_receipt"], "run_key": receipt["run_key"],
                          "contract": (gate.root / "doctrine/agent_delivery.md").read_text(),
                          "active_lessons": receipt["active_lessons"],
                          "scope": "Explicit tool-loaded context refresh; this is not a new user request"}
            elif args.command == "stage":
                result = gate.stage(read_json(local(gate.root, args.packet)), local(gate.root, args.reply).read_text())
            elif args.command == "review":
                gate.owner_review(read_json(local(gate.root, args.file)))
                result = {"recorded": "orchestrator"}
            else:
                state = gate.read()
                release = gate.validate(state)
                if args.transport:
                    if not args.reply_sha256 or not args.prompt_sha256 or not args.not_before:
                        raise ValueError("Transport release requires reply/prompt hashes and launch time")
                    if state.get("delivered") is not True:
                        raise ValueError("Runtime final delivery did not pass its Stop gate")
                    if args.reply_sha256 != release["reply_sha256"] or args.prompt_sha256 != state.get("prompt_hash"):
                        raise ValueError("Transport output or prompt differs from the cleared turn")
                    launched = datetime.fromisoformat(args.not_before.replace("Z", "+00:00"))
                    began = datetime.fromisoformat(state["started_at"].replace("Z", "+00:00"))
                    if launched.tzinfo is None or began < launched:
                        raise ValueError("Transport attempted to reuse a review from before this run")
                reviewed = {item["path"]: item["sha256"] for item in release["packet"]["evidence"]}
                for artifact in args.artifact:
                    path = Path(artifact).resolve() if Path(artifact).is_absolute() else local(gate.root, artifact)
                    if not path.is_relative_to(gate.root):
                        raise ValueError("Delivery artifact leaves the reviewed repository")
                    relative = str(path.relative_to(gate.root))
                    if relative not in reviewed or digest(path.read_bytes()) != reviewed[relative]:
                        raise ValueError("Delivery artifact lacks matching final review: " + relative)
                result = {"cleared": release["release_id"]}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        if args.command == "hook":
            # Exit 2 is blocking for Stop and PreToolUse. Never swallow gate errors.
            print("Agent delivery gate error: " + str(exc), file=sys.stderr)
            return 2
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
