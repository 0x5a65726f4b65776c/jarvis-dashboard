"""Exercise final delivery against missing, changed, and replayed evidence."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


gate_module = module("delivery_gate_under_test", ROOT / "scripts/agent_gate.py")
fidelity = module("delivery_fidelity_under_test", ROOT / "scripts/check_agent_fidelity.py")


class DeliveryGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in fidelity.SHARED_PATHS:
            source = ROOT / relative
            self.put(relative, source.read_text() if source.exists() else "Fixture standing policy\n")
        self.put(".claude/agents/hermes-reviewer.md", (ROOT / ".claude/agents/hermes-reviewer.md").read_text())
        self.put("evidence/receipt.json", '{"result":"requested value verified"}\n')
        self.refresh()
        self.gate = gate_module.Gate(self.root, "runtime-session-one")
        self.reply = "The requested value is verified."

    def put(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def refresh(self):
        fidelity.write_manifest(self.root, "test-fixture", None, [])

    def start(self):
        self.gate.hook({"hook_event_name": "SessionStart", "source": "startup"})
        self.gate.hook({"hook_event_name": "UserPromptSubmit", "prompt": "Verify the requested value."})

    def packet(self):
        return {
            "schema_version": 1, "task_key": "test-task", "request": "Verify the requested value.",
            "summary": "Value verified", "outcome": "complete",
            "evidence": [{"path": "evidence/receipt.json"}],
            "acceptance": [{"criterion": "Requested value verified", "met": True,
                            "evidence": ["evidence/receipt.json"]}],
            "claims": [{"kind": "fact", "text": "The requested value is verified",
                        "evidence": ["evidence/receipt.json"]}],
            "learning": {"disposition": "none", "reason": "No correction occurred", "lesson_ids": []},
            "unresolved": [],
        }

    def stage(self, packet=None):
        return self.gate.stage(packet if packet is not None else self.packet(), self.reply)

    def review(self, release, role="orchestrator", **changes):
        row = {"release_id": release["release_id"], "role": role, "verdict": "pass",
               "checks": {key: True for key in gate_module.CHECKS},
               "basis": "Read the exact reply and original receipt; value and task match.",
               "findings": []}
        row.update(changes)
        return row

    def hermes(self, release, *, agent_id="reviewer-one", start=True, **changes):
        if start:
            self.gate.hook({"hook_event_name": "SubagentStart", "agent_type": "hermes-reviewer",
                            "agent_id": agent_id})
        return self.gate.hook({"hook_event_name": "SubagentStop", "agent_type": "hermes-reviewer",
                               "agent_id": agent_id,
                               "last_assistant_message": json.dumps(self.review(release, "hermes", **changes))})

    def cleared(self):
        self.start()
        release = self.stage()
        self.gate.owner_review(self.review(release))
        self.assertEqual({}, self.hermes(release))
        return release

    def stop(self, reply=None):
        return self.gate.hook({"hook_event_name": "Stop", "last_assistant_message": self.reply if reply is None else reply})

    def test_two_independent_reviews_clear_only_the_exact_reply(self):
        release = self.cleared()
        self.assertEqual(release["release_id"], self.gate.validate(self.gate.read())["release_id"])
        self.assertEqual({}, self.stop())
        self.assertIs(self.gate.read()["delivered"], True)

    def test_staging_without_any_runtime_turn_is_rejected(self):
        with self.assertRaises(ValueError):
            self.stage()

    def test_manual_begin_does_not_substitute_for_runtime_context_receipt(self):
        self.gate.begin()
        with self.assertRaises(ValueError):
            self.stage()

    def test_missing_orchestrator_review_blocks(self):
        self.start()
        release = self.stage()
        self.hermes(release)
        self.assertEqual("block", self.stop().get("decision"))

    def test_missing_hermes_review_blocks(self):
        self.start()
        release = self.stage()
        self.gate.owner_review(self.review(release))
        self.assertEqual("block", self.stop().get("decision"))

    def test_hermes_name_without_runtime_dispatch_is_rejected(self):
        self.start()
        release = self.stage()
        self.assertEqual("block", self.hermes(release, start=False).get("decision"))
        self.assertNotIn("hermes", self.gate.read().get("reviews", {}))

    def test_other_agent_dispatch_cannot_be_claimed_as_hermes(self):
        self.start()
        release = self.stage()
        self.gate.hook({"hook_event_name": "SubagentStart", "agent_type": "ordinary-worker", "agent_id": "worker"})
        self.assertEqual("block", self.hermes(release, start=False, agent_id="worker").get("decision"))

    def test_changed_final_reply_requires_new_review(self):
        self.cleared()
        self.assertEqual("block", self.stop("A different result was verified.").get("decision"))

    def test_changed_evidence_requires_new_review(self):
        self.cleared()
        self.put("evidence/receipt.json", '{"result":"not verified"}\n')
        self.assertEqual("block", self.stop().get("decision"))

    def test_changed_instruction_bundle_requires_new_review_even_after_refresh(self):
        self.cleared()
        self.put("CLAUDE.md", "Updated standing rule\n")
        self.refresh()
        self.assertEqual("block", self.stop().get("decision"))

    def test_changed_context_receipt_is_rejected(self):
        self.cleared()
        reference = self.gate.read()["context_receipt"]
        self.put(reference["path"], '{"supplied":"different context"}\n')
        self.assertEqual("block", self.stop().get("decision"))

    def test_instruction_change_requires_explicit_context_refresh_before_restaging(self):
        self.start()
        self.put("CLAUDE.md", "A newly authorized standing rule\n")
        self.refresh()
        with self.assertRaises(ValueError):
            self.stage()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = gate_module.main(["--root", str(self.root), "refresh-context", "--session", self.gate.session])
        self.assertEqual(0, result)
        self.stage()

    def test_artifact_cli_clears_only_a_file_in_the_reviewed_evidence(self):
        self.cleared()
        self.put("evidence/unreviewed.txt", "Not part of the reviewed packet\n")
        prefix = ["--root", str(self.root), "check", "--session", self.gate.session, "--artifact"]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(0, gate_module.main(prefix + ["evidence/receipt.json"]))
            self.assertEqual(1, gate_module.main(prefix + ["evidence/unreviewed.txt"]))

    def test_restaging_invalidates_previous_reviews(self):
        old = self.cleared()
        new = self.stage()
        self.assertNotEqual(old["release_id"], new["release_id"])
        self.assertEqual("block", self.stop().get("decision"))
        with self.assertRaises(ValueError):
            self.gate.owner_review(self.review(old))

    def test_next_prompt_cannot_reuse_an_old_review(self):
        old = self.cleared()
        self.gate.hook({"hook_event_name": "UserPromptSubmit", "prompt": "A different task"})
        self.stage()
        self.assertEqual("block", self.hermes(old, start=False).get("decision"))
        self.assertEqual("block", self.stop().get("decision"))

    def test_a_revision_verdict_never_clears_delivery(self):
        self.start()
        release = self.stage()
        self.gate.owner_review(self.review(release))
        self.hermes(release, verdict="revise", findings=[{"severity": "block", "finding": "Evidence incomplete"}])
        self.assertEqual("block", self.stop().get("decision"))

    def test_undisclosed_material_finding_cannot_pass(self):
        self.start()
        release = self.stage()
        with self.assertRaises(ValueError):
            self.gate.owner_review(self.review(release, findings=[{"severity": "material", "finding": "Uncertainty changes the decision"}]))

    def test_blocking_finding_cannot_be_rubber_stamped(self):
        self.start()
        release = self.stage()
        with self.assertRaises(ValueError):
            self.gate.owner_review(self.review(release, findings=[{"severity": "block", "finding": "Source contradicts claim"}]))

    def test_unknown_finding_severity_is_rejected(self):
        self.start()
        release = self.stage()
        with self.assertRaises(ValueError):
            self.gate.owner_review(self.review(release, findings=[{"severity": "anything", "finding": "Source contradicts claim"}]))

    def test_failed_review_dimension_cannot_pass(self):
        self.start()
        release = self.stage()
        checks = {key: True for key in gate_module.CHECKS}
        checks["completion"] = False
        with self.assertRaises(ValueError):
            self.gate.owner_review(self.review(release, checks=checks))

    def test_malformed_packet_fails_cleanly(self):
        self.start()
        with self.assertRaises(ValueError):
            self.stage([])

    def test_malformed_hermes_json_is_blocked(self):
        self.start()
        self.stage()
        result = self.gate.hook({"hook_event_name": "SubagentStop", "agent_type": "hermes-reviewer",
                                 "agent_id": "missing", "last_assistant_message": "not json"})
        self.assertEqual("block", result.get("decision"))

    def test_complete_requires_destination_evidence(self):
        self.start()
        packet = self.packet()
        packet["evidence"] = []
        with self.assertRaises(ValueError):
            self.stage(packet)

    def test_partial_requires_explicit_blocker_owner_and_next_action(self):
        self.start()
        packet = self.packet()
        packet["outcome"] = "partial"
        with self.assertRaises(ValueError):
            self.stage(packet)

    def test_unknown_learning_disposition_is_rejected(self):
        self.start()
        packet = self.packet()
        packet["learning"]["disposition"] = "learned-in-memory"
        with self.assertRaises(ValueError):
            self.stage(packet)

    def test_lesson_id_must_exist_before_final_delivery(self):
        self.start()
        self.put("capture/lessons.jsonl", "")
        packet = self.packet()
        packet["learning"] = {"disposition": "candidate", "reason": "Observed failure", "lesson_ids": ["L-missing"]}
        with self.assertRaises(ValueError):
            self.stage(packet)

    def test_legacy_applied_flag_does_not_establish_verified_learning(self):
        self.start()
        self.put("capture/lessons.jsonl", json.dumps({"id": "L-legacy", "state": "applied", "rule": "Read the evidence"}) + "\n")
        packet = self.packet()
        packet["learning"] = {"disposition": "applied", "reason": "Old flag is not evidence", "lesson_ids": ["L-legacy"]}
        with self.assertRaises(ValueError):
            self.stage(packet)

    def test_bounded_recovery_never_labels_a_review_failure_complete(self):
        self.start()
        self.assertEqual("block", self.stop().get("decision"))
        self.assertEqual("block", self.stop().get("decision"))
        self.assertEqual({}, self.stop(gate_module.BLOCKED_REPLY))
        self.assertEqual("blocked", self.gate.read()["delivered"])
        self.assertEqual("block", self.stop("Everything is done.").get("decision"))

    def test_required_native_worker_must_return_before_review_and_remain_unchanged(self):
        self.put(".claude/agents/native.md", "# Native scoped orchestrator\n")
        fidelity.write_manifest(self.root, "test-fixture", None, [{"name": "native", "kind": "orchestrator",
            "sources": [".claude/agents/native.md"], "skills": []}])
        manifest_path = self.root / "config/agent_fidelity.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["required_dispatches"] = ["native"]
        manifest_path.write_text(json.dumps(manifest))
        self.start()
        with self.assertRaisesRegex(ValueError, "Required native agent"):
            self.stage()
        self.gate.hook({"hook_event_name": "SubagentStart", "agent_id": "native-one", "agent_type": "native"})
        with self.assertRaisesRegex(ValueError, "Required native agent"):
            self.stage()
        self.gate.hook({"hook_event_name": "PostToolUse", "tool_name": "SubagentHandback", "agent_id": "native-one",
            "agent_type": "native", "tool_input": {"message": "Evidence-backed native result"}})
        self.gate.hook({"hook_event_name": "SubagentStop", "agent_id": "native-one", "agent_type": "native",
            "last_assistant_message": ""})
        release = self.stage()
        self.gate.owner_review(self.review(release))
        self.hermes(release)
        self.assertEqual({}, self.stop())
        self.gate.hook({"hook_event_name": "SubagentStart", "agent_id": "native-two", "agent_type": "native"})
        self.assertEqual("block", self.stop().get("decision"))
        with self.assertRaisesRegex(ValueError, "Required native agent"):
            self.stage()
        self.gate.hook({"hook_event_name": "SubagentStop", "agent_id": "native-two", "agent_type": "native",
            "last_assistant_message": "A materially different result"})
        self.assertEqual("block", self.stop().get("decision"))

    def test_transport_requires_current_prompt_final_stop_and_exact_reply(self):
        from datetime import datetime, timezone, timedelta
        launched = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.cleared()
        base = ["--root", str(self.root), "check", "--session", self.gate.session, "--transport",
                "--reply-sha256", gate_module.reply_hash(self.reply),
                "--prompt-sha256", gate_module.digest(b"Verify the requested value."), "--not-before", launched]
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(1, gate_module.main(base))
        self.stop()
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(0, gate_module.main(base))
            wrong_reply = list(base)
            wrong_reply[wrong_reply.index("--reply-sha256") + 1] = "0" * 64
            self.assertEqual(1, gate_module.main(wrong_reply))
            wrong_prompt = list(base)
            wrong_prompt[wrong_prompt.index("--prompt-sha256") + 1] = "0" * 64
            self.assertEqual(1, gate_module.main(wrong_prompt))
            replay = list(base)
            replay[-1] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
            self.assertEqual(1, gate_module.main(replay))


if __name__ == "__main__":
    unittest.main()
