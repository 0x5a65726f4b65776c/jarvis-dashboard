#!/usr/bin/env python3
"""Executed regression, recurrence, and stale-evidence tests in temporary repos."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import lesson
import learning_evidence as evidence


class LearningEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = lesson.REPO, lesson.LEDGER
        lesson.REPO, lesson.LEDGER = str(self.root), str(self.root / "capture/lessons.jsonl")
        self.rule = "Reject a delivery when destination evidence is absent"
        self.surface = "doctrine/delivery.md"
        self.write(self.surface, self.rule + "\n")
        self.write("scripts/test_check.py", "from pathlib import Path\nassert Path('doctrine/delivery.md').read_text().startswith('Reject a delivery')\n")
        self.write("scripts/test_fail.py", "raise SystemExit(3)\n")
        self.assertEqual(self.call("add", "--surface", self.surface, "--rule", self.rule,
                                   "--why", "process success alone does not demonstrate destination completion"), 0)
        self.ident = lesson.load()[0]["id"]

    def tearDown(self):
        lesson.REPO, lesson.LEDGER = self.original
        self.temp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def call(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return lesson.main(list(args))

    def verify(self, run="install", test="scripts/test_check.py", extra=()):
        if self.row().get("promotion"):
            self.context(run)
        return self.call("verify", self.ident, "--run-key", run,
                         "--criterion", "the destination rule must remain mandatory",
                         "--test-file", test, "--receipt", "capture/%s.json" % run,
                         *extra, "--", sys.executable, test)

    def apply(self, run="install"):
        return self.call("apply", self.ident, "--receipt", "capture/%s.json" % run)

    def row(self):
        return lesson.load()[0]

    def context_path(self, run):
        return ".claude/state/agent-gate/test-session/contexts/%s.json" % run

    def context(self, run):
        self.write(self.context_path(run), json.dumps({
            "schema_version": 1, "contract_version": evidence.CONTEXT_CONTRACT,
            "session_id": "test-session", "turn_id": run, "run_key": run, "at": evidence.now(),
            "surfaces": {self.surface: evidence.digest(self.root / self.surface)},
            "fidelity_sha256": "f" * 64,
        }))

    def observe(self, run, result="pass", loaded=None):
        return self.call("observe", self.ident, "--run-key", run, "--result", result,
                         "--evidence", "capture/%s.json" % run,
                         "--loaded-sha", loaded or self.row()["promotion"]["surface_sha256"],
                         "--context-receipt", self.context_path(run),
                         "--note", "this executed local regression covers the named delivery rule only")

    def test_apply_needs_real_executed_receipt(self):
        with self.assertRaises(SystemExit):
            self.call("apply", self.ident, "--surface", self.surface)
        self.assertEqual(self.row()["state"], "open")

    def test_successful_install_is_not_later_validation(self):
        self.assertEqual(self.verify(), 0)
        self.assertEqual(self.apply(), 0)
        status = evidence.assess(self.row(), self.root)
        self.assertTrue(status["promotion_verified"])
        self.assertFalse(status["validated"])
        self.assertEqual(status["status"], "installed")

    def test_failed_check_cannot_be_promoted(self):
        self.assertEqual(self.verify(test="scripts/test_fail.py"), 1)
        record = json.loads((self.root / "capture/install.json").read_text())
        self.assertEqual(record["exit_code"], 3)
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.row()["state"], "open")

    def test_success_flag_cannot_override_exit_code(self):
        self.verify(test="scripts/test_fail.py")
        path = self.root / "capture/install.json"
        record = json.loads(path.read_text())
        record["passed"] = True
        path.write_text(json.dumps(record))
        self.assertEqual(self.apply(), 1)

    def test_absent_rule_cannot_be_verified(self):
        self.write(self.surface, "No applicable correction is installed.\n")
        self.assertEqual(self.verify(), 1)
        self.assertFalse((self.root / "capture/install.json").exists())

    def test_target_change_invalidates_receipt(self):
        self.verify()
        self.write(self.surface, self.rule + "\nAn unchecked exception.\n")
        self.assertEqual(self.apply(), 1)

    def test_test_change_invalidates_receipt(self):
        self.verify()
        self.write("scripts/test_check.py", "pass\n")
        self.assertEqual(self.apply(), 1)

    def test_later_test_of_same_loaded_revision_validates(self):
        self.verify()
        self.apply()
        self.verify(run="later")
        self.assertEqual(self.observe("later"), 0)
        status = evidence.assess(self.row(), self.root)
        self.assertEqual(status["status"], "validated")
        self.assertTrue(status["validated"])

    def test_install_run_cannot_validate_itself(self):
        self.verify()
        self.apply()
        self.assertEqual(self.observe("install"), 1)
        self.assertEqual(self.row()["state"], "applied")

    def test_wrong_loaded_revision_is_refused(self):
        self.verify()
        self.apply()
        self.verify(run="later")
        self.assertEqual(self.observe("later", loaded="f" * 64), 1)

    def test_runtime_context_for_wrong_revision_is_refused(self):
        self.verify()
        self.apply()
        self.verify(run="later")
        path = self.root / self.context_path("later")
        context = json.loads(path.read_text())
        context["surfaces"][self.surface] = "e" * 64
        path.write_text(json.dumps(context))
        self.assertEqual(self.observe("later"), 1)

    def test_previous_contract_observation_retains_its_bound_evidence(self):
        self.verify()
        self.apply()
        self.verify(run="later")
        path = self.root / self.context_path("later")
        context = json.loads(path.read_text())
        context["contract_version"] = "2026-11-17.1"
        path.write_text(json.dumps(context))
        self.assertEqual(self.observe("later"), 0)
        self.assertEqual(evidence.assess(self.row(), self.root)["status"], "validated")

    def test_unrecognized_context_contract_is_not_promoted(self):
        self.verify()
        self.apply()
        self.verify(run="later")
        path = self.root / self.context_path("later")
        context = json.loads(path.read_text())
        context["contract_version"] = "unreviewed-contract"
        path.write_text(json.dumps(context))
        self.assertEqual(self.observe("later"), 1)

    def test_runtime_context_for_another_run_is_refused(self):
        self.verify()
        self.apply()
        self.verify(run="later")
        path = self.root / self.context_path("later")
        context = json.loads(path.read_text())
        context["run_key"] = "different"
        path.write_text(json.dumps(context))
        self.assertEqual(self.observe("later"), 1)

    def test_runtime_context_created_after_check_is_refused(self):
        self.verify()
        self.apply()
        self.verify(run="later")
        self.context("later")
        self.assertEqual(self.observe("later"), 1)

    def test_runtime_context_tampering_invalidates_validation(self):
        self.verify()
        self.apply()
        self.verify(run="later")
        self.observe("later")
        with (self.root / self.context_path("later")).open("a") as handle:
            handle.write("\n")
        self.assertEqual(evidence.assess(self.row(), self.root)["status"], "stale_evidence")

    def test_duplicate_run_cannot_count_twice(self):
        self.verify()
        self.apply()
        self.verify(run="later")
        self.assertEqual(self.observe("later"), 0)
        self.assertEqual(self.observe("later"), 1)
        self.assertEqual(len(self.row()["observations"]), 1)

    def test_failure_reopens_and_retains_evidence(self):
        self.verify()
        self.apply()
        self.assertEqual(self.verify(run="recurrence", test="scripts/test_fail.py"), 1)
        self.assertEqual(self.observe("recurrence", result="fail"), 0)
        row = self.row()
        self.assertEqual(row["state"], "reopened")
        self.assertEqual(evidence.assess(row, self.root)["recurrences"], 1)
        self.assertIn("promotion", row)
        self.assertEqual(len(row["observations"]), 1)
        self.assertEqual(self.verify(run="unchanged"), 0)
        self.assertEqual(self.apply("unchanged"), 1)

    def test_repair_preserves_promotion_history(self):
        self.verify()
        self.apply()
        old = self.row()["promotion"]["surface_sha256"]
        self.verify(run="recurrence", test="scripts/test_fail.py")
        self.observe("recurrence", result="fail")
        self.write(self.surface, self.rule + "\nCheck the actual destination receipt before delivery.\n")
        self.verify(run="repair")
        self.assertEqual(self.apply("repair"), 0)
        row = self.row()
        self.assertEqual(row["promotion_history"][0]["surface_sha256"], old)
        self.assertEqual(len(row["observations"]), 1)
        self.assertNotEqual(row["promotion"]["surface_sha256"], old)
        self.assertFalse(evidence.assess(row, self.root)["validated"])

    def test_tampered_observation_is_not_validated(self):
        self.verify()
        self.apply()
        self.verify(run="later")
        self.observe("later")
        with (self.root / "capture/later.json").open("a") as handle:
            handle.write("\n")
        status = evidence.assess(self.row(), self.root)
        self.assertFalse(status["validated"])
        self.assertEqual(status["status"], "stale_evidence")

    def test_legacy_applied_is_readable_but_not_evidence(self):
        row = self.row()
        row["state"] = "applied"
        lesson.save([row])
        status = evidence.assess(row, self.root)
        self.assertEqual(status["status"], "legacy_unverified")
        self.assertFalse(status["promotion_verified"])
        self.assertEqual(self.call("check"), 0)
        self.assertEqual(self.call("check", "--evidence"), 1)

    def test_duplicate_applied_lesson_is_not_recreated(self):
        self.verify()
        self.apply()
        self.assertEqual(self.call("add", "--surface", self.surface, "--rule", self.rule,
                                   "--why", "process success alone does not demonstrate destination completion"), 1)

    def test_missing_or_trivial_command_is_refused(self):
        args = dict(lesson_id=self.ident, run_key="x", surface=self.surface,
                    anchor=self.rule, criterion="rule is mandatory", test_file="scripts/test_check.py",
                    inputs=[], receipt="capture/trivial.json")
        with self.assertRaises(ValueError):
            evidence.run_check(self.root, command=["true"], **args)
        with self.assertRaises(ValueError):
            evidence.run_check(self.root, command=[sys.executable, "-c", "pass", "scripts/test_check.py"], **args)

    def test_nonexecuting_runner_modes_cannot_create_passing_receipts(self):
        args = dict(lesson_id=self.ident, run_key="x", surface=self.surface,
                    anchor=self.rule, criterion="rule is mandatory", test_file="scripts/test_fail.py",
                    inputs=[], receipt="capture/not-executed.json")
        commands = [
            [sys.executable, "-m", "unittest", "--help", "scripts/test_fail.py"],
            [sys.executable, "-m", "unittest", "scripts/test_fail.py", "-h"],
            [sys.executable, "-m", "unittest", "-k", "missing", "scripts/test_fail.py"],
            [sys.executable, "-m", "pytest", "--collect-only", "scripts/test_fail.py"],
            [sys.executable, "-m", "pytest", "--setup-only", "scripts/test_fail.py"],
            [sys.executable, "-m", "pytest", "scripts/test_check.py", "scripts/test_fail.py", "-k", "check"],
            [sys.executable, "scripts/test_fail.py", "--help"],
        ]
        for command in commands:
            with self.subTest(command=command), self.assertRaises(ValueError):
                evidence.run_check(self.root, command=command, **args)
        self.assertFalse((self.root / "capture/not-executed.json").exists())

    def test_unittest_zero_cases_is_not_a_passing_regression(self):
        self.write("scripts/__init__.py", "")
        self.write("scripts/test_empty.py", "if __name__ == '__main__':\n    raise SystemExit(3)\n")
        record = evidence.run_check(
            self.root, lesson_id=self.ident, run_key="empty", surface=self.surface,
            anchor=self.rule, criterion="rule is mandatory", test_file="scripts/test_empty.py",
            inputs=[], receipt="capture/empty.json",
            command=[sys.executable, "-m", "unittest", "scripts/test_empty.py"])
        self.assertIn(record["exit_code"], (0, 5))  # unittest versions differ on empty suites
        self.assertEqual(record["executed_tests"], 0)
        self.assertFalse(record["passed"])

    def test_unittest_actual_passing_case_is_recorded(self):
        self.write("scripts/__init__.py", "")
        self.write("scripts/test_case.py", "import unittest\nclass Case(unittest.TestCase):\n    def test_rule(self):\n        self.assertEqual(2 + 2, 4)\n")
        record = evidence.run_check(
            self.root, lesson_id=self.ident, run_key="case", surface=self.surface,
            anchor=self.rule, criterion="rule is mandatory", test_file="scripts/test_case.py",
            inputs=[], receipt="capture/case.json",
            command=[sys.executable, "-m", "unittest", "scripts/test_case.py"])
        self.assertEqual(record["executed_tests"], 1)
        self.assertTrue(record["passed"])

    def test_path_traversal_and_escaping_symlink_are_refused(self):
        for path in ("../outside.json", "/tmp/outside.json"):
            with self.assertRaises(ValueError):
                evidence.local_path(self.root, path, exists=False)
        (self.root / "escape").symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            evidence.local_path(self.root, "escape/outside.json", exists=False)

    def test_test_mutation_cannot_claim_success(self):
        self.write("scripts/test_mutate.py", "from pathlib import Path\nPath('doctrine/delivery.md').write_text('removed')\n")
        self.assertEqual(self.verify(test="scripts/test_mutate.py"), 1)
        record = json.loads((self.root / "capture/install.json").read_text())
        self.assertEqual(record["exit_code"], 0)
        self.assertFalse(record["passed"])
        self.assertFalse(record["inputs_unchanged"])

    def test_timeout_is_recorded_as_failure(self):
        self.write("scripts/test_slow.py", "import time\ntime.sleep(2)\n")
        self.assertEqual(self.verify(test="scripts/test_slow.py", extra=("--timeout", "1")), 1)
        record = json.loads((self.root / "capture/install.json").read_text())
        self.assertIsNone(record["exit_code"])
        self.assertFalse(record["passed"])


if __name__ == "__main__":
    unittest.main()
