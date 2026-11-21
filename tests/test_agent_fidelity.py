"""Instruction changes must invalidate the release bundle until reconciled."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_agent_fidelity.py"
SPEC = importlib.util.spec_from_file_location("agent_fidelity_under_test", SCRIPT)
fidelity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fidelity)


class FidelityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in fidelity.SHARED_PATHS:
            self.put(relative, "reviewed shared instruction\n")
        self.put("scripts/check_agent_fidelity.py", SCRIPT.read_text())
        self.put("scripts/generate.py", """from pathlib import Path
import sys
sys.exit(0 if Path('sources/worker.md').read_bytes() ==
         Path('.claude/agents/worker.md').read_bytes() else 1)
""")
        self.worker = """---
name: worker
tools: Read, Grep, Glob
skills:
  - example-method
---
CLAUDE.md binds this seat.
Instruction contract: 2026-11-21.1. Read doctrine/agent_delivery.md.
Learning disposition goes to the owning orchestrator and Hermes.
"""
        self.put("sources/worker.md", self.worker)
        self.put(".claude/agents/worker.md", self.worker)
        self.put(".claude/agents/hermes-reviewer.md", "Independent reviewer\n")
        self.put("skills/example-method/SKILL.md", "---\nname: example-method\ndescription: Verify example evidence.\n---\nRead original evidence.\n")
        self.put("skills/example-method/references/method.md", "Use dated originals.\n")
        (self.root / ".claude/skills").symlink_to("../skills", target_is_directory=True)
        self.regenerate()

    def put(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def regenerate(self):
        fidelity.write_manifest(self.root, "fixture", "scripts/generate.py", [
            {"name": "worker", "sources": ["sources/worker.md"], "skills": ["example-method"]}
        ])

    def test_complete_matching_bundle_passes(self):
        self.assertEqual([], fidelity.check(self.root))

    def test_changed_skill_reference_blocks_old_bundle(self):
        self.put("skills/example-method/references/method.md", "Changed method.\n")
        self.assertTrue(any("changed instruction source: skills/" in e for e in fidelity.check(self.root)))

    def test_changed_shared_policy_blocks_old_bundle(self):
        self.put("CLAUDE.md", "Changed principal instruction.\n")
        self.assertIn("changed instruction source: CLAUDE.md", fidelity.check(self.root))

    def test_changed_execution_policy_invalidates_reviewed_bundle(self):
        self.put("config/maturation.json", '{"entity":"another-entity"}\n')
        self.assertIn("changed instruction source: config/maturation.json", fidelity.check(self.root))

    def test_commercial_rules_cannot_be_left_out_of_fidelity(self):
        self.put("config/opportunity_rules.json", '{"schema_version":1}\n')
        self.assertIn("untracked domain instruction source: config/opportunity_rules.json", fidelity.check(self.root))
        self.regenerate()
        self.assertEqual([], fidelity.check(self.root))

    def test_hash_update_alone_does_not_bless_a_stale_generated_agent(self):
        self.put(".claude/agents/worker.md", self.worker + "Hand-edited behavior.\n")
        self.regenerate()
        self.assertTrue(any("generated definitions differ from source" in e for e in fidelity.check(self.root)))

    def test_new_runtime_agent_must_be_in_reviewed_roster(self):
        self.put(".claude/agents/untracked.md", "Untracked worker\n")
        self.assertIn("untracked runtime agent: .claude/agents/untracked.md", fidelity.check(self.root))

    def test_missing_reviewer_blocks_release(self):
        (self.root / ".claude/agents/hermes-reviewer.md").unlink()
        self.regenerate()
        self.assertIn("missing independent final reviewer: hermes-reviewer", fidelity.check(self.root))

    def test_opt_out_of_standing_instructions_blocks_even_with_matching_hashes(self):
        opted_out = self.worker.replace("name: worker", "name: worker\nomitClaudeMd: true")
        self.put("sources/worker.md", opted_out)
        self.put(".claude/agents/worker.md", opted_out)
        self.regenerate()
        self.assertIn("agent opts out of standing rules: worker", fidelity.check(self.root))

    def test_manifest_cannot_reference_a_file_outside_repository(self):
        path = self.root / fidelity.MANIFEST
        manifest = json.loads(path.read_text())
        manifest["agents"][0]["sources"]["../outside.md"] = "0" * 64
        path.write_text(json.dumps(manifest))
        self.assertIn("path escapes the repository: ../outside.md", fidelity.check(self.root))

    def test_missing_shared_component_does_not_become_a_partial_pass(self):
        (self.root / "scripts/learning_evidence.py").unlink()
        self.regenerate()
        self.assertIn("untracked shared instruction source: scripts/learning_evidence.py", fidelity.check(self.root))

    def test_native_skill_discovery_cannot_point_to_a_different_bundle(self):
        (self.root / ".claude/skills").unlink()
        (self.root / ".claude/skills").mkdir()
        self.assertIn("native skill discovery must resolve .claude/skills to skills", fidelity.check(self.root))

    def test_manual_runtime_definition_can_have_its_own_canonical_path(self):
        self.put("agents/marlowe.md", "Runtime canonical instructions\n")
        (self.root / ".claude/agents/worker.md").unlink()
        fidelity.write_manifest(self.root, "fixture", None, [
            {"name": "marlowe", "kind": "orchestrator", "definition": "agents/marlowe.md",
             "sources": ["agents/marlowe.md"], "skills": []}
        ])
        self.assertEqual([], fidelity.check(self.root))


if __name__ == "__main__":
    unittest.main()
