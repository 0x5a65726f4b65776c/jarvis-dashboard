"""Synthetic-event checks for the probe protocol, never live hook attestation."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = load("probe_gate_fixtures", ROOT / "tests/test_agent_gate.py")
probe = load("runtime_probe_under_test", ROOT / "scripts/runtime_probe.py")


class RuntimeProbeTests(unittest.TestCase):
    setUp = fixtures.DeliveryGateTests.setUp
    put = fixtures.DeliveryGateTests.put
    refresh = fixtures.DeliveryGateTests.refresh
    start = fixtures.DeliveryGateTests.start

    def prepare(self):
        self.start()
        self.prepared = probe.prepare(self.gate, "offline-fixture", "fixture-1", mode="self-test")
        self.transcript = self.root / "fixture-transcript.jsonl"
        self.ident = self.prepared["probe_id"]
        self.paths = dict(zip(("allow", "deny"), self.prepared["read_paths_in_order"]))
        return self.prepared

    def event(self, kind, event="PreToolUse", **extra):
        payload = {"hook_event_name": event, "session_id": self.gate.session, "tool_name": "Read",
                   "tool_use_id": kind + "-read", "tool_input": {"file_path": self.paths[kind]},
                   "transcript_path": str(self.transcript), **extra}
        # Exercise the actual JSON hook entry point, but label the fixture honestly.
        import sys
        from unittest.mock import patch
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), redirect_stdout(output):
            self.assertEqual(0, fixtures.gate_module.main(["--root", str(self.root), "hook"]))
        return json.loads(output.getvalue())

    def transcript_rows(self, *, denial=True, session=None):
        rows = []
        for kind in ("allow", "deny"):
            rows.append({"type": "assistant", "sessionId": session or self.gate.session,
                         "message": {"role": "assistant", "content": [{"type": "tool_use", "id": kind + "-read",
                            "name": "Read", "input": {"file_path": self.paths[kind]}}]}})
            content = ("agent-gate-allowed-canary:" + self.ident if kind == "allow" else
                       "Runtime enforcement canary " + self.ident + ": this harmless read must be denied.")
            rows.append({"type": "user", "sessionId": session or self.gate.session,
                         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": kind + "-read",
                            "is_error": bool(kind == "deny" and denial), "content": content}]}})
        return rows

    def write_transcript(self, **options):
        self.transcript.write_text("".join(json.dumps(row) + "\n" for row in self.transcript_rows(**options)))

    def observe_fixture(self):
        self.assertEqual({}, self.event("allow"))
        self.assertEqual({}, self.event("allow", "PostToolUse", tool_response={"text": "agent-gate-allowed-canary:" + self.ident}))
        result = self.event("deny")
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        self.assertIn(self.ident, result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_files_and_context_alone_do_not_attest_runtime_enforcement(self):
        self.prepare()
        receipt = probe.status(self.gate)
        self.assertEqual("unverified", receipt["status"])
        self.assertFalse(receipt["full_runtime_attestation"])
        self.assertTrue(receipt["gaps"])

    def test_configured_hooks_have_a_positive_and_negative_canary_path(self):
        settings = json.loads((ROOT / ".claude/settings.json").read_text())
        for event in ("PreToolUse", "PostToolUse"):
            import re
            self.assertTrue(any(re.fullmatch(block.get("matcher", ".*"), "Read")
                                and any("agent_gate.py hook" in hook.get("command", "") for hook in block["hooks"])
                                for block in settings["hooks"][event]))

    def test_ordinary_reads_do_not_need_a_probe_and_are_never_granted_extra_permission(self):
        self.start()
        event = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_use_id": "normal",
                 "tool_input": {"file_path": str(self.root / "evidence/receipt.json")}}
        self.assertEqual({}, self.gate.hook(event))
        probe.prepare(self.gate, "offline-fixture", "fixture-1", mode="self-test")
        self.assertEqual({}, self.gate.hook(event))

    def test_hook_decisions_without_runtime_tool_results_are_unverified(self):
        self.prepare()
        self.observe_fixture()
        self.assertEqual("unverified", probe.status(self.gate)["status"])

    def test_correlated_fixture_results_are_only_an_offline_self_test(self):
        self.prepare()
        self.observe_fixture()
        self.write_transcript()
        receipt = probe.status(self.gate)
        self.assertEqual("self_test_passed", receipt["status"])
        self.assertFalse(receipt["full_runtime_attestation"])
        self.assertIn("no live runtime", receipt["scope"])
        self.assertEqual({"allow", "deny"}, set(receipt["transcript_evidence"]["matched"]))

    def test_a_runtime_that_executes_the_denied_canary_fails(self):
        self.prepare()
        self.observe_fixture()
        self.write_transcript(denial=False)
        self.assertEqual("failed", probe.status(self.gate)["status"])

    def test_denied_read_post_event_cannot_be_hidden_by_a_transcript_error(self):
        self.prepare()
        self.observe_fixture()
        self.event("deny", "PostToolUse", tool_response="Read executed")
        self.write_transcript()
        self.assertEqual("failed", probe.status(self.gate)["status"])

    def test_another_session_transcript_cannot_attest_this_session(self):
        self.prepare()
        self.observe_fixture()
        self.write_transcript(session="another-session")
        self.assertEqual("unverified", probe.status(self.gate)["status"])

    def test_expired_probe_requires_a_fresh_run(self):
        self.prepare()
        self.observe_fixture()
        self.write_transcript()
        path = self.gate.directory / "probes" / self.ident / "probe.json"
        record = json.loads(path.read_text())
        record["expires_at"] = "2000-01-01T00:00:00+00:00"
        path.write_text(json.dumps(record))
        self.assertEqual("unverified", probe.status(self.gate)["status"])


if __name__ == "__main__":
    unittest.main()
