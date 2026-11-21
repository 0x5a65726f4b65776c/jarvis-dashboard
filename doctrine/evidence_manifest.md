# Evidence continuity and stage recovery

Use `scripts/evidence_manifest.py` for a recurring sweep, reused claim, or multistage
deliverable whose inputs can change. It records dependencies of existing canonical
records; it does not create another knowledge register or collect sources itself.
The trusted entity is `config/maturation.json`. Never take it from a worker packet.

## Freeze the collection scope before collecting

The owning orchestrator writes a task-specific collection plan from the request and
the existing source policy. The final reviewer compares that scope with the original
task: a perfectly preserved but incomplete plan is still an incomplete answer. A plan
is evidence of intended coverage, never permission to read a new entity or act.

```json
{
  "schema_version": 1,
  "entity": "atg",
  "task_key": "existing-task-key",
  "sources": [
    {
      "id": "approved-claims",
      "path": "capture/task-inputs/claims.json",
      "required": true,
      "max_age_seconds": 86400,
      "source_ref": "the-existing-authoritative-record"
    }
  ]
}
```

Use the relevant entity and actual paths. `max_age_seconds` is a decision-specific
freshness rule, not a universal default. Do not extend it to make a stale source pass.
Keep source and plan revisions in the existing repository history, or use immutable
versioned snapshot paths. Keep the live canonical path in the plan when an accepted
correction must invalidate all work derived from that local record.

## Record collection outcomes faithfully

Write a manifest specification referencing the saved plan. The `freeze` command
fills missing SHA256 values from the files that actually exist. It never supplies
collection success, dates, source versions, or business authority.

```json
{
  "schema_version": 1,
  "entity": "atg",
  "task_key": "existing-task-key",
  "collection_plan": ".claude/state/task/collection-plan.json",
  "sources": [
    {
      "id": "approved-claims",
      "entity": "atg",
      "path": "capture/task-inputs/claims.json",
      "state": "ok",
      "attempted_at": "2026-11-21T12:00:00Z",
      "observed_at": "2026-11-21T12:00:00Z",
      "expires_at": "2026-11-22T12:00:00Z",
      "result_count": 1,
      "version": "the-version-returned-by-the-authoritative-source"
    }
  ]
}
```

Replace all example dates and values with observations from the actual run. For
`empty`, `result_count` must be zero and a successful collection receipt must exist
at the planned path. For `failed` or `unknown`, record `attempted_at` and `reason`;
`last_success_at` may preserve the previous successful retrieval time. An unattempted
planned source may be omitted from observations: the validator emits `unknown` and
refuses complete coverage when that source is required. Unknown is never empty.

```sh
python3 scripts/evidence_manifest.py freeze \
  .claude/state/task/evidence-spec.json .claude/state/task/evidence-set.json
python3 scripts/evidence_manifest.py check .claude/state/task/evidence-set.json
```

The frozen manifest contains the plan's hash and source hashes. Corrections to a
planned canonical file, a substituted plan, missing evidence, or expired observations
invalidate reuse. Every lookup must validate before reading the dependent summary or
claim. A derived artifact is not current merely because it exists.

Remote changes are detectable only after the existing collector refreshes its dated
local receipt. This mechanism does not update an external search index or discover
unobserved remote corrections. Freshness windows bound that lag; the collector and
reviewer remain responsible for source coverage and factual interpretation.

## Deliver coverage and publish coherently

Include the frozen reference in the existing delivery packet:

```json
{"evidence_manifest": {"path": ".claude/state/task/evidence-set.json", "sha256": "actual-sha256"}}
```

The final gate binds the manifest, collection plan, and underlying source versions.
The manifest task must equal the delivery task. A complete result needs every required
source to be current and successfully collected. Partial work lists the unavailable
sources, consequence, owner, and next action in the normal unresolved-work fields.
`check --allow-incomplete` returns the exact coverage states for that disclosure; it
does not establish complete coverage.

Brief artifacts using this manifest also declare `coverage_status: complete|incomplete`.
For each reported coverage issue, provide a `coverage_limitations` entry with
`source_id` and `text`; that exact limitation text must appear in a rendered content
field, such as the existing assumptions section. Metadata that never reaches the
reader does not disclose a coverage gap. Historical artifacts without any of these
fields retain their existing validator; current delivery profiles determine when a
manifest is mandatory.

Where recurring analysis needs a stable published set, advance its pointer only after
all required inputs validate:

```sh
python3 scripts/evidence_manifest.py publish \
  .claude/state/task/evidence-set.json capture/current-evidence-set.json
```

Publication uses one atomic file replacement. A failed candidate leaves the previous
pointer unchanged. Reading that pointer still requires checking its referenced manifest
and freshness; the previous set must not silently appear current. No input is required
to share an identical observation date; each must satisfy its own planned window.

## Resume only the failed stage

After a stage completes, bind its actual output bytes and source manifest. Add each
earlier stage whose output was consumed as `--parent`. Omitted parent dependencies are
a review failure. Checkpoints are immutable; create a new path for a new valid result.

```sh
python3 scripts/evidence_manifest.py checkpoint \
  .claude/state/task/analysis-stage.json --task-key existing-task-key \
  --stage analysis --manifest .claude/state/task/evidence-set.json \
  --output .claude/state/task/analysis.md
python3 scripts/evidence_manifest.py checkpoint \
  .claude/state/task/render-stage.json --task-key existing-task-key \
  --stage render --manifest .claude/state/task/evidence-set.json \
  --output .claude/state/task/report.pdf --parent .claude/state/task/analysis-stage.json
python3 scripts/evidence_manifest.py resume \
  .claude/state/task/analysis-stage.json --task-key existing-task-key
```

`reusable` proves matching declared inputs, output hashes, parent checkpoints, task and
entity. A missing rendered file can leave analysis reusable; a changed source invalidates
both. `absent` means the checkpoint genuinely does not exist. `corrupt` means invalid
checkpoint content. `stale` means a dependency or output requires revalidation. A temporary
storage failure raises `EvidenceUnavailable` (CLI exit 3): retry the read and preserve
the checkpoint. Do not interpret that failure as lost work and buy the analysis again.

These are workflow-integrity checks, not process isolation or proof against an actor
who can rewrite the repository. They grant no sending, spending, signature, or publication
authority. A stage checkpoint never replaces final independent review or destination
verification.
