"""Reservations must survive races, failed workers, and incomplete cost telemetry."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("execution_control", ROOT / "scripts/execution_control.py")
control_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control_module)


def competing_reservation(root, ready, start, results):
    control = control_module.TaskControl(root, "task", "atg")
    ready.put(True)
    start.wait(10)
    try:
        control.reserve("worker", 600, (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat())
        results.put("admitted")
    except control_module.ControlError:
        results.put("refused")


class ExecutionControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "config/maturation.json").write_text(json.dumps({"entity": "atg", "execution": {
            "max_dispatches": 32, "review_dispatches": 4, "deadline_seconds": 7200,
            "review_grace_seconds": 900, "max_concurrent": 4}}))
        self.control = control_module.TaskControl(self.root, "task", "atg")
        self.limits = {"total_microusd": 1000, "verification_reserve_microusd": 200,
                       "max_attempts": 6, "max_concurrent": 4,
                       "deadline_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                       "authority_ref": "test fixture only: no provider calls"}
        self.control.configure(self.limits)

    def reserve(self, amount=100, **kwargs):
        return self.control.reserve("worker", amount, (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(), **kwargs)["lease"]

    def test_parallel_workers_cannot_reserve_the_same_remaining_money(self):
        ctx = multiprocessing.get_context("spawn")
        ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
        jobs = [ctx.Process(target=competing_reservation, args=(str(self.root), ready, start, results)) for _ in range(2)]
        for job in jobs:
            job.start()
        for _ in jobs:
            self.assertTrue(ready.get(timeout=15))
        start.set()
        outcomes = [results.get(timeout=15) for _ in jobs]
        for job in jobs:
            job.join(15)
            self.assertEqual(0, job.exitcode)
        self.assertCountEqual(["admitted", "refused"], outcomes)
        self.assertEqual(600, self.control.status()["reserved_or_measured_microusd"])

    def test_verification_reserve_is_not_available_to_work(self):
        first = self.reserve(800)
        with self.assertRaises(control_module.ControlError):
            self.reserve(1)
        review = self.reserve(200, purpose="verification")
        self.control.finish(first, 800, "observed provider cost")
        self.control.finish(review, 200, "observed reviewer cost")
        self.assertEqual(0, self.control.status()["remaining_microusd"])

    def test_children_share_root_allowance_and_cannot_outlive_parent(self):
        parent_expiry = (datetime.now(timezone.utc) + timedelta(seconds=40)).isoformat()
        parent = self.control.reserve("parent", 400, parent_expiry)["lease"]
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()
        child = self.control.reserve("child", 400, expiry, parent=parent)["lease"]
        with self.assertRaises(control_module.ControlError):
            self.control.reserve("child-two", 1, expiry, parent=parent)
        with self.assertRaises(control_module.ControlError):
            self.control.finish(parent, 400, "parent cost")
        self.control.finish(child, 200, "child stopped with measured cost")
        self.control.finish(parent, 400, "parent stopped with measured cost")
        with self.assertRaises(control_module.ControlError):
            self.control.reserve("late child", 0, expiry, parent=parent)

    def test_expired_worker_is_fenced_and_retains_its_liability_and_slot(self):
        token = self.reserve(700)
        future = datetime.now(timezone.utc) + timedelta(minutes=2)
        later = control_module.TaskControl(self.root, "task", "atg", now=lambda: future)
        with self.assertRaises(control_module.ControlError):
            later.assert_active(token)
        with self.assertRaises(control_module.ControlError):
            later.finish(token, 0, "late worker self-report")
        self.assertEqual(1, later.status()["possibly_running"])
        self.assertEqual(700, later.status()["reserved_or_measured_microusd"])
        self.assertEqual(1, later.status()["expired_unreconciled"])
        expired = control_module.TaskControl(self.root, "task", "atg", now=lambda: future + timedelta(hours=1))
        self.assertEqual("expired", expired.status()["admission_state"])
        self.assertTrue(expired.status()["deadline_expired"])
        later.reconcile(token, 650, "owner observed process stop and provider cost")
        self.assertEqual(0, later.status()["possibly_running"])

    def test_parking_invalidates_all_leases_without_releasing_money(self):
        token = self.reserve(700)
        self.control.park("Deadline reached; waiting on access")
        with self.assertRaises(control_module.ControlError):
            self.control.assert_active(token)
        with self.assertRaises(control_module.ControlError):
            self.control.resume("New access", "access receipt")
        self.assertEqual(700, self.control.status()["reserved_or_measured_microusd"])
        self.control.reconcile(token, 500, "owner confirmed worker stopped and billed amount")
        self.control.resume("Access granted", "dated access receipt")
        self.assertEqual("active", self.control.status()["state"])

    def test_local_commit_and_concurrent_park_have_one_enforced_order(self):
        token = self.reserve(100)
        attempted, parked = threading.Event(), threading.Event()
        def park():
            attempted.set()
            self.control.park("Cancellation requested")
            parked.set()
        result = self.root / "result.txt"
        with self.control.effect_guard(token):
            worker = threading.Thread(target=park)
            worker.start()
            self.assertTrue(attempted.wait(1))
            self.assertFalse(parked.wait(0.05))
            result.write_text("Commit admitted before cancellation")
        worker.join(2)
        self.assertTrue(parked.is_set())
        with self.assertRaises(control_module.ControlError):
            with self.control.effect_guard(token):
                result.write_text("Late stale write")
        self.assertEqual("Commit admitted before cancellation", result.read_text())

    def test_unknown_cost_is_not_reported_as_zero(self):
        token = self.reserve(700)
        self.control.finish(token, None, "worker stopped; provider cost unavailable")
        result = self.control.status()
        self.assertIsNone(result["actual_microusd"])
        self.assertEqual(700, result["reserved_or_measured_microusd"])
        self.control.reconcile(token, 600, "provider invoice")
        self.assertEqual(600, self.control.status()["actual_microusd"])

    def test_real_cost_overrun_is_recorded_before_further_work_is_parked(self):
        token = self.reserve(100)
        self.control.finish(token, 150, "provider actual exceeds estimate")
        self.assertEqual(150, self.control.status()["actual_microusd"])
        self.assertEqual("parked", self.control.status()["state"])
        with self.assertRaises(control_module.ControlError):
            self.reserve()

    def test_existing_limits_cannot_be_reset_or_widened(self):
        self.reserve(700)
        with self.assertRaises(control_module.ControlError):
            self.control.configure({**self.limits, "total_microusd": 2000})
        with self.assertRaises(control_module.ControlError):
            self.control.configure({**self.limits, "max_concurrent": 10})

    def test_entity_boundary_and_bool_amount_are_rejected(self):
        with self.assertRaises(control_module.ControlError):
            control_module.TaskControl(self.root, "task", "bastion")
        with self.assertRaises(control_module.ControlError):
            self.reserve(True)

    def test_corrupt_state_cannot_be_reinitialized_as_an_unused_budget(self):
        self.control.path.write_text("{broken")
        with self.assertRaises(ValueError):
            self.control.configure(self.limits)

    def test_negative_persisted_measurement_cannot_create_more_allowance(self):
        token = self.reserve(100)
        self.control.finish(token, 100, "measured provider cost")
        state = json.loads(self.control.path.read_text())
        state["reservations"][token]["actual_microusd"] = -100
        self.control.path.write_text(json.dumps(state))
        with self.assertRaises(control_module.ControlError):
            self.reserve(800)

    def test_work_retries_cannot_exhaust_the_reserved_verification_attempt(self):
        for _ in range(self.limits["max_attempts"] - 1):
            token = self.reserve(0)
            self.control.finish(token, 0, "local test operation: no provider call")
        with self.assertRaises(control_module.ControlError):
            self.reserve(0)
        review = self.reserve(0, purpose="verification")
        self.control.finish(review, 0, "local verification: no provider call")


if __name__ == "__main__":
    unittest.main()
