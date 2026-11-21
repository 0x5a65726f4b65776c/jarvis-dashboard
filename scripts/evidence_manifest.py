#!/usr/bin/env python3
"""Validate dated evidence against a frozen collection plan and resume valid stages.

This module reads existing records. It neither collects sources nor changes a
canonical fact. A manifest establishes integrity of its declared collection
scope; the independent reviewer still checks that scope against the request.
"""
from __future__ import annotations

import argparse
import html
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

SCHEMA_VERSION = 1
COLLECTION_STATES = {"ok", "empty", "failed", "unknown"}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class EvidenceUnavailable(OSError):
    """A read failed; retry the read rather than repurchasing completed work."""


class IncompleteEvidence(ValueError):
    """Required declared evidence is unavailable, changed, or expired."""


def timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A timestamp with a timezone is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _clock(at=None):
    value = timestamp(at) if isinstance(at, str) else at or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Clock has no timezone")
    return value.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " is required")
    return value


def local(root, value):
    _text(value, "Repository-relative path")
    root = Path(root).resolve()
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Evidence paths must be repository-relative")
    result = (root / path).resolve()
    if not result.is_relative_to(root) or result == root:
        raise ValueError("Evidence path leaves this repository")
    return result


def _bytes(path):
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise EvidenceUnavailable("Evidence storage could not be read; retry the read") from exc


def file_hash(root, path):
    return hashlib.sha256(_bytes(local(root, path))).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON field: " + key)
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite JSON number: " + value)


def _decode(data):
    return json.loads(data, object_pairs_hook=_pairs, parse_constant=_invalid_constant)


def read_json(path):
    return _decode(_bytes(path))


def _canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _identity(value, entity, label):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError(label + " has an unsupported schema")
    _text(entity, "Expected entity")
    if value.get("entity") != entity:
        raise ValueError(label + " belongs to another entity")
    _text(value.get("task_key"), label + " task_key")


def _reference(root, reference, *, require_hash=True):
    if isinstance(reference, str):
        reference = {"path": reference}
    if not isinstance(reference, dict):
        raise ValueError("File reference must be a path or object")
    path = _text(reference.get("path"), "Reference path")
    supplied = reference.get("sha256")
    if require_hash or supplied is not None:
        if not isinstance(supplied, str) or not SHA256.fullmatch(supplied):
            raise ValueError("Reference requires a SHA256 digest")
    try:
        data = _bytes(local(root, path))
    except FileNotFoundError as exc:
        raise ValueError("Referenced evidence file is missing: " + path) from exc
    actual = hashlib.sha256(data).hexdigest()
    if supplied is not None and actual != supplied:
        raise ValueError("Referenced evidence changed: " + path)
    return _decode(data), {"path": path, "sha256": actual}


def _plan(root, reference, entity, task_key):
    plan, bound = _reference(root, reference)
    _identity(plan, entity, "Collection plan")
    if plan["task_key"] != task_key:
        raise ValueError("Collection plan belongs to another task")
    rows = plan.get("sources")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Collection plan requires its expected sources")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Planned source must be an object")
        source_id = _text(row.get("id"), "Source ID")
        if source_id in result:
            raise ValueError("Duplicate planned source ID")
        local(root, row.get("path"))
        if type(row.get("required")) is not bool:
            raise ValueError("Each planned source must declare required true or false")
        age = row.get("max_age_seconds")
        if type(age) is not int or age <= 0:
            raise ValueError("Each planned source needs a positive freshness window")
        if row.get("source_ref") is not None:
            _text(row["source_ref"], "Source reference")
        result[source_id] = row
    if not any(row["required"] for row in result.values()):
        raise ValueError("Collection plan requires at least one mandatory source")
    return result, bound


def check_manifest(root, manifest, expected_entity, require_complete=True, at=None):
    """Recheck declared scope, source versions, collection outcomes, and freshness."""
    _identity(manifest, expected_entity, "Evidence manifest")
    now = _clock(at)
    created = timestamp(manifest.get("created_at"))
    if created > now:
        raise ValueError("Evidence manifest is dated in the future")
    planned, plan_ref = _plan(root, manifest.get("collection_plan"), expected_entity, manifest["task_key"])
    rows = manifest.get("sources")
    if not isinstance(rows, list):
        raise ValueError("Manifest sources must be a list")
    observations = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("id") not in planned:
            raise ValueError("Unplanned source cannot enter this evidence set")
        if row["id"] in observations:
            raise ValueError("Duplicate observed source ID")
        observations[row["id"]] = row
    normalized, issues, dependencies = [], [], [plan_ref]
    for source_id, spec in planned.items():
        row = observations.get(source_id)
        result = {"id": source_id, "entity": expected_entity, "required": spec["required"],
                  "path": spec["path"], "state": "unknown", "reported_state": "unknown"}
        if spec.get("source_ref"):
            result["source_ref"] = spec["source_ref"]
        code, reason = "not_collected", "Planned source was not collected"
        if row is not None:
            if row.get("entity") != expected_entity:
                raise ValueError("Observed source belongs to another entity")
            if row.get("path") != spec["path"]:
                raise ValueError("Observed source differs from its planned canonical path")
            if "required" in row and row["required"] is not spec["required"]:
                raise ValueError("Observation cannot change a source's required status")
            if row.get("source_ref", spec.get("source_ref")) != spec.get("source_ref"):
                raise ValueError("Observation cannot replace its planned source reference")
            state = row.get("state")
            if state not in COLLECTION_STATES:
                raise ValueError("Unknown collection outcome; staleness is calculated at use time")
            attempted = timestamp(row.get("attempted_at"))
            if attempted > created:
                raise ValueError("Collection attempt follows its manifest")
            result.update(state=state, reported_state=state, attempted_at=row["attempted_at"])
            if row.get("last_success_at") is not None:
                if timestamp(row["last_success_at"]) > attempted:
                    raise ValueError("Last success follows the latest collection attempt")
                result["last_success_at"] = row["last_success_at"]
            if state in {"failed", "unknown"}:
                reason = _text(row.get("reason"), "Collection failure or unknown reason")
                code = "collection_" + state
            else:
                observed = timestamp(row.get("observed_at"))
                expires = timestamp(row.get("expires_at"))
                if observed > attempted or expires <= observed:
                    raise ValueError("Observation and expiry do not bound a valid collection")
                if (expires - observed).total_seconds() > spec["max_age_seconds"]:
                    raise ValueError("Observation exceeds the plan's freshness window")
                count = row.get("result_count")
                if type(count) is not int or count < 0 or (state == "empty") != (count == 0):
                    raise ValueError("Successful empty collection requires result_count zero")
                supplied = row.get("sha256")
                if not isinstance(supplied, str) or not SHA256.fullmatch(supplied):
                    raise ValueError("Successful collection requires its source digest")
                result.update(observed_at=row["observed_at"], expires_at=row["expires_at"],
                              last_success_at=row.get("last_success_at", row["attempted_at"]),
                              sha256=supplied, result_count=count)
                for field in ("source_as_of", "version"):
                    if row.get(field) is not None:
                        result[field] = _text(row[field], field)
                try:
                    actual = file_hash(root, spec["path"])
                except FileNotFoundError:
                    result["state"], code, reason = "unknown", "source_missing", "Collected source file is missing"
                else:
                    dependencies.append({"path": spec["path"], "sha256": actual})
                    if actual != supplied:
                        result["state"], code, reason = "stale", "source_changed", "Source changed; dependent work requires revalidation"
                    elif now >= expires:
                        result["state"], code, reason = "stale", "source_expired", "Source freshness window expired"
                    else:
                        code, reason = None, None
        if reason:
            result["reason"] = reason
            issues.append({"source_id": source_id, "code": code, "message": reason, "required": spec["required"]})
        normalized.append(result)
    report = {"schema_version": 1, "entity": expected_entity, "task_key": manifest["task_key"],
              "complete": not any(row["required"] for row in issues), "sources": normalized,
              "issues": issues, "dependencies": dependencies}
    if require_complete and not report["complete"]:
        raise IncompleteEvidence("Required source coverage is incomplete: " + ", ".join(
            row["source_id"] + " (" + row["code"] + ")" for row in issues if row["required"]))
    return report


def check_reference(root, reference, expected_entity, require_complete=True, at=None):
    manifest, bound = _reference(root, reference, require_hash=False)
    report = check_manifest(root, manifest, expected_entity, require_complete, at)
    report["reference"] = bound
    report["dependencies"] = [bound, *report["dependencies"]]
    return report


def check_coverage(root, artifact, expected_entity, visible_text, at=None):
    """Validate an artifact's explicit coverage and rendered limitation disclosure.

    Legacy artifacts without the new fields retain their existing validation;
    the delivery profile decides when a manifest is mandatory for current work.
    """
    if not any(key in artifact for key in ("evidence_manifest", "coverage_status", "coverage_limitations")):
        return None
    report = check_reference(root, artifact.get("evidence_manifest"), expected_entity,
                             require_complete=False, at=at)
    if artifact.get("task_key", report["task_key"]) != report["task_key"]:
        raise ValueError("Artifact coverage belongs to another task")
    status = artifact.get("coverage_status")
    if status not in {"complete", "incomplete"}:
        raise ValueError("Artifact must explicitly declare complete or incomplete source coverage")
    if status == "complete" and not report["complete"]:
        raise IncompleteEvidence("Artifact claims complete coverage despite missing required sources")
    limitations = artifact.get("coverage_limitations", [])
    if not isinstance(limitations, list):
        raise ValueError("Coverage limitations must be a list")
    issue_ids = {row["source_id"] for row in report["issues"]}
    disclosed = set()
    rendered = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", visible_text)).split())
    for row in limitations:
        if not isinstance(row, dict) or row.get("source_id") not in issue_ids or row["source_id"] in disclosed:
            raise ValueError("Coverage limitation must name one distinct unavailable source")
        text = _text(row.get("text"), "Coverage limitation text")
        if " ".join(text.split()) not in rendered:
            raise ValueError("Coverage limitation is absent from the rendered artifact")
        disclosed.add(row["source_id"])
    if disclosed != issue_ids:
        raise ValueError("Every unavailable source requires a visible coverage limitation")
    return report


def _atomic_write(root, path, value, *, replace=False):
    target = local(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(value)
    handle, temporary = tempfile.mkstemp(prefix=".evidence-", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, target)
        else:
            # An immutable checkpoint must never replace another worker's result.
            os.link(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}


def freeze_manifest(root, specification, destination, expected_entity, at=None):
    """Bind existing source files without inventing collection dates or outcomes."""
    if not isinstance(specification, dict):
        raise ValueError("Manifest specification must be an object")
    manifest = json.loads(json.dumps(specification))
    manifest.setdefault("schema_version", 1)
    manifest.setdefault("created_at", _iso(_clock(at)))
    plan = manifest.get("collection_plan")
    if isinstance(plan, str):
        manifest["collection_plan"] = {"path": plan, "sha256": file_hash(root, plan)}
    if not isinstance(manifest.get("sources"), list):
        raise ValueError("Manifest sources must be a list")
    for row in manifest["sources"]:
        if not isinstance(row, dict):
            raise ValueError("Observed source must be an object")
        if row.get("state") in {"ok", "empty"} and "sha256" not in row:
            row["sha256"] = file_hash(root, row.get("path"))
    check_manifest(root, manifest, expected_entity, require_complete=False, at=at)
    return _atomic_write(root, destination, manifest)


def publish_manifest(root, reference, destination, expected_entity, at=None):
    """Atomically advance a complete evidence set; failure leaves the prior set."""
    report = check_reference(root, reference, expected_entity, at=at)
    if local(root, destination) in {local(root, row["path"]) for row in report["dependencies"]}:
        raise ValueError("Published pointer cannot overwrite its source evidence")
    pointer = {"schema_version": 1, "entity": expected_entity, "task_key": report["task_key"],
               "manifest": report["reference"], "published_at": _iso(_clock(at)),
               "dependencies": report["dependencies"]}
    return _atomic_write(root, destination, pointer, replace=True)


def write_checkpoint(root, destination, expected_entity, task_key, stage, manifest_ref,
                     outputs, parents=(), at=None):
    report = check_reference(root, manifest_ref, expected_entity, at=at)
    if report["task_key"] != task_key:
        raise ValueError("Checkpoint manifest belongs to another task")
    _text(stage, "Checkpoint stage")
    if not isinstance(outputs, (list, tuple)) or not outputs:
        raise ValueError("A completed stage requires output files")
    output_refs = [{"path": path, "sha256": file_hash(root, path)} for path in outputs]
    if len({row["path"] for row in output_refs}) != len(output_refs):
        raise ValueError("Duplicate checkpoint output")
    parent_refs = []
    for parent in parents:
        state = inspect_checkpoint(root, parent, expected_entity, task_key, at=at)
        if state["state"] != "reusable":
            raise ValueError("Parent checkpoint is not reusable: " + str(parent))
        parent_refs.append(state["reference"])
    protected = {local(root, row["path"]) for row in [*report["dependencies"], *output_refs, *parent_refs]}
    if local(root, destination) in protected:
        raise ValueError("Checkpoint cannot overwrite a dependency or output")
    checkpoint = {"schema_version": 1, "entity": expected_entity, "task_key": task_key,
                  "stage": stage, "completed_at": _iso(_clock(at)),
                  "evidence_manifest": report["reference"], "outputs": output_refs, "parents": parent_refs}
    return _atomic_write(root, destination, checkpoint)


def inspect_checkpoint(root, reference, expected_entity, task_key, at=None, _seen=None):
    """Absence, corruption, staleness and temporary storage failure stay distinct."""
    path = reference.get("path") if isinstance(reference, dict) else reference
    target = local(root, path)
    seen = set() if _seen is None else set(_seen)
    if target in seen or len(seen) >= 100:
        return {"state": "corrupt", "reason": "Checkpoint dependency cycle or excessive depth"}
    seen.add(target)
    try:
        data = _bytes(target)
    except FileNotFoundError:
        return {"state": "absent", "reason": "Checkpoint does not exist"}
    bound = {"path": path, "sha256": hashlib.sha256(data).hexdigest()}
    if isinstance(reference, dict) and reference.get("sha256") != bound["sha256"]:
        return {"state": "stale", "reason": "Checkpoint was changed"}
    try:
        checkpoint = _decode(data)
        _identity(checkpoint, expected_entity, "Checkpoint")
        if checkpoint["task_key"] != task_key:
            raise ValueError("Checkpoint belongs to another task")
        _text(checkpoint.get("stage"), "Checkpoint stage")
        if timestamp(checkpoint.get("completed_at")) > _clock(at):
            raise ValueError("Checkpoint completion is dated in the future")
        report = check_reference(root, checkpoint.get("evidence_manifest"), expected_entity,
                                 require_complete=False, at=at)
        if report["task_key"] != task_key:
            raise ValueError("Checkpoint evidence belongs to another task")
        if not report["complete"]:
            return {"state": "stale", "reason": "Checkpoint inputs require refresh", "issues": report["issues"]}
        outputs = checkpoint.get("outputs")
        parents = checkpoint.get("parents")
        if not isinstance(outputs, list) or not outputs or not isinstance(parents, list):
            raise ValueError("Checkpoint outputs and parents are invalid")
        for output in outputs:
            if not isinstance(output, dict) or not SHA256.fullmatch(str(output.get("sha256", ""))):
                raise ValueError("Checkpoint output lacks its content digest")
            try:
                actual = file_hash(root, output.get("path"))
            except FileNotFoundError:
                return {"state": "stale", "reason": "Checkpoint output is missing"}
            if actual != output["sha256"]:
                return {"state": "stale", "reason": "Checkpoint output changed"}
        for parent in parents:
            if not isinstance(parent, dict) or not SHA256.fullmatch(str(parent.get("sha256", ""))):
                raise ValueError("Checkpoint parent lacks its content digest")
            status = inspect_checkpoint(root, parent, expected_entity, task_key, at=at, _seen=seen)
            if status["state"] != "reusable":
                return {"state": "stale", "reason": "Parent checkpoint is " + status["state"]}
        return {"state": "reusable", "stage": checkpoint["stage"], "reference": bound,
                "outputs": outputs, "evidence_manifest": report["reference"]}
    except (ValueError, TypeError, UnicodeError) as exc:
        return {"state": "corrupt", "reason": str(exc)}


def _entity(root):
    config = read_json(local(root, "config/maturation.json"))
    if not isinstance(config, dict):
        raise ValueError("Maturation configuration must be an object")
    return _text(config.get("entity"), "Configured entity")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--at", help="Explicit observation time for reproducible checks")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("manifest")
    check.add_argument("--allow-incomplete", action="store_true")
    freeze = sub.add_parser("freeze")
    freeze.add_argument("specification")
    freeze.add_argument("destination")
    publish = sub.add_parser("publish")
    publish.add_argument("manifest")
    publish.add_argument("destination")
    save = sub.add_parser("checkpoint")
    save.add_argument("destination")
    save.add_argument("--task-key", required=True)
    save.add_argument("--stage", required=True)
    save.add_argument("--manifest", required=True)
    save.add_argument("--output", action="append", required=True)
    save.add_argument("--parent", action="append", default=[])
    resume = sub.add_parser("resume")
    resume.add_argument("checkpoint")
    resume.add_argument("--task-key", required=True)
    args = parser.parse_args(argv)
    try:
        entity = _entity(args.root)
        if args.command == "check":
            result = check_reference(args.root, args.manifest, entity,
                                     require_complete=not args.allow_incomplete, at=args.at)
        elif args.command == "freeze":
            result = freeze_manifest(args.root, read_json(local(args.root, args.specification)),
                                     args.destination, entity, at=args.at)
        elif args.command == "publish":
            result = publish_manifest(args.root, args.manifest, args.destination, entity, at=args.at)
        elif args.command == "checkpoint":
            result = write_checkpoint(args.root, args.destination, entity, args.task_key, args.stage,
                                      args.manifest, args.output, args.parent, at=args.at)
        else:
            result = inspect_checkpoint(args.root, args.checkpoint, entity, args.task_key, at=args.at)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if result.get("state", "reusable") == "reusable" else 1
    except EvidenceUnavailable as exc:
        print(json.dumps({"state": "unavailable", "retryable": True, "error": str(exc)}))
        return 3
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"state": "invalid", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
