#!/usr/bin/env python3
"""Atomic task allowances for adapters with explicit cost and lifecycle evidence.

This local controller never launches a command, grants authority, or calls a provider.
One durable checkout owns a task. Child reservations share the same root allowance.
An expired lease fences further work; it does not prove a provider stopped billing.
Unknown/expired reservations retain their allowance until the owner reconciles them.
Native hosted inference without pre-call telemetry is not covered by monetary limits.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets


class ControlError(ValueError):
    pass


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ControlError(name + " must be an integer >= " + str(minimum))
    return value


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ControlError(name + " must be explicit")
    return value


def instant(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ControlError("Timestamp needs a timezone")
    return parsed.astimezone(timezone.utc)


def clock():
    return datetime.now(timezone.utc)


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


class TaskControl:
    def __init__(self, root, task_key, entity, *, now=clock):
        self.root = Path(root).resolve()
        self.task_key = text(task_key, "task_key")
        self.entity = text(entity, "entity")
        self.now = now
        config = json.loads((self.root / "config/maturation.json").read_text())
        if config.get("entity") != entity:
            raise ControlError("Execution task belongs to another entity")
        self.policy = config["execution"]
        key = hashlib.sha256((entity + "\n" + task_key).encode()).hexdigest()[:32]
        self.path = self.root / ".claude/state/execution-control" / (key + ".json")
        if not self.path.resolve().is_relative_to(self.root):
            raise ControlError("Execution state leaves its entity repository")
        self.lock = self.path.with_suffix(".lock")

    def _read(self):
        # Only proven absence permits initialization. Corrupt or unreadable state fails.
        try:
            value = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        if (not isinstance(value, dict) or value.get("schema_version") != 1
                or value.get("task_key") != self.task_key or value.get("entity") != self.entity):
            raise ControlError("Invalid execution state or entity boundary")
        if value.get("state") not in {"active", "parked"} or not isinstance(value.get("reservations"), dict):
            raise ControlError("Invalid execution state")
        limits = value["limits"]
        for key in ("total_microusd", "verification_reserve_microusd", "max_attempts", "max_concurrent"):
            integer(limits[key], key, 1 if key.startswith("max_") else 0)
        if limits["verification_reserve_microusd"] > limits["total_microusd"]:
            raise ControlError("Invalid persisted verification reserve")
        instant(limits["deadline_at"])
        for row in value["reservations"].values():
            if not isinstance(row, dict) or row.get("status") not in {"active", "revoked", "settled", "cost_unknown"}:
                raise ControlError("Invalid reservation")
            integer(row["reserved_microusd"], "reserved_microusd")
            if row.get("actual_microusd") is not None:
                integer(row["actual_microusd"], "actual_microusd")
            if row.get("purpose") not in {"work", "verification"} or type(row.get("stopped")) is not bool:
                raise ControlError("Invalid reservation purpose or stop evidence")
            if row["stopped"] != (row["status"] in {"settled", "cost_unknown"}):
                raise ControlError("Reservation stop state is inconsistent")
            if (row["status"] == "settled") != (row.get("actual_microusd") is not None):
                raise ControlError("Reservation measurement state is inconsistent")
            instant(row["expires_at"])
        return value

    def _save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + "." + secrets.token_hex(6))
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def _state(self):
        state = self._read()
        if state is None:
            raise ControlError("Task allowance is not configured")
        return state

    def configure(self, limits):
        if not isinstance(limits, dict):
            raise ControlError("Task limits must be an object")
        limits = {"verification_attempts": 1, **limits}
        expected = {"total_microusd", "verification_reserve_microusd", "max_attempts",
                    "max_concurrent", "deadline_at", "authority_ref", "verification_attempts"}
        if set(limits) != expected:
            raise ControlError("Task limits require exactly: " + ", ".join(sorted(expected)))
        total = integer(limits["total_microusd"], "total_microusd")
        reserve = integer(limits["verification_reserve_microusd"], "verification_reserve_microusd")
        if reserve > total:
            raise ControlError("Verification reserve exceeds the total allowance")
        integer(limits["max_attempts"], "max_attempts", 1)
        integer(limits["max_concurrent"], "max_concurrent", 1)
        verification_attempts = integer(limits["verification_attempts"], "verification_attempts", 1)
        if verification_attempts > limits["max_attempts"] or verification_attempts > integer(self.policy["review_dispatches"], "policy.review_dispatches", 1):
            raise ControlError("Invalid verification attempt reserve")
        if limits["max_attempts"] - verification_attempts > integer(self.policy["max_dispatches"], "policy.max_dispatches", 1):
            raise ControlError("Work attempts exceed repository policy")
        if limits["max_attempts"] > integer(self.policy["max_dispatches"], "policy.max_dispatches", 1) + integer(self.policy["review_dispatches"], "policy.review_dispatches", 1):
            raise ControlError("Task attempts exceed repository policy")
        if limits["max_concurrent"] > integer(self.policy["max_concurrent"], "policy.max_concurrent", 1):
            raise ControlError("Task concurrency exceeds repository policy")
        text(limits["authority_ref"], "authority_ref")
        if instant(limits["deadline_at"]) <= self.now():
            raise ControlError("Task deadline has passed")
        if (instant(limits["deadline_at"]) - self.now()).total_seconds() > integer(self.policy["deadline_seconds"], "policy.deadline_seconds", 1):
            raise ControlError("Task deadline exceeds repository policy")
        with locked(self.lock):
            state = self._read()
            if state:
                if state["limits"] != limits:
                    raise ControlError("Existing task limits are immutable; do not reset a consumed allowance")
                return state
            state = {"schema_version": 1, "entity": self.entity, "task_key": self.task_key,
                     "created_at": self.now().isoformat(), "limits": limits,
                     "state": "active", "reservations": {}, "events": []}
            self._save(state)
            return state

    @staticmethod
    def _liability(row):
        return row["actual_microusd"] if row.get("actual_microusd") is not None else row["reserved_microusd"]

    def _valid(self, state, token):
        row = state["reservations"].get(token)
        if not row or row["status"] != "active" or state["state"] != "active":
            raise ControlError("Lease is absent, revoked, or parked")
        if self.now() >= instant(row["expires_at"]) or self.now() >= instant(state["limits"]["deadline_at"]):
            raise ControlError("Lease expired; reconcile before retrying")
        if row.get("parent"):
            self._valid(state, row["parent"])
        return row

    def reserve(self, worker, estimated_microusd, expires_at, *, purpose="work", parent=None):
        text(worker, "worker")
        estimate = integer(estimated_microusd, "estimated_microusd")
        if purpose not in {"work", "verification"}:
            raise ControlError("Reservation purpose must be work or verification")
        expiry = instant(expires_at)
        with locked(self.lock):
            state = self._state()
            limits = state["limits"]
            if state["state"] != "active" or self.now() >= instant(limits["deadline_at"]):
                raise ControlError("Task is parked or its deadline passed")
            if expiry <= self.now() or expiry > instant(limits["deadline_at"]):
                raise ControlError("Lease must expire within the active task deadline")
            if parent:
                parent_row = self._valid(state, parent)
                if expiry > instant(parent_row["expires_at"]):
                    raise ControlError("A child cannot outlive its parent lease")
            rows = list(state["reservations"].values())
            if len(rows) >= limits["max_attempts"]:
                raise ControlError("Attempt allowance exhausted; checkpoint and park")
            reserved_attempts = limits.get("verification_attempts", 1)
            purpose_limit = reserved_attempts if purpose == "verification" else limits["max_attempts"] - reserved_attempts
            if sum(r["purpose"] == purpose for r in rows) >= purpose_limit:
                raise ControlError("Purpose-specific attempt allowance exhausted; preserve verification")
            # A timeout does not release a slot: the worker may still be running.
            if sum(not r["stopped"] for r in rows) >= limits["max_concurrent"]:
                raise ControlError("Concurrent worker allowance exhausted")
            used = sum(self._liability(r) for r in rows)
            if used + estimate > limits["total_microusd"]:
                raise ControlError("Total task allowance exhausted")
            work_used = sum(self._liability(r) for r in rows if r["purpose"] == "work")
            if purpose == "work" and work_used + estimate > limits["total_microusd"] - limits["verification_reserve_microusd"]:
                raise ControlError("Work would consume the reserved verification allowance")
            token = secrets.token_hex(24)
            state["reservations"][token] = {"worker": worker, "purpose": purpose, "parent": parent,
                "reserved_microusd": estimate, "actual_microusd": None, "status": "active",
                "stopped": False, "created_at": self.now().isoformat(), "expires_at": expiry.isoformat()}
            self._save(state)
            return {"lease": token, "task_key": self.task_key, "entity": self.entity,
                    "expires_at": expiry.isoformat(), "reserved_microusd": estimate}

    def assert_active(self, token):
        with locked(self.lock):
            return dict(self._valid(self._state(), token))

    @contextmanager
    def effect_guard(self, token):
        """Serialize a short local commit with cancellation; remote effects need fencing.

        A commit admitted before park may finish before park acquires the lock.
        This is not a clock-atomic provider transaction or remote cancellation.
        """
        with locked(self.lock):
            row = self._valid(self._state(), token)
            yield dict(row)

    def finish(self, token, actual_microusd, evidence_ref):
        """Called after adapter-observed stop and cost measurement; None retains liability."""
        if actual_microusd is not None:
            integer(actual_microusd, "actual_microusd")
        text(evidence_ref, "Cost/stop evidence reference")
        with locked(self.lock):
            state = self._state()
            row = self._valid(state, token)
            if any(r.get("parent") == token and not r["stopped"] for r in state["reservations"].values()):
                raise ControlError("Parent cannot finish while a child may still be running")
            self._close(state, row, actual_microusd, evidence_ref)
            self._save(state)
            return dict(row)

    def _close(self, state, row, actual, evidence):
        row.update(actual_microusd=actual, stopped=True, evidence_ref=evidence,
                   status="settled" if actual is not None else "cost_unknown", closed_at=self.now().isoformat())
        if actual is not None and actual > row["reserved_microusd"]:
            # Record the real overrun before stopping further admissions.
            state.update(state="parked", reason="Provider cost exceeded its reservation")

    def park(self, reason):
        text(reason, "Park reason")
        with locked(self.lock):
            state = self._state()
            state.update(state="parked", reason=reason)
            for row in state["reservations"].values():
                if not row["stopped"]:
                    row["status"] = "revoked"
            state["events"].append({"at": self.now().isoformat(), "event": "parked", "reason": reason})
            self._save(state)
            return state

    def reconcile(self, token, actual_microusd, evidence_ref):
        """Owner-only workflow after observed stop; a timed-out worker cannot finish itself."""
        integer(actual_microusd, "actual_microusd")
        text(evidence_ref, "Reconciliation evidence reference")
        with locked(self.lock):
            state = self._state()
            row = state["reservations"].get(token)
            if not row:
                raise ControlError("Unknown reservation")
            if row["status"] == "settled":
                raise ControlError("Measured cost is already settled")
            if any(r.get("parent") == token and not r["stopped"] for r in state["reservations"].values()):
                raise ControlError("Reconcile running children before their parent")
            self._close(state, row, actual_microusd, evidence_ref)
            self._save(state)
            return dict(row)

    def resume(self, changed_condition, evidence_ref):
        text(changed_condition, "Changed condition")
        text(evidence_ref, "Resume evidence reference")
        with locked(self.lock):
            state = self._state()
            if state["state"] != "parked":
                raise ControlError("Only parked work can resume")
            if any(not r["stopped"] or r.get("actual_microusd") is None for r in state["reservations"].values()):
                raise ControlError("Reconcile every unresolved worker and cost before resuming")
            if self.now() >= instant(state["limits"]["deadline_at"]):
                raise ControlError("The original task deadline still applies")
            state.update(state="active", reason=None)
            state["events"].append({"at": self.now().isoformat(), "event": "resumed",
                                    "changed_condition": changed_condition, "evidence_ref": evidence_ref})
            self._save(state)
            return state

    def status(self):
        with locked(self.lock):
            state = self._state()
            rows = list(state["reservations"].values())
            liability = sum(self._liability(r) for r in rows)
            deadline_expired = self.now() >= instant(state["limits"]["deadline_at"])
            return {"entity": self.entity, "task_key": self.task_key, "state": state["state"],
                    "admission_state": "expired" if deadline_expired else state["state"],
                    "deadline_at": state["limits"]["deadline_at"], "deadline_expired": deadline_expired,
                    "expired_unreconciled": sum(not r["stopped"] and self.now() >= instant(r["expires_at"]) for r in rows),
                    "attempts": len(rows), "possibly_running": sum(not r["stopped"] for r in rows),
                    "actual_microusd": sum(r["actual_microusd"] for r in rows) if all(r.get("actual_microusd") is not None for r in rows) else None,
                    "reserved_or_measured_microusd": liability,
                    "remaining_microusd": max(0, state["limits"]["total_microusd"] - liability),
                    "scope": "Only calls admitted by this local controller; no native hosted inference coverage"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--task-key", required=True)
    parser.add_argument("--entity", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure")
    configure.add_argument("--limits", type=Path, required=True)
    reserve = commands.add_parser("reserve")
    reserve.add_argument("--worker", required=True)
    reserve.add_argument("--estimated-microusd", type=int, required=True)
    reserve.add_argument("--expires-at", required=True)
    reserve.add_argument("--purpose", choices=["work", "verification"], default="work")
    reserve.add_argument("--parent")
    active = commands.add_parser("assert-active")
    active.add_argument("--lease", required=True)
    for name in ("finish", "reconcile"):
        sub = commands.add_parser(name)
        sub.add_argument("--lease", required=True)
        sub.add_argument("--actual-microusd", type=int, required=name == "reconcile")
        sub.add_argument("--evidence-ref", required=True)
    park = commands.add_parser("park")
    park.add_argument("--reason", required=True)
    resume = commands.add_parser("resume")
    resume.add_argument("--changed-condition", required=True)
    resume.add_argument("--evidence-ref", required=True)
    commands.add_parser("status")
    args = parser.parse_args(argv)
    try:
        control = TaskControl(args.root, args.task_key, args.entity)
        if args.command == "configure":
            result = control.configure(json.loads(args.limits.read_text()))
        elif args.command == "reserve":
            result = control.reserve(args.worker, args.estimated_microusd, args.expires_at, purpose=args.purpose, parent=args.parent)
        elif args.command == "assert-active":
            result = control.assert_active(args.lease)
        elif args.command in {"finish", "reconcile"}:
            result = getattr(control, args.command)(args.lease, args.actual_microusd, args.evidence_ref)
        elif args.command == "park":
            result = control.park(args.reason)
        elif args.command == "resume":
            result = control.resume(args.changed_condition, args.evidence_ref)
        else:
            result = control.status()
        print(json.dumps(result, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        import sys
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
