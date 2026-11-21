#!/usr/bin/env python3
"""Evidence for installed lessons and later recurrence checks; standard library only.

These receipts establish which local files an executed check covered. They do
not establish that a chosen test is adequate or that an external act succeeded.
The orchestrator and Hermes review those claims before delivery to Nick.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile

SCHEMA = "learning-verification/v1"
CONTEXT_CONTRACT = "2026-11-21.1"
# Historical observations retain their original contract and bound source hashes.
# Accepting their schema does not refresh a current delivery or its instruction bundle.
CONTEXT_CONTRACTS = {"2026-11-17.1", CONTEXT_CONTRACT}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def timestamp(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("evidence timestamp must carry a timezone")
    return stamp


def local_path(root, value, *, exists=True):
    """Refuse absolute paths, traversal, and symlinks that escape the repo."""
    rel = Path(value)
    if not value or rel.is_absolute() or ".." in rel.parts:
        raise ValueError("evidence paths must be repository-relative without '..'")
    base = Path(root).resolve()
    path = (base / rel).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ValueError("evidence path escapes the repository") from exc
    if exists and not path.is_file():
        raise ValueError("evidence file does not exist: %s" % value)
    return path


def surface_problems(root, surface, protected=(), prefixes=(), *, allow_protected=False):
    try:
        canonical = local_path(root, surface, exists=False).relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError) as exc:
        return [str(exc)]
    if not allow_protected and (canonical in protected or any(canonical.startswith(p) for p in prefixes)):
        return ["%s is protected; propose the correction through its authorized owner" % canonical]
    return []


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def ledger_lock(path):
    """Serialize CLI writers. Atomic replacement keeps concurrent reads whole."""
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+", encoding="utf-8") as handle:
        if os.name == "nt":
            import msvcrt
            if lock.stat().st_size == 0:
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def snapshot(root, paths):
    return {str(Path(p)): digest(local_path(root, p)) for p in sorted(set(paths))}


def names_test(command, test_file, absolute):
    """Accept a direct script or common test runner, never `-c pass test.py`."""
    if not command:
        return False
    paths = {test_file, absolute}
    nonexecuting = {"-h", "--help", "-V", "--version", "--collect-only", "--collectonly", "--co",
                    "--list", "--list-tests", "--dry-run", "--dryrun", "--fixtures", "--fixtures-per-test",
                    "--setup-plan", "--setup-only", "--markers"}
    if any(arg.split("=", 1)[0] in nonexecuting for arg in command[1:]):
        return False

    def runner_arguments(arguments, runner):
        # One explicit test operand. Selection/exclusion/config flags could
        # otherwise skip that file while an unrelated test makes the run green.
        allowed = ({"-v", "-q", "-f", "-b", "-c", "--verbose", "--quiet", "--failfast", "--buffer", "--catch"}
                   if runner == "unittest" else
                   {"-v", "-vv", "-q", "-qq", "-x", "--verbose", "--exitfirst", "--disable-warnings",
                    "--strict-markers", "--strict-config"})
        return sum(arg in paths for arg in arguments) == 1 and all(arg in paths or arg in allowed for arg in arguments)

    if command[0] in paths:
        return True
    executable = Path(command[0]).name.lower()
    if executable.startswith("python"):
        if len(command) > 1 and command[1] in paths:
            return True
        return len(command) > 3 and command[1:3] in (["-m", "pytest"], ["-m", "unittest"]) and runner_arguments(command[3:], command[2])
    if executable in ("bash", "sh", "node", "ruby", "perl"):
        return len(command) > 1 and command[1] in paths
    if executable in ("pytest", "pytest.exe"):
        return runner_arguments(command[1:], "pytest")
    return False


def runner_kind(command):
    executable = Path(command[0]).name.lower()
    if executable.startswith("python") and command[1:3] in (["-m", "unittest"], ["-m", "pytest"]):
        return command[2]
    return "pytest" if executable in ("pytest", "pytest.exe") else "script"


def output_tail(path):
    with Path(path).open("rb") as handle:
        handle.seek(max(0, Path(path).stat().st_size - 262144))
        return handle.read().decode("utf-8", errors="replace")


def executed_tests(kind, output):
    if kind == "unittest":
        matches = re.findall(r"\bRan (\d+) tests?\b", output)
        skipped = re.findall(r"\bskipped=(\d+)\b", output)
        return int(matches[-1]) - (int(skipped[-1]) if skipped else 0) if matches else 0
    if kind == "pytest":
        matches = re.findall(r"\b(\d+) passed\b", output)
        return int(matches[-1]) if matches else 0
    return None


def run_check(root, *, lesson_id, run_key, surface, anchor, criterion,
              test_file, inputs, command, receipt, timeout=120):
    """Execute an explicitly supplied local check and retain its actual result."""
    if not run_key.strip() or not criterion.strip():
        raise ValueError("a run key and bounded verification criterion are required")
    if len(anchor.strip()) < 20:
        raise ValueError("the installed-content anchor must be at least 20 characters")
    target = local_path(root, surface)
    if anchor not in target.read_text(encoding="utf-8"):
        raise ValueError("the installed-content anchor is absent from the target")
    local_path(root, test_file)
    if not command or not all(isinstance(arg, str) and arg for arg in command):
        raise ValueError("an explicit check command is required")
    # A receipt for `true` is not a regression. The declared test must be run.
    if not names_test(command, test_file, str(local_path(root, test_file))):
        raise ValueError("the command must execute the declared --test-file")
    if not 1 <= timeout <= 600:
        raise ValueError("check timeout must be between 1 and 600 seconds")
    output = local_path(root, receipt, exists=False)
    if output.exists():
        raise ValueError("receipt already exists; preserve it and choose a new path")
    paths = [surface, test_file] + list(inputs)
    before = snapshot(root, paths)
    started = now()
    failure = ""
    with tempfile.TemporaryDirectory(prefix="lesson-check-") as logs:
        stdout_path, stderr_path = Path(logs) / "stdout", Path(logs) / "stderr"
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen(command, cwd=root, stdout=stdout, stderr=stderr,
                                           stdin=subprocess.DEVNULL, shell=False, start_new_session=True)
                try:
                    exit_code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    if hasattr(os, "killpg"):
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait()
                    exit_code, failure = None, "verification timed out"
            except OSError as exc:
                exit_code, failure = None, "verification could not execute"
                stderr.write(str(exc).encode())
        stdout_sha256, stderr_sha256 = digest(stdout_path), digest(stderr_path)
        kind = runner_kind(command)
        test_count = executed_tests(kind, output_tail(stdout_path) + "\n" + output_tail(stderr_path))
        if kind != "script" and not test_count:
            failure = "test runner reported no executed passing tests"
    try:
        after = snapshot(root, paths)
    except (OSError, ValueError):
        after = {}
    stable = before == after
    if not stable:
        failure = "a tested file changed during verification"
    record = {
        "schema": SCHEMA, "lesson_id": lesson_id, "run_key": run_key,
        "surface": str(Path(surface)), "surface_sha256": before[str(Path(surface))],
        "anchor": anchor, "criterion": criterion, "test_file": str(Path(test_file)),
        "inputs": before, "command": list(command), "started_at": started,
        "finished_at": now(), "exit_code": exit_code, "inputs_unchanged": stable,
        "runner": kind, "executed_tests": test_count,
        "passed": exit_code == 0 and stable and not failure, "failure": failure,
        "stdout_sha256": stdout_sha256, "stderr_sha256": stderr_sha256,
        "not_proved": "Test adequacy, deployment, model context consumption, and external outcomes require separate evidence.",
    }
    atomic_text(output, json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def read_receipt(root, path, lesson_id, *, require_pass=True, current=True):
    receipt = local_path(root, path)
    record = json.loads(receipt.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("verification receipt must be an object")
    if record.get("schema") != SCHEMA or record.get("lesson_id") != lesson_id:
        raise ValueError("receipt schema or lesson id does not match")
    for key in ("run_key", "surface", "surface_sha256", "anchor", "criterion", "test_file"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError("receipt lacks %s" % key)
    inputs = record.get("inputs")
    if not isinstance(inputs, dict) or inputs.get(record["surface"]) != record["surface_sha256"]:
        raise ValueError("receipt lacks the tested surface hash")
    if record["test_file"] not in inputs:
        raise ValueError("receipt lacks the regression test hash")
    command = record.get("command")
    test_abs = str(local_path(root, record["test_file"], exists=current))
    if not isinstance(command, list) or not all(isinstance(a, str) and a for a in command):
        raise ValueError("receipt lacks an executed command")
    if not names_test(command, record["test_file"], test_abs):
        raise ValueError("receipt command did not name the regression test")
    began, ended = timestamp(record["started_at"]), timestamp(record["finished_at"])
    if ended < began or ended > datetime.now(timezone.utc):
        raise ValueError("receipt has invalid execution chronology")
    kind = runner_kind(command)
    if record.get("runner") != kind:
        raise ValueError("receipt does not identify the actual test runner")
    executed = kind == "script" or (type(record.get("executed_tests")) is int and record["executed_tests"] > 0)
    actual_pass = type(record.get("exit_code")) is int and record["exit_code"] == 0 and record.get("inputs_unchanged") is True and executed and not record.get("failure")
    if record.get("passed") is not actual_pass:
        raise ValueError("receipt success flag disagrees with the executed result")
    if require_pass and not actual_pass:
        raise ValueError("the executed regression check did not pass")
    if current:
        if snapshot(root, inputs) != inputs:
            raise ValueError("tested files have changed; run verification again")
        target = local_path(root, record["surface"])
        if record["anchor"] not in target.read_text(encoding="utf-8"):
            raise ValueError("installed-content anchor is no longer present")
    for key in ("surface_sha256", "stdout_sha256", "stderr_sha256"):
        value = record.get(key, "")
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("receipt lacks a valid %s" % key)
    return record


def promotion(root, row, receipt_path):
    record = read_receipt(root, receipt_path, row["id"])
    if row.get("state") in ("moved", "superseded"):
        raise ValueError("a moved or superseded lesson cannot be promoted")
    old = row.get("promotion")
    if old:
        if timestamp(record["started_at"]) <= timestamp(old["at"]):
            raise ValueError("reapplication requires a new executed check")
        if row.get("state") == "reopened" and record["surface_sha256"] == old["surface_sha256"]:
            raise ValueError("the failed mechanism is unchanged; repair or supersede it before reapplying")
    return {
        "version": 1, "at": now(), "surface": record["surface"],
        "surface_sha256": record["surface_sha256"], "anchor": record["anchor"],
        "receipt": str(Path(receipt_path)),
        "receipt_sha256": digest(local_path(root, receipt_path)),
        "run_key": record["run_key"], "criterion": record["criterion"],
    }


def read_context(root, path, installed, receipt):
    local = local_path(root, path)
    relative = local.relative_to(Path(root).resolve()).as_posix()
    if not relative.startswith(".claude/state/agent-gate/") or "/contexts/" not in relative:
        raise ValueError("context receipt must come from the agent-gate context store")
    context = json.loads(local.read_text(encoding="utf-8"))
    if not isinstance(context, dict):
        raise ValueError("context receipt must be an object")
    if context.get("schema_version") != 1 or context.get("contract_version") not in CONTEXT_CONTRACTS:
        raise ValueError("context receipt uses an unknown schema or delivery contract")
    if not context.get("session_id") or context.get("run_key") != receipt["run_key"] or context.get("turn_id") != receipt["run_key"]:
        raise ValueError("context receipt does not belong to this later run")
    surfaces = context.get("surfaces", {})
    if not isinstance(surfaces, dict) or surfaces.get(installed["surface"]) != installed["surface_sha256"]:
        raise ValueError("runtime context did not supply the promoted instruction revision")
    if not timestamp(installed["at"]) < timestamp(context["at"]) <= timestamp(receipt["started_at"]):
        raise ValueError("context must load after promotion and before the later check")
    fidelity = context.get("fidelity_sha256", "")
    if not isinstance(fidelity, str) or len(fidelity) != 64 or any(c not in "0123456789abcdef" for c in fidelity):
        raise ValueError("context receipt lacks the instruction fidelity hash")
    return context


def observe(root, row, *, run_key, result, evidence, loaded_sha, note, context_receipt):
    installed = row.get("promotion")
    if not installed or installed.get("version") != 1:
        raise ValueError("lesson needs a verified promotion before observing recurrence")
    if row.get("state") not in ("applied", "validated"):
        raise ValueError("repair and reapply a reopened lesson before observing the replacement")
    receipt = read_receipt(root, evidence, row["id"], require_pass=False)
    if run_key != receipt["run_key"] or run_key == installed["run_key"]:
        raise ValueError("observation must use a distinct later run key from its receipt")
    if any(event.get("run_key") == run_key for event in row.get("observations", [])):
        raise ValueError("this run already has an observation")
    if timestamp(receipt["started_at"]) <= timestamp(installed["at"]):
        raise ValueError("observation check must execute after the lesson was applied")
    if receipt["surface"] != installed["surface"]:
        raise ValueError("observation covers a different instruction surface")
    if receipt["criterion"] != installed["criterion"]:
        raise ValueError("observation checks a different acceptance criterion")
    if loaded_sha != receipt["surface_sha256"] or loaded_sha != installed["surface_sha256"]:
        raise ValueError("observation did not load the promoted instruction revision")
    context = read_context(root, context_receipt, installed, receipt)
    if result not in ("pass", "fail") or (result == "pass") != receipt["passed"]:
        raise ValueError("observed result disagrees with executed verification")
    if not note.strip():
        raise ValueError("observation must state the bounded outcome and its limits")
    return {
        "at": now(), "run_key": run_key, "result": result, "note": note,
        "loaded_sha256": loaded_sha, "evidence": str(Path(evidence)),
        "evidence_sha256": digest(local_path(root, evidence)),
        "promotion_sha256": installed["surface_sha256"],
        "context_receipt": context_receipt,
        "context_sha256": digest(local_path(root, context_receipt)),
        "fidelity_sha256": context["fidelity_sha256"],
    }


def assess(row, root):
    """Return evidence status without upgrading legacy labels to proof."""
    state = row.get("state", "open")
    status = {"id": row.get("id"), "state": state, "status": "candidate",
              "promotion_verified": False, "validated": False, "problems": [],
              "recurrences": sum(e.get("result") == "fail" for e in row.get("observations", []))}
    if state in ("moved", "superseded"):
        status["status"] = state
        return status
    installed = row.get("promotion")
    if not installed:
        if state in ("applied", "validated"):
            status["status"] = "legacy_unverified"
            status["problems"].append("legacy state has no installation and regression receipt")
        return status
    try:
        if installed.get("version") != 1:
            raise ValueError("unknown promotion evidence version")
        receipt = read_receipt(root, installed["receipt"], row["id"])
        if digest(local_path(root, installed["receipt"])) != installed["receipt_sha256"]:
            raise ValueError("promotion receipt changed after application")
        if receipt["surface_sha256"] != installed["surface_sha256"] or receipt["surface"] != installed["surface"]:
            raise ValueError("promotion does not match the verified surface")
        status["promotion_verified"] = True
        status["status"] = "installed"
        if state == "reopened":
            status["status"] = "reopened"
            status["problems"].append("failure recurred; repair the mechanism and reapply")
        elif state == "validated":
            events = [e for e in row.get("observations", [])
                      if e.get("promotion_sha256") == installed["surface_sha256"] and e.get("result") == "pass"]
            if not events:
                raise ValueError("validated state lacks a later passing observation")
            event = events[-1]
            evidence = read_receipt(root, event["evidence"], row["id"])
            if digest(local_path(root, event["evidence"])) != event["evidence_sha256"]:
                raise ValueError("observation evidence changed")
            if evidence["run_key"] == installed["run_key"] or timestamp(evidence["started_at"]) <= timestamp(installed["at"]):
                raise ValueError("observation is not a later run")
            if evidence["surface_sha256"] != installed["surface_sha256"]:
                raise ValueError("observation tested a different installed revision")
            if evidence["criterion"] != installed["criterion"]:
                raise ValueError("observation checked a different acceptance criterion")
            read_context(root, event["context_receipt"], installed, evidence)
            if digest(local_path(root, event["context_receipt"])) != event["context_sha256"]:
                raise ValueError("runtime context receipt changed after the observation")
            status["validated"], status["status"] = True, "validated"
    except (ValueError, KeyError, TypeError, OSError) as exc:
        status["status"] = "stale_evidence"
        status["problems"].append(str(exc))
    return status
