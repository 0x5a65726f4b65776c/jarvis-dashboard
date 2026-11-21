"""Evidence integrity, collection health, atomic publication, and stage recovery."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evidence_manifest_test", ROOT / "scripts/evidence_manifest.py")
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)
AT = "2026-11-21T12:00:00Z"


class EvidenceManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.put("capture/source.json", {"claim": "Current supported claim"})
        self.put("capture/other.json", {"items": []})
        self.plan = {"schema_version": 1, "entity": "atg", "task_key": "brief:today", "sources": [
            {"id": "technical", "path": "capture/source.json", "required": True,
             "max_age_seconds": 3600, "source_ref": "https://example.test/technical"},
            {"id": "policy", "path": "capture/other.json", "required": True,
             "max_age_seconds": 3600},
        ]}
        self.put("capture/collection-plan.json", self.plan)
        self.manifest = {"schema_version": 1, "entity": "atg", "task_key": "brief:today",
                         "created_at": AT, "collection_plan": self.ref("capture/collection-plan.json"),
                         "sources": [self.observation("technical", "capture/source.json"),
                                     self.observation("policy", "capture/other.json", state="empty", count=0)]}

    def put(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value) if not isinstance(value, str) else value)

    def ref(self, path):
        return {"path": path, "sha256": evidence.file_hash(self.root, path)}

    def observation(self, source_id, path, state="ok", count=1):
        return {"id": source_id, "entity": "atg", "path": path, "state": state,
                "attempted_at": AT, "observed_at": "2026-11-21T11:59:00Z",
                "expires_at": "2026-11-21T12:59:00Z", "result_count": count,
                "sha256": evidence.file_hash(self.root, path)}

    def save_manifest(self):
        self.put("capture/evidence-set.json", self.manifest)
        return self.ref("capture/evidence-set.json")

    def check(self, **kwargs):
        return evidence.check_manifest(self.root, self.manifest, "atg", at=AT, **kwargs)

    def test_successful_empty_is_distinct_from_failed_stale_unknown(self):
        self.assertEqual(["ok", "empty"], [row["state"] for row in self.check()["sources"]])
        for state in ("failed", "unknown"):
            changed = copy.deepcopy(self.manifest)
            changed["sources"][1].update(state=state, reason="Source could not be reached")
            report = evidence.check_manifest(self.root, changed, "atg", False, AT)
            self.assertFalse(report["complete"])
            self.assertEqual(state, report["sources"][1]["state"])
        expired = evidence.check_manifest(self.root, self.manifest, "atg", False,
                                          "2026-11-21T13:00:00Z")
        self.assertEqual("stale", expired["sources"][1]["state"])

    def test_required_source_cannot_be_omitted_or_downgraded(self):
        self.manifest["sources"].pop()
        self.assertEqual("unknown", self.check(require_complete=False)["sources"][1]["state"])
        with self.assertRaises(evidence.IncompleteEvidence):
            self.check()
        self.manifest["sources"][0]["required"] = False
        with self.assertRaisesRegex(ValueError, "required status"):
            self.check(require_complete=False)

    def test_source_plan_and_task_are_bound(self):
        self.manifest["task_key"] = "different-task"
        with self.assertRaisesRegex(ValueError, "another task"):
            self.check()
        self.manifest["task_key"] = "brief:today"
        self.plan["sources"].pop()
        self.put("capture/collection-plan.json", self.plan)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.check()

    def test_correction_invalidates_derived_evidence_without_changing_source(self):
        self.put("capture/source.json", {"claim": "Corrected current claim"})
        report = self.check(require_complete=False)
        self.assertEqual("source_changed", report["issues"][0]["code"])
        self.assertEqual("Corrected current claim", json.loads((self.root / "capture/source.json").read_text())["claim"])
        with self.assertRaises(evidence.IncompleteEvidence):
            self.check()

    def test_cross_entity_and_path_escape_are_rejected(self):
        self.manifest["sources"][0]["entity"] = "bastion"
        with self.assertRaisesRegex(ValueError, "another entity"):
            self.check()
        for path in ("../other/private.json", "/tmp/private.json"):
            with self.assertRaises(ValueError):
                evidence.local(self.root, path)
        with tempfile.TemporaryDirectory() as outside:
            (self.root / "outside").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                evidence.local(self.root, "outside/private.json")

    def test_manifest_and_observations_cannot_widen_scope(self):
        self.manifest["sources"][0]["path"] = "capture/other.json"
        with self.assertRaisesRegex(ValueError, "planned canonical path"):
            self.check()
        self.manifest["sources"][0]["path"] = "capture/source.json"
        self.manifest["sources"].append(self.observation("unplanned", "capture/source.json"))
        with self.assertRaisesRegex(ValueError, "Unplanned"):
            self.check()

    def test_freshness_cannot_exceed_plan_or_future_date(self):
        self.manifest["sources"][0]["expires_at"] = "2027-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "freshness window"):
            self.check()
        self.manifest["sources"][0]["expires_at"] = "2026-11-21T12:59:00Z"
        self.manifest["sources"][0]["observed_at"] = "2026-11-21T12:01:00Z"
        with self.assertRaises(ValueError):
            self.check()

    def test_wrong_empty_count_and_malformed_json_are_rejected(self):
        self.manifest["sources"][1]["result_count"] = 1
        with self.assertRaises(ValueError):
            self.check()
        self.put("capture/bad.json", '{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            evidence.check_reference(self.root, "capture/bad.json", "atg", at=AT)

    def test_only_allowlisted_source_fields_reach_report(self):
        self.manifest["sources"][0]["private_internal_field"] = "must not leave the record"
        report = self.check()
        self.assertNotIn("private_internal_field", report["sources"][0])
        self.assertNotIn("must not leave", json.dumps(report))

    def test_temporary_source_storage_error_is_retryable_not_empty(self):
        original = Path.read_bytes
        def fail(path):
            if path.name == "source.json":
                raise PermissionError("temporary storage failure")
            return original(path)
        with patch.object(Path, "read_bytes", fail):
            with self.assertRaises(evidence.EvidenceUnavailable):
                self.check(require_complete=False)

    def test_failed_publication_preserves_previous_complete_cohort(self):
        reference = self.save_manifest()
        evidence.publish_manifest(self.root, reference, "capture/current.json", "atg", AT)
        previous = (self.root / "capture/current.json").read_bytes()
        self.manifest["sources"][1].update(state="failed", reason="Collector timed out")
        reference = self.save_manifest()
        with self.assertRaises(evidence.IncompleteEvidence):
            evidence.publish_manifest(self.root, reference, "capture/current.json", "atg", AT)
        self.assertEqual(previous, (self.root / "capture/current.json").read_bytes())

    def test_freeze_preserves_dates_and_cannot_overwrite_prior_evidence(self):
        specification = copy.deepcopy(self.manifest)
        specification["collection_plan"] = "capture/collection-plan.json"
        for row in specification["sources"]:
            row.pop("sha256")
        reference = evidence.freeze_manifest(self.root, specification, "capture/frozen.json", "atg", AT)
        report = evidence.check_reference(self.root, reference, "atg", at=AT)
        self.assertTrue(report["complete"])
        self.assertEqual(specification["sources"][0]["observed_at"], report["sources"][0]["observed_at"])
        with self.assertRaises(FileExistsError):
            evidence.freeze_manifest(self.root, specification, "capture/frozen.json", "atg", AT)

    def make_stages(self):
        manifest = self.save_manifest()
        self.put("capture/analysis.txt", "Completed supported analysis")
        analysis = evidence.write_checkpoint(self.root, "capture/analysis-stage.json", "atg", "brief:today",
                                             "analysis", manifest, ["capture/analysis.txt"], at=AT)
        self.put("capture/report.txt", "Rendered supported report")
        report = evidence.write_checkpoint(self.root, "capture/render-stage.json", "atg", "brief:today",
                                           "render", manifest, ["capture/report.txt"], [analysis], AT)
        return analysis, report

    def test_render_failure_preserves_valid_analysis(self):
        analysis, report = self.make_stages()
        (self.root / "capture/report.txt").unlink()
        self.assertEqual("stale", evidence.inspect_checkpoint(self.root, report, "atg", "brief:today", AT)["state"])
        self.assertEqual("reusable", evidence.inspect_checkpoint(self.root, analysis, "atg", "brief:today", AT)["state"])

    def test_changed_output_cannot_claim_stage_completion(self):
        analysis, report = self.make_stages()
        self.put("capture/analysis.txt", "Changed unsupported analysis")
        self.assertEqual("stale", evidence.inspect_checkpoint(self.root, analysis, "atg", "brief:today", AT)["state"])
        self.assertEqual("stale", evidence.inspect_checkpoint(self.root, report, "atg", "brief:today", AT)["state"])

    def test_changed_source_invalidates_dependent_stages(self):
        analysis, report = self.make_stages()
        self.put("capture/source.json", {"claim": "Corrected claim"})
        for reference in (analysis, report):
            self.assertEqual("stale", evidence.inspect_checkpoint(self.root, reference, "atg", "brief:today", AT)["state"])

    def test_checkpoint_absence_corruption_and_transient_error_are_distinct(self):
        self.assertEqual("absent", evidence.inspect_checkpoint(self.root, "capture/missing.json", "atg", "brief:today", AT)["state"])
        self.put("capture/broken.json", "not json")
        self.assertEqual("corrupt", evidence.inspect_checkpoint(self.root, "capture/broken.json", "atg", "brief:today", AT)["state"])
        with patch.object(Path, "read_bytes", side_effect=OSError("transient")):
            with self.assertRaises(evidence.EvidenceUnavailable):
                evidence.inspect_checkpoint(self.root, "capture/broken.json", "atg", "brief:today", AT)

    def test_checkpoint_is_bound_to_entity_task_and_nonempty_outputs(self):
        analysis, _ = self.make_stages()
        for entity, task in (("bastion", "brief:today"), ("atg", "another-task")):
            self.assertEqual("corrupt", evidence.inspect_checkpoint(self.root, analysis, entity, task, AT)["state"])
        with self.assertRaises(ValueError):
            evidence.write_checkpoint(self.root, "capture/empty-stage.json", "atg", "brief:today",
                                      "analysis", self.ref("capture/evidence-set.json"), [], at=AT)

    def test_failed_source_requires_visible_limitation_in_partial_artifact(self):
        self.manifest["sources"][1].update(state="failed", reason="Collector timed out")
        reference = self.save_manifest()
        artifact = {"task_key": "brief:today", "evidence_manifest": reference, "coverage_status": "complete"}
        with self.assertRaises(evidence.IncompleteEvidence):
            evidence.check_coverage(self.root, artifact, "atg", "No change found.", AT)
        artifact["coverage_status"] = "incomplete"
        artifact["coverage_limitations"] = [{"source_id": "policy", "text": "Policy source unavailable; this review has incomplete coverage."}]
        with self.assertRaisesRegex(ValueError, "rendered artifact"):
            evidence.check_coverage(self.root, artifact, "atg", "No change found.", AT)
        visible = "<p>Policy source unavailable; this review has incomplete coverage.</p>"
        self.assertFalse(evidence.check_coverage(self.root, artifact, "atg", visible, AT)["complete"])

    def test_forged_or_missing_coverage_manifest_is_rejected(self):
        with self.assertRaises(ValueError):
            evidence.check_coverage(self.root, {"coverage_status": "complete"}, "atg", "All good", AT)
        self.assertIsNone(evidence.check_coverage(self.root, {"title": "Historical artifact"}, "atg", "", AT))

    def test_boolean_freshness_and_duplicate_plan_ids_are_rejected(self):
        self.plan["sources"][0]["max_age_seconds"] = True
        self.put("capture/collection-plan.json", self.plan)
        self.manifest["collection_plan"] = self.ref("capture/collection-plan.json")
        with self.assertRaisesRegex(ValueError, "positive freshness"):
            self.check()
        self.plan["sources"][0]["max_age_seconds"] = 3600
        self.plan["sources"].append(copy.deepcopy(self.plan["sources"][0]))
        self.put("capture/collection-plan.json", self.plan)
        self.manifest["collection_plan"] = self.ref("capture/collection-plan.json")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.check()

    @unittest.skipUnless((ROOT / "brief/validate_brief.py").is_file(), "ATG brief integration only")
    def test_existing_brief_validator_enforces_manifest_coverage(self):
        spec = importlib.util.spec_from_file_location("legacy_brief_evidence_test", ROOT / "brief/validate_brief.py")
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        validator.HERE = str(self.root / "brief")
        self.put("scripts/evidence_manifest.py", (ROOT / "scripts/evidence_manifest.py").read_text())
        self.put("config/maturation.json", {"entity": "atg"})
        now = datetime.now(timezone.utc)
        self.manifest["created_at"] = now.isoformat()
        for row in self.manifest["sources"]:
            row.update(attempted_at=now.isoformat(), observed_at=(now - timedelta(seconds=30)).isoformat(),
                       expires_at=(now + timedelta(minutes=10)).isoformat())
        artifact = json.loads((ROOT / "brief/fixtures/brief-2026-09-15.json").read_text())
        artifact.update(evidence_manifest=self.save_manifest(), coverage_status="complete")
        self.assertFalse(validator.validate(artifact).fails)
        self.manifest["sources"][1].update(state="failed", reason="Source unreachable")
        artifact["evidence_manifest"] = self.save_manifest()
        findings = validator.validate(artifact).fails
        self.assertTrue(any(where == "evidence_manifest" and "complete coverage" in message for where, message in findings))


if __name__ == "__main__":
    unittest.main()
