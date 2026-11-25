#!/usr/bin/env python3
"""Record observed routine starts and gated terminal outputs in their owning repo.

These editable local records are observations, not write permission, a global
lease, or independent runtime attestation. Retain the native effect journal.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RELATIVE_LOG = Path("capture/routine_runs.jsonl")
UTC = dt.timezone.utc
MAX_ARTIFACT = 8 * 1024 * 1024
MAX_LOG = 64 * 1024 * 1024
HASH = re.compile(r"[a-f0-9]{64}")
RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")


def timestamp(value):
    if not isinstance(value, str) or not RFC3339.fullmatch(value):
        raise ValueError("a complete RFC3339 timestamp with a timezone is required")
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso(at):
    if not isinstance(at, dt.datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("a timezone-aware datetime is required")
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def routine_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", value):
        raise ValueError("invalid routine_id")
    return value


def session_name(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 200
            or any(ord(c) < 32 for c in value)):
        raise ValueError("actual runtime session/run id is required")
    return value


def identity(routine_id, scheduled_at):
    return routine_name(routine_id) + ":" + iso(timestamp(scheduled_at))


def strict_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def strict_json(raw):
    def reject(value):
        raise ValueError("non-finite JSON number: " + value)
    return json.loads(raw, object_pairs_hook=strict_object, parse_constant=reject)


def relative_name(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(c) < 32 for c in value)):
        raise ValueError("evidence requires a repository-relative file path")
    path = Path(value)
    if path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ValueError("routine evidence must stay inside its owning repository")
    return path


def safe_path(root, relative):
    """Reject traversal and existing symlinks, for reads and writes alike."""
    root = Path(root).resolve()
    path = root
    for part in relative_name(relative.as_posix() if isinstance(relative, Path) else relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError("routine evidence paths cannot contain symlinks")
    if not path.resolve().is_relative_to(root):
        raise ValueError("routine evidence leaves its owning repository")
    return path


def artifact(root, relative):
    path = safe_path(root, relative)
    if not path.is_file() or path.stat().st_size > MAX_ARTIFACT:
        raise ValueError("routine evidence is unavailable or exceeds 8 MiB")
    return {"path": relative_name(relative).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def validate_verification(proof, run_id, outputs):
    if (not isinstance(proof, dict)
            or set(proof) != {"method", "session_id", "release_id", "artifact_hashes"}
            or proof.get("method") != "agent_gate.check"
            or proof.get("session_id") != run_id
            or not isinstance(proof.get("release_id"), str)
            or not HASH.fullmatch(proof["release_id"])
            or proof.get("artifact_hashes") != [row["sha256"] for row in outputs]):
        raise ValueError("output verification binding mismatch")


def validate_event(row):
    common = {"schema_version", "event", "routine_id", "at"}
    if (not isinstance(row, dict) or type(row.get("schema_version")) is not int
            or row["schema_version"] != 1):
        raise ValueError("unsupported routine receipt")
    routine_name(row.get("routine_id"))
    at = timestamp(row.get("at"))
    if row.get("event") == "monitoring_started":
        if set(row) != common | {"reason"} or not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError("monitoring coverage requires its observed reason")
        return
    common |= {"scheduled_at", "occurrence_id", "run_id"}
    if row.get("event") not in {"started", "terminal"}:
        raise ValueError("unknown routine event")
    due = timestamp(row.get("scheduled_at"))
    if due > at or row.get("occurrence_id") != identity(row["routine_id"], row["scheduled_at"]):
        raise ValueError("receipt occurrence identity or observation time mismatch")
    session_name(row.get("run_id"))
    if row["event"] == "started":
        if set(row) != common:
            raise ValueError("invalid start receipt fields")
        return
    if set(row) != common | {"state", "outputs", "verification", "reason"}:
        raise ValueError("invalid terminal receipt fields")
    if row["state"] not in {"verified", "unverified", "failed", "skipped"}:
        raise ValueError("invalid terminal state")
    if row["reason"] is not None and (not isinstance(row["reason"], str) or not row["reason"].strip()):
        raise ValueError("terminal reason must be meaningful text")
    if row["state"] != "verified" and not row["reason"]:
        raise ValueError("unverified, failed or skipped work requires a reason")
    outputs = row["outputs"]
    if not isinstance(outputs, list) or len(outputs) > 32 or (row["state"] == "verified" and not outputs):
        raise ValueError("verified work requires bounded output artifacts")
    seen = set()
    for output in outputs:
        if not isinstance(output, dict) or set(output) != {"path", "source_path", "sha256"}:
            raise ValueError("invalid archived output")
        source = relative_name(output["source_path"]).as_posix()
        if source in seen:
            raise ValueError("duplicate routine output")
        seen.add(source)
        if not isinstance(output["sha256"], str) or not HASH.fullmatch(output["sha256"]):
            raise ValueError("invalid output hash")
        if output["path"] != "capture/routine_receipts/" + output["sha256"] + ".artifact":
            raise ValueError("output is not a content-addressed routine snapshot")
    if row["state"] == "verified":
        validate_verification(row["verification"], row["run_id"], outputs)
    elif row["verification"] is not None:
        raise ValueError("nonverified work cannot claim gate verification")


def read_events(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("routine log cannot be a symlink")
    if not path.exists():
        return []
    if not path.is_file() or path.stat().st_size > MAX_LOG:
        raise ValueError("routine log is unavailable or exceeds 64 MiB")
    rows, starts, terminals = [], {}, set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = strict_json(line)
        validate_event(row)
        if row["event"] != "monitoring_started":
            key = (row["occurrence_id"], row["run_id"])
            if row["event"] == "started":
                if key in starts:
                    raise ValueError("duplicate start receipts")
                starts[key] = timestamp(row["at"])
            else:
                if key not in starts or timestamp(row["at"]) < starts[key]:
                    raise ValueError("terminal receipt has no preceding observed start")
                if key in terminals:
                    raise ValueError("conflicting terminal receipts")
                terminals.add(key)
        rows.append(row)
    return rows


def verify_delivery(root, run_id, outputs):
    """One actual gate invocation binds every output to the same cleared release."""
    gate = safe_path(root, "scripts/agent_gate.py")
    if not gate.is_file():
        raise ValueError("delivery gate unavailable; record an unverified result")
    command = [sys.executable, str(gate), "check", "--session", run_id, "--require-complete"]
    for output in outputs:
        command.extend(["--artifact", output["path"]])
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("delivery gate did not verify routine output")
    answer = strict_json(result.stdout)
    if not isinstance(answer, dict) or set(answer) != {"cleared"}:
        raise ValueError("delivery gate returned no cleared release")
    proof = {"method": "agent_gate.check", "session_id": run_id,
             "release_id": answer["cleared"], "artifact_hashes": [row["sha256"] for row in outputs]}
    validate_verification(proof, run_id, outputs)
    return proof


def append_rows(path, rows):
    # The lock and append are local to this checkout; separate clones need one
    # authoritative writer and provider reconciliation, not another local claim.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        for row in rows:
            validate_event(row)
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def local_lock(path):
    # Same checkout-local POSIX/Windows lock convention as execution_control.
    if path.is_symlink():
        raise ValueError("routine lock cannot be a symlink")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "r+b") as stream:
        if os.name == "nt":
            import msvcrt
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def record(root, routine_id, scheduled_at=None, run_id=None, event="started", *, state=None,
           outputs=(), reason=None, now=None, verifier=verify_delivery):
    root = Path(root).resolve()
    routine_name(routine_id)
    if event not in {"monitoring_started", "started", "terminal"}:
        raise ValueError("invalid event")
    if not isinstance(outputs, (list, tuple)) or len(outputs) > 32:
        raise ValueError("outputs must be a bounded list of file paths")
    if event != "terminal" and (state is not None or outputs):
        raise ValueError("only a terminal event can carry results")
    path = safe_path(root, RELATIVE_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = safe_path(root, RELATIVE_LOG.with_suffix(".lock"))
    with local_lock(lock_path):
        observed_at = timestamp(iso(now or dt.datetime.now(UTC)))
        rows = read_events(path)
        if any(timestamp(r["at"]) > observed_at for r in rows):
            raise ValueError("routine log contains observations from after this clock")
        if event == "monitoring_started":
            if scheduled_at is not None or run_id is not None:
                raise ValueError("monitoring enrollment has no invented occurrence or run")
            row = {"schema_version": 1, "event": event, "routine_id": routine_id,
                   "at": iso(observed_at), "reason": reason}
            validate_event(row)
            append_rows(path, [row])
            return row
        due = timestamp(scheduled_at)
        key = identity(routine_id, scheduled_at)
        session_name(run_id)
        if due > observed_at:
            raise ValueError("a future occurrence cannot already have started")
        current = [r for r in rows if r.get("occurrence_id") == key]
        mine = [r for r in current if r.get("run_id") == run_id]
        row = {"schema_version": 1, "event": event, "routine_id": routine_id,
               "scheduled_at": iso(due), "occurrence_id": key, "run_id": run_id,
               "at": iso(observed_at)}
        if event == "started":
            if any(r["event"] == "terminal" for r in mine):
                raise ValueError("this attempt is already terminal; reconcile before a new session retry")
            if any(r["event"] == "started" for r in mine):
                return next(r for r in mine if r["event"] == "started")
            if any(r["event"] == "terminal" and r["state"] == "verified" for r in current):
                raise ValueError("occurrence already verified; reconcile before another run")
            starts = {r["run_id"] for r in current if r["event"] == "started"}
            finished = {r["run_id"] for r in current if r["event"] == "terminal"}
            if starts - finished:
                raise ValueError("another local run remains active; reconcile before retry")
        else:
            if not any(r["event"] == "started" for r in mine):
                raise ValueError("a terminal receipt requires its observed start")
            if any(r["event"] == "terminal" for r in mine):
                raise ValueError("terminal receipt is immutable; reconcile or record a new attempt")
            if state not in {"verified", "unverified", "failed", "skipped"}:
                raise ValueError("invalid terminal state")
            if state != "verified" and (not isinstance(reason, str) or not reason.strip()):
                raise ValueError("unverified, failed or skipped work requires a reason")
            evidence = [artifact(root, p) for p in outputs]
            if len({o["path"] for o in evidence}) != len(evidence):
                raise ValueError("duplicate routine output")
            if state == "verified" and not evidence:
                raise ValueError("verified work requires output artifacts")
            verification = verifier(root, run_id, evidence) if state == "verified" else None
            if state == "verified":
                validate_verification(verification, run_id, evidence)
            if evidence != [artifact(root, p) for p in outputs]:
                raise ValueError("routine output changed during verification")
            archived = []
            for item in evidence:
                content = safe_path(root, item["path"]).read_bytes()
                if len(content) > MAX_ARTIFACT or hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise ValueError("routine output changed before its snapshot")
                relative = "capture/routine_receipts/" + item["sha256"] + ".artifact"
                destination = safe_path(root, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with destination.open("xb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                except FileExistsError:
                    if safe_path(root, relative).read_bytes() != content:
                        raise ValueError("immutable routine output snapshot changed")
                archived.append({"path": relative, "source_path": item["path"], "sha256": item["sha256"]})
            row.update(state=state, outputs=archived, verification=verification, reason=reason)
            if now is None:
                row["at"] = iso(dt.datetime.now(UTC))
        validate_event(row)
        new_rows = []
        if not any(r["event"] == "monitoring_started" and r["routine_id"] == routine_id for r in rows):
            new_rows.append({"schema_version": 1, "event": "monitoring_started", "routine_id": routine_id,
                             "at": iso(observed_at),
                             "reason": "Coverage begins at this observed start; no earlier coverage is asserted."})
        append_rows(path, new_rows + [row])
        return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("event", choices=["monitoring-started", "started", "terminal"])
    ap.add_argument("--routine", required=True)
    ap.add_argument("--scheduled-at")
    ap.add_argument("--run-id")
    ap.add_argument("--state", choices=["verified", "unverified", "failed", "skipped"])
    ap.add_argument("--output", action="append", default=[])
    ap.add_argument("--reason")
    args = ap.parse_args(argv)
    try:
        result = record(ROOT, args.routine, args.scheduled_at, args.run_id, args.event.replace("-", "_"),
                        state=args.state, outputs=args.output, reason=args.reason)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print("Routine receipt unavailable: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
