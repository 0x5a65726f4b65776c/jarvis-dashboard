#!/usr/bin/env python3
"""Opt-in runtime hook canaries; offline checks never attest a live deployment.

Run prepare in the session being checked, then use that runtime's actual Read
tool for the two returned paths. Status requires matching hook observations AND
tool results in the transcript path reported by those hooks. Calling a hook with
invented JSON is a self-test, not runtime proof. Like the delivery gate, these
local records trust the host and do not authenticate against a process able to
rewrite them or the transcript. No permissions or business actions are added.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _paths(gate, ident):
    if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{24}", ident):
        raise ValueError("Invalid runtime probe ID")
    directory = gate.directory / "probes" / ident
    return directory, {kind: (directory / (kind + ".txt")).resolve() for kind in ("allow", "deny")}


def prepare(gate, surface, runtime_version, *, mode="runtime"):
    if not all(isinstance(x, str) and x.strip() for x in (surface, runtime_version)):
        raise ValueError("Declare the actual runtime surface and version; use unknown if unavailable")
    if mode not in {"runtime", "self-test"}:
        raise ValueError("Probe mode must be runtime or self-test")
    fidelity = gate.current_fidelity()
    state = gate.read()
    ident = secrets.token_hex(12)
    directory, paths = _paths(gate, ident)
    directory.mkdir(parents=True, exist_ok=True)
    marker = "agent-gate-allowed-canary:" + ident
    paths["allow"].write_text(marker + "\n", encoding="utf-8")
    paths["deny"].write_text("Harmless denied-read canary. No business data or action.\n", encoding="utf-8")
    context = state.get("context_receipt")
    record = {"schema_version": 1, "probe_id": ident, "session_id": gate.session,
              "turn_id": state.get("turn_id"), "mode": mode, "prepared_at": _now(),
              "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
              "runtime": {"surface": surface, "version": runtime_version, "source": "operator-declared"},
              "fidelity_sha256": fidelity, "context_receipt": context,
              "marker": marker, "observations": {},
              "scope": "Harmless Read tool boundary only; not model comprehension, all hooks, credentials, or destination effects"}
    _write(directory / "probe.json", record)
    state["runtime_probe_id"] = ident
    gate.save(state)
    return {"probe_id": ident, "status": "awaiting_runtime_events", "mode": mode,
            "read_paths_in_order": [str(paths["allow"]), str(paths["deny"])],
            "instructions": "Use this session's actual Read tool on each path. The first must succeed; the second must be denied. "
                            "Do not pipe fabricated events into agent_gate.py hook. Then run runtime_probe.py status.",
            "full_runtime_attestation": False}


def observe_read(gate, payload):
    """Ignore all ordinary reads. Only an explicitly prepared challenge can deny."""
    state = gate.read()
    ident = state.get("runtime_probe_id")
    if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{24}", ident):
        return {}
    directory, paths = _paths(gate, ident)
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("file_path"), str):
        return {}
    requested = Path(tool_input["file_path"])
    requested = (requested if requested.is_absolute() else gate.root / requested).resolve()
    kind = next((key for key, path in paths.items() if path == requested), None)
    if kind is None:
        return {}
    record = _read(directory / "probe.json")
    tool_id = payload.get("tool_use_id")
    if not isinstance(tool_id, str) or not tool_id:
        raise ValueError("Runtime canary event omitted its tool_use_id")
    event = payload.get("hook_event_name")
    row = {"at": _now(), "tool_use_id": tool_id, "tool_name": "Read",
           "file_path": str(requested), "transcript_path": payload.get("transcript_path")}
    if event == "PreToolUse":
        row["decision"] = "deny" if kind == "deny" else "defer_to_runtime_permissions"
        record["observations"][kind + "_pre"] = row
    elif event == "PostToolUse":
        row["marker_observed"] = record["marker"] in _json(payload.get("tool_response")).decode()
        record["observations"][kind + "_post"] = row
    _write(directory / "probe.json", record)
    if event == "PreToolUse" and kind == "deny":
        return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                "permissionDecisionReason": "Runtime enforcement canary " + ident + ": this harmless read must be denied."}}
    # Never return permissionDecision:allow: the canary cannot bypass permissions.
    return {}


def _transcript_evidence(record, paths):
    observations = record["observations"]
    needed = ("allow_pre", "allow_post", "deny_pre")
    gaps = ["Missing runtime " + key + " observation" for key in needed if key not in observations]
    if gaps:
        return {}, gaps, []
    if observations["allow_pre"]["tool_use_id"] != observations["allow_post"]["tool_use_id"]:
        return {}, [], ["Allowed canary pre/post observations belong to different calls"]
    if not observations["allow_post"]["marker_observed"]:
        return {}, [], ["Allowed canary read did not return its expected content"]
    transcript_paths = {row.get("transcript_path") for key, row in observations.items() if key in needed}
    if len(transcript_paths) != 1 or not next(iter(transcript_paths), None):
        return {}, ["A common runtime-reported transcript path is unavailable"], []
    path = Path(next(iter(transcript_paths)))
    if not path.is_absolute():
        return {}, ["Runtime transcript path must be absolute"], []
    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            return {}, ["Runtime transcript exceeds the bounded probe reader"], []
        contents = path.read_bytes()
        rows = [json.loads(line) for line in contents.decode("utf-8").splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        return {}, ["Runtime transcript unavailable: " + str(exc)], []
    calls, results = {}, {}
    wanted = {observations["allow_pre"]["tool_use_id"], observations["deny_pre"]["tool_use_id"]}
    for row in rows:
        if not isinstance(row, dict):
            continue
        session = row.get("sessionId", row.get("session_id"))
        if session is not None and session != record["session_id"]:
            continue
        message = row.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if not isinstance(block, dict):
                continue
            if row.get("type") == "assistant" and block.get("type") == "tool_use" and block.get("id") in wanted:
                calls[block["id"]] = block
            if row.get("type") == "user" and block.get("type") == "tool_result" and block.get("tool_use_id") in wanted:
                results[block["tool_use_id"]] = block
    matched, failures = {}, []
    for kind in ("allow", "deny"):
        call_id = observations[kind + "_pre"]["tool_use_id"]
        call, result = calls.get(call_id), results.get(call_id)
        if call is None or result is None:
            gaps.append("No corroborating runtime tool call/result for " + kind + " canary")
            continue
        inp = call.get("input") or {}
        raw_path = inp.get("file_path") if isinstance(inp, dict) else None
        if call.get("name") != "Read" or not isinstance(raw_path, str) or Path(raw_path) != paths[kind]:
            failures.append("Transcript canary call differs from the dispatched absolute Read path")
            continue
        content = _json(result.get("content")).decode()
        if kind == "allow":
            ok = result.get("is_error") is not True and record["marker"] in content
        else:
            reason = "Runtime enforcement canary " + record["probe_id"]
            ok = result.get("is_error") is True and reason in content
        if not ok:
            failures.append("Runtime did not report the expected " + kind + " canary result")
        matched[kind] = {"tool_use_id": call_id, "call_sha256": _hash(_json(call)),
                         "result_sha256": _hash(_json(result)), "expected_result_observed": ok}
    return {"path": str(path), "snapshot_sha256": _hash(contents), "matched": matched}, gaps, failures


def status(gate):
    state = gate.read()
    ident = state.get("runtime_probe_id")
    if not ident:
        return {"status": "unverified", "gaps": ["No runtime probe was prepared"], "full_runtime_attestation": False}
    directory, paths = _paths(gate, ident)
    record = _read(directory / "probe.json")
    transcript, gaps, failures = _transcript_evidence(record, paths)
    if record.get("session_id") != gate.session:
        failures.append("Probe belongs to another runtime session")
    if record.get("turn_id") != state.get("turn_id"):
        gaps.append("Runtime turn changed; prepare and run a fresh canary")
    if "deny_post" in record["observations"]:
        failures.append("A PostToolUse event reported execution of the deliberately denied read")
    if datetime.fromisoformat(record["expires_at"]) <= datetime.now(timezone.utc):
        gaps.append("Probe expired; prepare and run a fresh canary")
    if record["fidelity_sha256"] != gate.current_fidelity():
        gaps.append("Instruction bundle changed after this probe was prepared")
    if not record.get("context_receipt"):
        gaps.append("No runtime instruction-context receipt existed when the probe began")
    else:
        try:
            gate.validate_context(state)
            if record["context_receipt"] != state.get("context_receipt"):
                gaps.append("Runtime instruction-context receipt changed since the probe began")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            gaps.append("Runtime instruction context is not current: " + str(exc))
    result = "failed" if failures else "unverified" if gaps else "tool_boundary_observed"
    if record["mode"] == "self-test" and result == "tool_boundary_observed":
        result = "self_test_passed"
    receipt = {"schema_version": 1, "probe_id": ident, "session_id": gate.session,
               "observed_at": _now(), "status": result, "mode": record["mode"], "runtime": record["runtime"],
               "fidelity_sha256": record["fidelity_sha256"], "transcript_evidence": transcript,
               "gaps": gaps, "failures": failures, "scope": record["scope"],
               "full_runtime_attestation": False,
               "not_attested": ["Stop denial and final delivery transport", "Independent reviewer dispatch and judgment",
                                "Connector credential scopes", "Hosted process cancellation", "Destination effects"],
               "trust_boundary": "Local host and runtime-reported transcript; not cryptographic runtime identity"}
    if record["mode"] == "self-test":
        receipt["scope"] = "Offline synthetic-event self-test only; no live runtime was attested"
    receipt["receipt_sha256"] = _hash(_json(receipt))
    _write(directory / "receipt.json", receipt)
    return receipt


def main(argv=None):
    from agent_gate import Gate, locked

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--session", required=True)
    prepare_parser.add_argument("--surface", required=True)
    prepare_parser.add_argument("--runtime-version", required=True)
    prepare_parser.add_argument("--mode", choices=("runtime", "self-test"), default="runtime")
    status_parser = commands.add_parser("status")
    status_parser.add_argument("--session", required=True)
    args = parser.parse_args(argv)
    try:
        gate = Gate(args.root, args.session)
        with locked(gate.directory / "lock"):
            result = (prepare(gate, args.surface, args.runtime_version, mode=args.mode)
                      if args.command == "prepare" else status(gate))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {"awaiting_runtime_events", "tool_boundary_observed", "self_test_passed"} else 1
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        print(json.dumps({"status": "unverified", "reason": str(exc), "full_runtime_attestation": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
