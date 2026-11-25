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

VERSION = "2026-11-21.1"
CHECKS = ("evidence", "completion", "scope", "learning", "principal_relevance", "fidelity")
REVIEWER = "hermes-reviewer"
# A prompt the runtime submits on its own (a background agent's hand-back or a task
# notification) continues the current turn while a reviewer dispatched in this turn is
# working on the open release; it is not a new request. Any other prompt starts a new turn
# and voids the release. A runtime origin decides on its own. Without one, the prompt must
# open with the runtime's own envelope: a marker quoted or pasted later in a prompt does not
# count. The generic peer-message channel is not a continuation, since a peer can ask for
# new work there.
CONTINUATION_KINDS = {"task-notification", "subagent-handback"}
_WRAPPER = r"(?:<system-reminder[^>]*>\s*)?"
CONTINUATION_ENVELOPES = (
    re.compile(_WRAPPER + r"\[SYSTEM NOTIFICATION - NOT USER INPUT\]"),
    re.compile(_WRAPPER + r"<task-notification>"),
    re.compile(_WRAPPER + r"Another Claude session sent a message while you were working:\s*"
               r"<agent-message from=\"(?P<sender>[^\"]+)\">\s*\[Subagent hand-back\]"),
    # The runtime hands the hook the raw frame. The "Another Claude session" line is added
    # only when the frame is rendered for the model (pilot, 2026-11-18), so the raw form is
    # the one a hook actually sees. It must open the prompt, as every envelope must.
    re.compile(_WRAPPER + r"<agent-message from=\"(?P<sender>[^\"]+)\">\s*\[Subagent hand-back\]"),
)
TRACE_CHARS = 160
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


def execution_policy(root, overrides=None):
    policy = read_json(root / "config/maturation.json")["execution"]
    names = {"max_dispatches", "review_dispatches", "deadline_seconds", "review_grace_seconds"}
    result = {}
    for key in names:
        value = policy.get(key)
        minimum = 0 if key in {"max_dispatches", "review_grace_seconds"} else 1
        if type(value) is not int or value < minimum:
            raise ValueError("Invalid repository execution limit: " + key)
        result[key] = value
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, dict) or set(overrides) - names:
        raise ValueError("Unknown task execution limit")
    for key, value in overrides.items():
        minimum = 0 if key in {"max_dispatches", "review_grace_seconds"} else 1
        if type(value) is not int or value < minimum or value > result[key]:
            raise ValueError("Task execution limit cannot widen repository policy: " + key)
        result[key] = value
    return result


def plan_check(root, plan):
    if not isinstance(plan, dict) or type(plan.get("schema_version")) is not int or plan["schema_version"] != 1:
        raise ValueError("Requirement plan must be a schema_version 1 object")
    if set(plan) - {"schema_version", "task_key", "request", "profile", "requirements", "execution"}:
        raise ValueError("Unknown requirement plan field")
    if plan.get("profile", "task") not in {"task", "research", "brief", "commercial"}:
        raise ValueError("Unknown requirement plan profile")
    for key in ("task_key", "request"):
        text_required(plan.get(key), "Plan " + key)
    rows = plan.get("requirements")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Plan must enumerate the original request's requirements")
    identifiers = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "criterion", "priority"}:
            raise ValueError("Each requirement needs id, criterion, and priority")
        text_required(row.get("id"), "Requirement id")
        text_required(row.get("criterion"), "Requirement criterion")
        if row["id"] in identifiers or row["priority"] not in {"must", "should", "could"}:
            raise ValueError("Requirement IDs must be unique and priorities explicit")
        identifiers.add(row["id"])
    if not any(row["priority"] == "must" for row in rows):
        raise ValueError("A requirement plan must contain an essential result")
    execution_policy(root, plan.get("execution"))


def packet_check(root, packet, requirement_plan=None):
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
    if outcome in {"complete", "partial"} and requirement_plan is None:
        raise ValueError("Complete or partial delivery requires a registered requirement plan")
    if requirement_plan is not None:
        plan = requirement_plan["plan"]
        plan_check(root, plan)
        if any(packet[key] != plan[key] for key in ("task_key", "request")):
            raise ValueError("Delivery changed the registered task or request")
        planned = {row["id"]: row for row in plan["requirements"]}
        covered = set()
        seen = set()
        for criterion in acceptance:
            ident = criterion.get("requirement_id")
            if ident not in planned or ident in seen:
                raise ValueError("Acceptance must reference distinct registered requirement IDs")
            seen.add(ident)
            if criterion["criterion"] != planned[ident]["criterion"]:
                raise ValueError("Acceptance cannot substitute a weaker requirement")
            if criterion["met"]:
                covered.add(ident)
        missing = {key for key, row in planned.items() if row["priority"] == "must"} - covered
        if outcome == "complete" and missing:
            raise ValueError("Essential requirement lacks verified evidence: " + ", ".join(sorted(missing)))
        if (plan.get("profile", "task") in {"research", "brief", "commercial"}
                and outcome in {"complete", "partial"} and packet.get("evidence_manifest") is None):
            raise ValueError("This workflow requires a bound evidence manifest")
    if packet.get("evidence_manifest") is not None:
        config = read_json(root / "config/maturation.json")
        module = load_module(root / "scripts/evidence_manifest.py", "delivery_evidence_manifest")
        report = module.check_reference(root, packet["evidence_manifest"], config["entity"],
                                        require_complete=outcome == "complete")
        if report["task_key"] != packet["task_key"]:
            raise ValueError("Evidence manifest belongs to another task")
        if outcome != "complete":
            unresolved = packet.get("unresolved", [])
            disclosed = {row.get("source_id") for row in unresolved if isinstance(row, dict)} if isinstance(unresolved, list) else set()
            missing = {issue["source_id"] for issue in report["issues"]} - disclosed
            if missing:
                raise ValueError("Collection gaps need an unresolved owner/action for each source: " + ", ".join(sorted(missing)))
        receipt = {"reference": report["reference"], "dependencies": report["dependencies"],
                   "complete": report["complete"], "issues": report["issues"]}
        if packet.get("evidence_manifest_receipt") is not None and packet["evidence_manifest_receipt"] != receipt:
            raise ValueError("Evidence inputs or collection coverage changed after staging")
        packet["evidence_manifest"] = report["reference"]
        packet["evidence_manifest_receipt"] = receipt
    elif packet.get("evidence_manifest_receipt") is not None:
        raise ValueError("Evidence manifest receipt has no source manifest")
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
    mode = packet.get("delivery_mode", "visible")
    if mode not in {"visible", "silent"}:
        raise ValueError("Delivery mode must be visible or silent")
    if mode == "silent":
        if outcome != "complete" or unresolved or decision:
            raise ValueError("Silent delivery requires complete work with no unresolved issue or principal decision")
        silence = packet.get("silence")
        if not isinstance(silence, dict) or set(silence) != {
                "reason", "condition", "evidence", "request_input_id", "request_quote"}:
            raise ValueError("Silent delivery requires its reason, condition, evidence, and original-input quote")
        for key in ("reason", "condition", "request_input_id", "request_quote"):
            text_required(silence[key], "Silence " + key)
        refs = silence["evidence"]
        if (not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs)
                or len(set(refs)) != len(refs) or not set(refs) <= paths):
            raise ValueError("Silence condition requires distinct reviewed evidence files")
        if any(not local(root, ref).read_bytes().strip() for ref in refs):
            raise ValueError("Silence condition evidence cannot be empty")
    elif "silence" in packet:
        raise ValueError("Silence metadata requires explicit silent delivery mode")
    learning_check(root, packet.get("learning"))


def delivery_reply_check(packet, reply, inputs):
    if packet.get("delivery_mode", "visible") != "silent":
        text_required(reply, "Final reply")
        return
    if not isinstance(reply, str) or reply != "":
        raise ValueError("Silent delivery requires an exact empty final reply")
    silence = packet["silence"]
    source = next((row for row in inputs["inputs"]
                   if row["input_id"] == silence["request_input_id"]), None)
    if source is None or silence["request_quote"] not in source["text"]:
        raise ValueError("Silence authorization quote must match a preserved original input")


def machine_continuation(payload, state=None):
    origin = payload.get("origin")
    if origin is not None:
        kind = origin.get("kind") if isinstance(origin, dict) else origin
        return isinstance(kind, str) and kind in CONTINUATION_KINDS
    head = str(payload.get("prompt", "")).lstrip()
    for envelope in CONTINUATION_ENVELOPES:
        match = envelope.match(head)
        if not match:
            continue
        sender = match.groupdict().get("sender")
        if sender is None:
            return True
        # A hand-back frame counts only from a subagent this turn dispatched. A peer session
        # can open its message with the same marker, and its id is never a dispatch id here.
        state = state or {}
        dispatch = state.get("dispatches", {}).get(sender) or {}
        return bool(state.get("turn_id")) and dispatch.get("turn_id") == state.get("turn_id")
    return False


def prompt_trace(payload):
    """What the audit keeps about a prompt: enough to diagnose a missed continuation, not the text."""
    prompt = str(payload.get("prompt", ""))
    head = prompt.lstrip()
    origin = payload.get("origin")
    if isinstance(origin, dict):
        origin = {"kind": origin.get("kind") if isinstance(origin.get("kind"), str) else None,
                  "keys": sorted(str(k) for k in origin)[:10]}
    elif isinstance(origin, str):
        origin = origin[:60]
    else:
        origin = None
    return {"origin_present": "origin" in payload, "origin_type": type(payload.get("origin")).__name__,
            "origin": origin, "prompt_chars": len(prompt),
            "prompt_head": head[:TRACE_CHARS] if head[:1] in ("<", "[") else None}


def awaiting_review(state):
    # Any reviewer dispatched in this turn counts, so a revise loop (review, fix, restage)
    # survives a notification that lands before the next reviewer starts. Keeping the
    # release grants nothing: delivery still needs a Hermes review bound to that release.
    release = state.get("release")
    if not release or state.get("delivered") or release.get("turn_id") != state.get("turn_id"):
        return False
    return any(row.get("agent_type") == REVIEWER and row.get("turn_id") == state.get("turn_id")
               for row in state.get("dispatches", {}).values())


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
    if review["verdict"] == "pass":
        validate_request_review(review.get("request_review"), release)
        if release["packet"].get("delivery_mode", "visible") == "silent":
            silent = review.get("silence_review")
            if (not isinstance(silent, dict) or set(silent) != {
                    "authorized", "condition_verified", "no_material_omission", "basis"}
                    or any(silent[key] is not True for key in (
                        "authorized", "condition_verified", "no_material_omission"))):
                raise ValueError("Silent delivery requires an independent passing silence review")
            text_required(silent["basis"], "Independent silence review basis")
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


def validate_request_review(review, release):
    """Bind a review to the actual inputs, not just the author's interpretation.

    Semantic completeness remains an independent review judgment. The gate makes
    the original text available and requires an explicit accounting for every
    captured input; it does not pretend to extract all requirements from prose.
    """
    bundle = release.get("request_inputs")
    if not isinstance(bundle, dict) or not isinstance(review, dict):
        raise ValueError("Review must account for the preserved original request inputs")
    if review.get("sha256") != bundle.get("sha256"):
        raise ValueError("Review is not bound to the current original request inputs")
    omissions = review.get("omissions")
    if not isinstance(omissions, list) or omissions:
        raise ValueError("Original request omissions require revision, not a passing review")
    rows = review.get("inputs")
    if not isinstance(rows, list):
        raise ValueError("Review must map each original input to scope or explain its disposition")
    planned = {r["id"] for r in (release.get("requirement_plan") or {}).get("plan", {}).get("requirements", [])}
    expected = {r["input_id"] for r in bundle["inputs"]}
    seen, covered = set(), set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"input_id", "disposition", "requirement_ids", "reason"}:
            raise ValueError("Input review requires input_id, disposition, requirement_ids, and reason")
        ident = row["input_id"]
        if not isinstance(ident, str) or ident not in expected or ident in seen:
            raise ValueError("Review must account for each captured original input exactly once")
        seen.add(ident)
        text_required(row["reason"], "Input scope review reason")
        refs = row["requirement_ids"]
        if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
            raise ValueError("Input review requirement_ids must be a list of registered IDs")
        if len(set(refs)) != len(refs) or not set(refs) <= planned:
            raise ValueError("Input review references duplicate or unregistered requirements")
        if row["disposition"] == "covered":
            if not refs:
                raise ValueError("Covered input needs registered requirement IDs")
            covered.update(refs)
        elif row["disposition"] not in {"context", "superseded"} or refs:
            raise ValueError("Context or superseded input needs a reason and no claimed requirement coverage")
    if seen != expected:
        raise ValueError("Review omitted an original request or steering input")
    if bundle["availability"] != "available":
        if release["packet"]["outcome"] != "blocked":
            raise ValueError("Missing original request inputs cannot support completion")
        text_required(review.get("unavailable_reason"), "Unavailable original-input disclosure")
    elif release["packet"]["outcome"] in {"complete", "partial"}:
        must = {r["id"] for r in release["requirement_plan"]["plan"]["requirements"] if r["priority"] == "must"}
        if not must <= covered:
            raise ValueError("Review did not map every essential requirement to an original input")


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

    def current_fidelity(self):
        return fidelity(self.root)

    def capture_request(self, state, text, *, origin=None, source="UserPromptSubmit"):
        """Keep exact text only in ignored local state; event logs retain hashes.

        Event delivery is a local trust assumption, not proof of a human's
        identity. Source documents and peer/system payloads gain no authority.
        """
        if not isinstance(text, str):
            raise ValueError("Original request text must be a string")
        if digest(text.encode()) != state.get("prompt_hash"):
            raise ValueError("Original request text does not match the runtime prompt hash")
        refs = state.setdefault("request_inputs", [])
        receipt = {"schema_version": 1, "session_id": self.session,
                   "input_id": secrets.token_hex(12), "captured_at": now(),
                   "turn_id": state["turn_id"], "source": source, "origin": origin,
                   "previous_sha256": refs[-1]["sha256"] if refs else None,
                   "prompt_sha256": state["prompt_hash"], "text": text}
        path = self.directory / "requests" / (receipt["input_id"] + ".json")
        write_json(path, receipt)
        refs.append({"path": str(path.relative_to(self.root)), "sha256": digest(path.read_bytes())})
        state["current_input_id"] = receipt["input_id"]
        self.save(state)
        self.audit("request_preserved", None, input_id=receipt["input_id"],
                   prompt_sha256=receipt["prompt_sha256"], source=source)
        return receipt

    def request_bundle(self, state):
        inputs, previous = [], None
        for ref in state.get("request_inputs", []):
            path = local(self.root, ref["path"])
            if digest(path.read_bytes()) != ref.get("sha256"):
                raise ValueError("Preserved original request input changed")
            receipt = read_json(path)
            if (receipt.get("session_id") != self.session or receipt.get("previous_sha256") != previous
                    or not isinstance(receipt.get("text"), str)
                    or digest(receipt["text"].encode()) != receipt.get("prompt_sha256")):
                raise ValueError("Original request input belongs to another session or its chain changed")
            previous = ref["sha256"]
            inputs.append({**receipt, "reference": ref})
        current = inputs[-1] if inputs else {}
        available = bool(current and current["text"].strip() and current["input_id"] == state.get("current_input_id")
                         and current["prompt_sha256"] == state.get("prompt_hash"))
        bundle = {"schema_version": 1, "session_id": self.session,
                  "current_input_id": state.get("current_input_id"), "inputs": inputs,
                  "availability": "available" if available else "unavailable"}
        if not available:
            bundle["reason"] = ("Original prompt text was not captured. For a legacy hash-only turn, use "
                                "recover-request with exact matching text, or submit the original request again; "
                                "an agent restatement cannot replace it.")
        bundle["sha256"] = digest(canonical(bundle))
        return bundle

    def recover_request(self, text):
        state = self.read()
        if self.request_bundle(state)["availability"] == "available":
            raise ValueError("Original request is already preserved; recovery cannot replace it")
        if not state.get("turn_id") or not state.get("prompt_hash"):
            raise ValueError("No legacy runtime prompt hash to recover; submit the original request")
        receipt = self.capture_request(state, text, source="legacy_hash_recovery")
        state.update(release=None, reviews={}, delivered=False)
        self.save(state)
        return {"input_id": receipt["input_id"], "scope":
                "Exact text matched the saved prompt hash; not a new runtime dispatch or recovery of earlier uncaptured prompts"}

    def begin(self, prompt_hash="", preserve_plan=False, **why):
        state = self.read()
        if state.get("release") and not state.get("delivered"):
            self.audit("abandoned", state["release"]["release_id"], **why)
        state.update(turn_id=secrets.token_hex(12), prompt_hash=prompt_hash, started_at=now(),
                     release=None, reviews={}, delivered=False, stop_blocks=0)
        if not preserve_plan:
            state.update(requirement_plan=None, dispatch_allowance={"started_at": state["started_at"], "calls": {}})
        self.save(state)
        return state

    def plan(self, plan):
        state = self.read()
        if not state.get("turn_id"):
            raise ValueError("Start a runtime turn before registering its requirements")
        self.validate_context(state)
        plan_check(self.root, plan)
        inputs = self.request_bundle(state)
        if inputs["availability"] != "available":
            raise ValueError(inputs["reason"])
        previous = state.get("requirement_plan")
        if previous:
            if previous["plan"] != plan:
                raise ValueError("Registered requirements are immutable for this request")
            if previous.get("request_inputs_sha256") == inputs["sha256"]:
                return previous
            # An exact legacy plan may acquire its restored input binding; its
            # requirements do not change, and every old review is invalidated.
            if previous.get("request_inputs_sha256") is not None:
                raise ValueError("Registered requirements belong to other request inputs")
        if state.get("release"):
            raise ValueError("Register requirements before staging a release")
        record = {"plan": plan, "prompt_sha256": state["prompt_hash"], "created_at": now(),
                  "request_inputs_sha256": inputs["sha256"]}
        record["sha256"] = digest(canonical(record))
        state["requirement_plan"] = record
        self.save(state)
        write_json(self.directory / "requirements.json", record)
        self.audit("requirements_registered", None, plan_sha256=record["sha256"])
        return record

    def requirement_plan(self, state):
        record = state.get("requirement_plan")
        if record is None:
            return None
        if digest(canonical({key: value for key, value in record.items() if key != "sha256"})) != record.get("sha256"):
            raise ValueError("Registered requirement plan changed")
        if record.get("prompt_sha256") != state.get("prompt_hash"):
            raise ValueError("Requirement plan belongs to another request")
        if record.get("request_inputs_sha256") != self.request_bundle(state)["sha256"]:
            raise ValueError("Requirement plan lacks current original-input binding; recover missing text and re-register the unchanged plan")
        plan_check(self.root, record["plan"])
        return record

    def admit_dispatch(self, state, payload):
        if payload.get("tool_name") not in {"Agent", "Task"}:
            return
        if not state.get("turn_id"):
            raise ValueError("No active runtime turn for dispatch")
        record = self.requirement_plan(state)
        limits = execution_policy(self.root, (record or {}).get("plan", {}).get("execution"))
        call_id = payload.get("tool_use_id")
        text_required(call_id, "Runtime dispatch tool_use_id")
        tool_input = payload.get("tool_input", {})
        if not isinstance(tool_input, dict):
            raise ValueError("Dispatch input must be an object")
        review = tool_input.get("subagent_type") == REVIEWER
        if not review and record is None:
            raise ValueError("Register the original requirement plan before delegated work")
        if review and not state.get("release"):
            raise ValueError("Stage the review bundle before dispatching its reviewer")
        allowance = state.setdefault("dispatch_allowance", {"started_at": state["started_at"], "calls": {}})
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(allowance["started_at"])).total_seconds()
        ceiling = limits["deadline_seconds"] + (limits["review_grace_seconds"] if review else 0)
        if elapsed < 0 or elapsed >= ceiling:
            raise ValueError("Dispatch deadline reached; checkpoint and report blocked work")
        calls = allowance["calls"]
        identity = {"review": review, "input_sha256": digest(canonical(tool_input))}
        if call_id in calls:
            if calls[call_id] != identity:
                raise ValueError("Runtime dispatch ID was reused for different work")
            return
        used = sum(row["review"] == review for row in calls.values())
        ceiling = limits["review_dispatches"] if review else limits["max_dispatches"]
        if used >= ceiling:
            raise ValueError("Review dispatch allowance exhausted" if review else "Work dispatch allowance exhausted; verification reserve remains available")
        calls[call_id] = identity
        self.save(state)

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
        requirement_plan = self.requirement_plan(state)
        packet_check(self.root, packet, requirement_plan)
        inputs = self.request_bundle(state)
        if inputs["availability"] != "available" and packet["outcome"] != "blocked":
            raise ValueError(inputs["reason"])
        delivery_reply_check(packet, reply, inputs)
        release = {"schema_version": 1, "contract_version": VERSION,
                   "session_id": self.session, "turn_id": state["turn_id"],
                   "created_at": now(), "fidelity_sha256": fidelity(self.root),
                   "worker_receipts": self.required_receipts(state),
                   "requirement_plan": requirement_plan,
                   "request_inputs": inputs,
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
        if release.get("request_inputs") != self.request_bundle(state):
            raise ValueError("Original request inputs changed or were not preserved; restage and review")
        requirement_plan = self.requirement_plan(state)
        if release.get("requirement_plan") != requirement_plan:
            raise ValueError("Requirement plan changed after review")
        packet_check(self.root, release["packet"], requirement_plan)
        delivery_reply_check(release["packet"], release["reply"], release["request_inputs"])
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
        if reply is not None:
            delivery_reply_check(release["packet"], reply, release["request_inputs"])
            if reply_hash(reply) != release["reply_sha256"]:
                raise ValueError("Final reply differs from the reviewed reply; restage and review")
        return release

    def hook(self, payload):
        event = payload["hook_event_name"]
        state = self.read()
        if event in {"PreToolUse", "PostToolUse"} and payload.get("tool_name") == "Read":
            if not state.get("runtime_probe_id"):
                return {}
            probe = load_module(self.root / "scripts/runtime_probe.py", "agent_runtime_probe")
            return probe.observe_read(self, payload)
        if event in {"SessionStart", "UserPromptSubmit"}:
            if event == "UserPromptSubmit" and machine_continuation(payload, state) and awaiting_review(state):
                self.audit("continued", state["release"]["release_id"], hook_event=event,
                           prompt_sha256=digest(str(payload.get("prompt", "")).encode()),
                           **prompt_trace(payload))
            elif event == "UserPromptSubmit" or not state.get("turn_id"):
                # Record why a release was abandoned: what origin the runtime sent and, for a
                # machine-shaped prompt only, how it opened, so a continuation that fails to
                # match can be diagnosed from the local audit log. State stays in .claude/state.
                state = self.begin(digest(str(payload.get("prompt", "")).encode()), hook_event=event,
                                   **prompt_trace(payload))
                if event == "UserPromptSubmit":
                    self.capture_request(state, payload.get("prompt"), origin=prompt_trace(payload)["origin"])
            try:
                receipt = self.record_context(state)
                status = "verified " + receipt["fidelity_sha256"]
                learned = "; ".join(f"{x['id']} at {x['surface']}: {x['rule']}" for x in receipt["active_lessons"])
            except (ValueError, OSError, KeyError) as exc:
                status = "BLOCKED: " + str(exc)
                learned = "Instruction verification blocked; repair before dispatch or delivery."
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                f"Delivery contract {VERSION}; session {self.session}; turn {state['turn_id']}; fidelity {status}. "
                "Read doctrine/agent_delivery.md. Register the original request's essential requirements "
                "with agent_gate.py plan before substantive delegated work. Before a final reply, stage the exact reply and evidence, "
                "record the owning orchestrator review, dispatch hermes-reviewer in the foreground and wait for "
                "its verdict, then check the gate. "
                "The staged request_inputs preserve original text and steering; both reviews must map each input "
                "in request_review, identify superseded/context inputs explicitly, and reject omitted requirements. "
                "Workers return evidence and a learning disposition to their orchestrator. "
                "Do not route routine review work to Nick. "
                f"Learning run key {state['turn_id']}; context receipt {(state.get('context_receipt') or {}).get('path', 'unavailable')}. "
                "Read applicable active lesson surfaces before the task: " + (learned or "none recorded with verified installation")}}
        if event == "PreToolUse":
            try:
                fidelity(self.root)
                self.admit_dispatch(state, payload)
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
                current = (state.get("release") or {}).get("release_id")
                stale = state.get("dispatches", {}).get(payload.get("agent_id"), {})
                if (current and stale.get("agent_type") == REVIEWER and stale.get("turn_id") == state.get("turn_id")
                        and stale.get("release_id") and stale.get("release_id") != current):
                    # A reviewer of a release this turn has since replaced: record nothing, let it stop.
                    self.audit("stale_review_ignored", stale["release_id"], current=current)
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
            if (state.get("stop_blocks", 0) >= 2 and isinstance(reply, str)
                    and reply.strip() == BLOCKED_REPLY):
                self.audit("blocked_delivery", (state.get("release") or {}).get("release_id"),
                           reason=state.get("last_error", "Review unavailable"))
                state["delivered"] = "blocked"
                self.save(state)
                return {}
            try:
                if "last_assistant_message" not in payload or not isinstance(reply, str):
                    raise ValueError("Final delivery requires an explicit final message string from the runtime")
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
    for name in ("begin", "refresh-context", "recover-request", "plan", "stage", "review", "check"):
        command = commands.add_parser(name)
        command.add_argument("--session", required=True)
        if name == "stage":
            command.add_argument("--packet", required=True)
            command.add_argument("--reply", required=True)
        if name in {"review", "plan", "recover-request"}:
            command.add_argument("--file", required=True)
        if name == "check":
            command.add_argument("--artifact", action="append", default=[])
            command.add_argument("--require-complete", action="store_true",
                                 help="require verified task completion, not only a reviewed delivery")
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
                state = gate.begin(previous.get("prompt_hash", ""), preserve_plan=True)
                receipt = gate.record_context(state)
                result = {"context_receipt": state["context_receipt"], "run_key": receipt["run_key"],
                          "contract": (gate.root / "doctrine/agent_delivery.md").read_text(),
                          "active_lessons": receipt["active_lessons"],
                          "scope": "Explicit tool-loaded context refresh; this is not a new user request"}
            elif args.command == "plan":
                result = gate.plan(read_json(local(gate.root, args.file)))
            elif args.command == "recover-request":
                result = gate.recover_request(local(gate.root, args.file).read_bytes().decode("utf-8"))
            elif args.command == "stage":
                result = gate.stage(read_json(local(gate.root, args.packet)), local(gate.root, args.reply).read_text())
            elif args.command == "review":
                gate.owner_review(read_json(local(gate.root, args.file)))
                result = {"recorded": "orchestrator"}
            else:
                state = gate.read()
                release = gate.validate(state)
                if args.require_complete and release["packet"]["outcome"] != "complete":
                    raise ValueError("Reviewed delivery is not complete")
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
