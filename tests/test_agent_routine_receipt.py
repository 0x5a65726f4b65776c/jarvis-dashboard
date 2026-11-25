"""Observed routine evidence: actual gate checks, immutable outputs and local claims.

Fixture review verdicts exercise the delivery protocol; they are not live
runtime or independent reviewer attestations.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


receipt = module("routine_receipt_under_test", ROOT / "scripts/routine_receipt.py")


class RoutineReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = receipt.timestamp("2026-11-24T10:08:00Z")
        self.due = "2026-11-24T05:07:00-05:00"
        self.put("facts/result.json", '{"result":"observed"}\n')

    def put(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def symlink(self, path, target, *, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except OSError:
            if os.name == "nt":
                self.skipTest("This Windows account cannot create symlink fixtures")
            raise

    def start(self, run="session-one", **kwargs):
        return receipt.record(self.root, "jarvis-morning", self.due, run, "started",
                              now=kwargs.pop("now", self.now), **kwargs)

    def proof(self, root, run, outputs):
        return {"method": "agent_gate.check", "session_id": run, "release_id": "a" * 64,
                "artifact_hashes": [r["sha256"] for r in outputs]}

    def terminal(self, run="session-one", state="verified", **kwargs):
        return receipt.record(self.root, "jarvis-morning", self.due, run, "terminal",
                              now=kwargs.pop("now", self.now + dt.timedelta(seconds=1)),
                              state=state, outputs=kwargs.pop("outputs", ["facts/result.json"]),
                              verifier=kwargs.pop("verifier", self.proof), **kwargs)

    def events(self):
        return receipt.read_events(self.root / receipt.RELATIVE_LOG)

    def test_actual_start_does_not_backdate_monitoring_to_due_time(self):
        self.start()
        coverage = self.events()[0]
        self.assertEqual("monitoring_started", coverage["event"])
        self.assertEqual(receipt.iso(self.now), coverage["at"])
        self.assertNotEqual(receipt.iso(receipt.timestamp(self.due)), coverage["at"])

    def test_prospective_enrollment_has_actual_time_and_no_invented_run(self):
        row = receipt.record(self.root, "jarvis-morning", event="monitoring_started",
                             now=self.now, reason="Durable receipt collection enabled for future occurrences.")
        self.assertEqual(receipt.iso(self.now), row["at"])
        self.assertNotIn("scheduled_at", row)
        self.assertNotIn("run_id", row)
        with self.assertRaises(ValueError):
            receipt.record(self.root, "jarvis-morning", event="monitoring_started", now=self.now)

    def test_identity_normalizes_offset_and_repeated_start_is_idempotent(self):
        first = self.start()
        second = receipt.record(self.root, "jarvis-morning", "2026-11-24T10:07:00Z",
                                "session-one", "started", now=self.now)
        self.assertEqual(first, second)
        self.assertEqual(2, len(self.events()))

    def test_second_local_run_is_refused_while_first_is_active(self):
        self.start()
        with self.assertRaisesRegex(ValueError, "another local run"):
            self.start("session-two")
        self.assertEqual(2, len(self.events()))

    def test_retry_keeps_occurrence_identity_and_all_attempts(self):
        first = self.start()
        self.terminal(state="failed", reason="Read failed before any external mutation.")
        second = self.start("session-two", now=self.now + dt.timedelta(seconds=2))
        self.assertEqual(first["occurrence_id"], second["occurrence_id"])
        self.assertEqual(4, len(self.events()))

    def test_verified_occurrence_cannot_get_a_new_local_attempt(self):
        self.start()
        self.terminal()
        with self.assertRaisesRegex(ValueError, "already terminal"):
            self.start(now=self.now + dt.timedelta(seconds=2))
        with self.assertRaisesRegex(ValueError, "already verified"):
            self.start("session-two", now=self.now + dt.timedelta(seconds=2))

    def test_terminal_requires_start_and_is_immutable(self):
        with self.assertRaisesRegex(ValueError, "observed start"):
            self.terminal()
        self.start()
        self.terminal()
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.terminal()

    def test_unknown_result_needs_reason_and_never_claims_verification(self):
        self.start()
        with self.assertRaises(ValueError):
            self.terminal(state="unverified")
        result = self.terminal(state="unverified", reason="Gate unavailable; observations retained.",
                               verifier=lambda *args: self.fail("nonverified work invoked the gate"))
        self.assertIsNone(result["verification"])
        self.assertTrue(result["outputs"])

    def test_verified_output_bytes_are_archived_before_mutable_source_changes(self):
        self.start()
        result = self.terminal()
        archive = self.root / result["outputs"][0]["path"]
        original = archive.read_bytes()
        self.put("facts/result.json", '{"result":"next day"}\n')
        self.assertEqual(original, archive.read_bytes())
        self.assertEqual(hashlib.sha256(original).hexdigest(), result["outputs"][0]["sha256"])
        self.assertEqual("facts/result.json", result["outputs"][0]["source_path"])

    def test_output_change_during_verification_refuses_terminal(self):
        self.start()
        def mutate(root, run, outputs):
            proof = self.proof(root, run, outputs)
            self.put("facts/result.json", '{"changed":true}\n')
            return proof
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.terminal(verifier=mutate)
        self.assertFalse(any(r["event"] == "terminal" for r in self.events()))

    def test_missing_or_duplicate_output_is_not_verified(self):
        self.start()
        for outputs in ([], ["missing.json"], ["facts/result.json", "facts/result.json"]):
            with self.subTest(outputs=outputs), self.assertRaises(ValueError):
                self.terminal(outputs=outputs)

    def test_wrong_session_or_hash_or_release_proof_is_rejected(self):
        self.start()
        for field, value in (("session_id", "another-run"), ("artifact_hashes", []),
                             ("release_id", None), ("method", "caller says so")):
            def forged(root, run, outputs, field=field, value=value):
                proof = self.proof(root, run, outputs)
                proof[field] = value
                return proof
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.terminal(verifier=forged)

    def test_preexisting_archive_collision_is_not_overwritten(self):
        self.start()
        digest = receipt.artifact(self.root, "facts/result.json")["sha256"]
        path = self.put("capture/routine_receipts/" + digest + ".artifact", "different bytes")
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            self.terminal()
        self.assertEqual("different bytes", path.read_text())

    def test_traversal_absolute_nonstring_and_symlink_output_are_rejected(self):
        self.start()
        outside = self.root.parent / (self.root.name + "-outside")
        outside.write_text("outside")
        self.addCleanup(outside.unlink)
        self.symlink(self.root / "alias", outside)
        for path in ("../" + outside.name, str(outside), None, "alias", "facts/./result.json"):
            with self.subTest(path=path), self.assertRaises((ValueError, TypeError)):
                self.terminal(outputs=[path])

    def test_capture_symlink_cannot_redirect_receipt_or_archive_writes(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.symlink(self.root / "capture", outside, directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.start()
        self.assertEqual([], list(outside.iterdir()))

    def test_archive_directory_symlink_is_rejected(self):
        self.start()
        self.symlink(self.root / "capture/routine_receipts", self.root / "facts", directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.terminal()

    def test_future_due_and_naive_clock_are_rejected(self):
        with self.assertRaises(ValueError):
            self.start(now=self.now - dt.timedelta(hours=1))
        with self.assertRaises(ValueError):
            self.start(now=dt.datetime(2026, 11, 24, 10, 8))

    def test_rfc3339_type_and_timestamp_validation(self):
        for value in (None, True, 1, "2026-11-24", "20261124T100800Z",
                      "2026-11-24 10:08:00Z", "2026-11-24T10:08:00",
                      "2026-11-24T10:08:00+0000", "2026-02-30T10:08:00Z"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                receipt.timestamp(value)
        self.assertEqual(receipt.timestamp("2026-11-24T10:08:00.123456Z"),
                         receipt.timestamp("2026-11-24T05:08:00.123456-05:00"))

    def test_schema_boolean_duplicate_keys_nonfinite_and_wrong_shapes_fail(self):
        self.start()
        row = self.events()[-1]
        bad = []
        for field, value in (("schema_version", True), ("run_id", []), ("event", "success"),
                             ("scheduled_at", "2026-11-24"), ("occurrence_id", "other")):
            candidate = copy.deepcopy(row)
            candidate[field] = value
            bad.append(json.dumps(candidate))
        bad.extend(["[]", '{"schema_version":1,"schema_version":1}', '{"v":NaN}'])
        for raw in bad:
            self.put("capture/routine_runs.jsonl", raw + "\n")
            with self.subTest(raw=raw), self.assertRaises((ValueError, TypeError)):
                self.events()

    def test_terminal_before_start_and_duplicate_records_fail_read(self):
        self.start()
        result = self.terminal()
        original = self.events()
        for rows in (original + [result], original + [original[1]],
                     [original[0], result, original[1]]):
            self.put("capture/routine_runs.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
            with self.assertRaises(ValueError):
                self.events()
        early = copy.deepcopy(result)
        early["at"] = "2026-11-24T10:07:30Z"
        self.put("capture/routine_runs.jsonl", "".join(json.dumps(r) + "\n" for r in original[:2] + [early]))
        with self.assertRaisesRegex(ValueError, "preceding observed start"):
            self.events()

    def test_fake_success_exit_without_gate_receipt_fails(self):
        self.start()
        self.put("scripts/agent_gate.py", 'print("all good")\n')
        with self.assertRaises(ValueError):
            self.terminal(verifier=receipt.verify_delivery)
        self.assertEqual(2, len(self.events()))

    def test_missing_gate_cannot_be_verified(self):
        self.start()
        with self.assertRaisesRegex(ValueError, "gate unavailable"):
            self.terminal(verifier=receipt.verify_delivery)


class RoutineDeliveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        helpers = module("routine_delivery_fixture", ROOT / "tests/test_agent_gate.py")
        self.fixture = helpers.DeliveryGateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = dt.datetime.now(dt.timezone.utc)
        self.due = receipt.iso(self.now - dt.timedelta(minutes=1))
        receipt.record(self.fixture.root, "test-routine", self.due, self.fixture.gate.session,
                       "started", now=self.now)

    def terminal(self, **kwargs):
        return receipt.record(self.fixture.root, "test-routine", self.due, self.fixture.gate.session,
                              "terminal", state="verified", outputs=["evidence/receipt.json"], **kwargs)

    def test_actual_gate_refuses_missing_reviews_and_clears_reviewed_exact_bytes(self):
        self.fixture.start()
        with self.assertRaisesRegex(ValueError, "gate did not verify"):
            self.terminal()
        release = self.fixture.stage()
        self.fixture.gate.owner_review(self.fixture.review(release))
        self.assertEqual({}, self.fixture.hermes(release))
        result = self.terminal()
        self.assertEqual(release["release_id"], result["verification"]["release_id"])
        self.assertEqual(self.fixture.gate.session, result["verification"]["session_id"])
        self.assertEqual((self.fixture.root / "evidence/receipt.json").read_bytes(),
                         (self.fixture.root / result["outputs"][0]["path"]).read_bytes())

    def test_actual_gate_refuses_changed_output_after_review(self):
        self.fixture.cleared()
        self.fixture.put("evidence/receipt.json", '{"result":"changed after review"}\n')
        with self.assertRaisesRegex(ValueError, "gate did not verify"):
            self.terminal()

    def test_reviewed_silent_completion_keeps_gate_and_durable_artifact_evidence(self):
        self.fixture.start_silent()
        release = self.fixture.stage(self.fixture.silent_packet())
        with self.assertRaisesRegex(ValueError, "gate did not verify"):
            self.terminal()
        self.fixture.gate.owner_review(self.fixture.review(release))
        with self.assertRaisesRegex(ValueError, "gate did not verify"):
            self.terminal()
        self.assertEqual({}, self.fixture.hermes(release))
        self.assertEqual({}, self.fixture.stop(""))
        result = self.terminal()
        self.assertEqual("verified", result["state"])
        self.assertEqual(release["release_id"], result["verification"]["release_id"])
        self.assertEqual((self.fixture.root / "evidence/receipt.json").read_bytes(),
                         (self.fixture.root / result["outputs"][0]["path"]).read_bytes())

    def test_reviewed_partial_delivery_cannot_be_recorded_as_verified_completion(self):
        self.fixture.start()
        self.fixture.gate.plan(self.fixture.requirements())
        self.fixture.put("evidence/receipt.json", '{"result":"partial evidence; requested value unavailable"}\n')
        packet = self.fixture.packet()
        packet.update(outcome="partial", summary="The requested value remains unavailable", claims=[],
                      unresolved=[{"issue": "Required source unavailable", "owner": "Owning orchestrator",
                                   "next_action": "Retry the unavailable read before claiming completion"}])
        packet["acceptance"][0].update(met=False, evidence=[])
        self.fixture.reply = "The requested value remains unavailable; the partial evidence is retained."
        release = self.fixture.stage(packet)
        self.fixture.gate.owner_review(self.fixture.review(release))
        self.assertEqual({}, self.fixture.hermes(release))
        ordinary_check = subprocess.run(
            [sys.executable, str(self.fixture.root / "scripts/agent_gate.py"), "check",
             "--session", self.fixture.gate.session, "--artifact", "evidence/receipt.json"],
            cwd=self.fixture.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, ordinary_check.returncode, ordinary_check.stderr)
        with self.assertRaisesRegex(ValueError, "gate did not verify"):
            self.terminal()
        recorded = receipt.record(
            self.fixture.root, "test-routine", self.due, self.fixture.gate.session, "terminal",
            state="unverified", outputs=["evidence/receipt.json"],
            reason="Reviewed partial evidence does not establish routine completion.")
        self.assertEqual("unverified", recorded["state"])
        self.assertIsNone(recorded["verification"])


if __name__ == "__main__":
    unittest.main()
