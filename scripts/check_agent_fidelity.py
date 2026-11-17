#!/usr/bin/env python3
"""Verify the instruction bundle used by the local agent delivery gate.

The manifest identifies source and deployed content, not a model's subjective
quality. Regenerate definitions after an authorized source change; do not edit
hashes to bless an unreviewed runtime file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

CONTRACT_VERSION = "2026-11-17.1"
MANIFEST = "config/agent_fidelity.json"
SHARED_PATHS = (
    "CLAUDE.md",
    "AGENTS.md",
    "doctrine/agent_delivery.md",
    "scripts/agent_gate.py",
    "scripts/learning_evidence.py",
    ".claude/settings.json",
    "scripts/check_agent_fidelity.py",
)
SUPPLEMENTAL = {"hermes-reviewer": "reviewer"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("expected a repository-relative file path")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("path escapes the repository: " + relative)
    if not path.is_file():
        raise ValueError("missing file: " + relative)
    return path


def hashes(root: Path, paths: list[str]) -> dict[str, str]:
    return {p: digest(local_file(root, p)) for p in sorted(set(paths))}


def skill_files(root: Path, names: list[str]) -> list[str]:
    files = []
    for name in names:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise ValueError("invalid skill name: " + name)
        folder = root / "skills" / name
        local_file(root, f"skills/{name}/SKILL.md")
        files.extend(
            p.relative_to(root).as_posix()
            for p in folder.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )
    return files


def write_manifest(root: Path, repo: str, generator: str | None,
                   agents: list[dict]) -> None:
    """Called by each generator after writing its authoritative definitions."""
    rows = []
    for spec in agents:
        definition = spec.get("definition", f".claude/agents/{spec['name']}.md")
        rows.append({
            "name": spec["name"],
            "kind": spec.get("kind", "worker"),
            "definition": definition,
            "sha256": digest(local_file(root, definition)),
            "sources": hashes(root, ([generator] if generator else []) + spec["sources"]),
            "skills": hashes(root, skill_files(root, spec.get("skills", []))),
        })
    for name, kind in SUPPLEMENTAL.items():
        definition = f".claude/agents/{name}.md"
        if (root / definition).exists() and not any(r["name"] == name for r in rows):
            rows.append({
                "name": name, "kind": kind, "definition": definition,
                "sha256": digest(local_file(root, definition)),
                "sources": hashes(root, [definition]), "skills": {},
            })
    # During installation the delivery files can be written after the domain
    # definitions. The checker still fails until every required source exists
    # and the manifest is regenerated; a partial install is never release-ready.
    shared = [p for p in SHARED_PATHS if (root / p).is_file()]
    data = {
        "schema_version": 1, "contract_version": CONTRACT_VERSION,
        "repo": repo, "generator": generator,
        "shared_sources": hashes(root, shared),
        "agents": sorted(rows, key=lambda row: row["name"]),
    }
    path = root / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check(root: Path) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    try:
        data = json.loads(local_file(root, MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [str(exc)]
    if not isinstance(data, dict):
        return ["agent fidelity manifest must be an object"]
    if data.get("schema_version") != 1 or data.get("contract_version") != CONTRACT_VERSION:
        errors.append("unsupported instruction contract version")
    if not isinstance(data.get("repo"), str) or not data["repo"].strip():
        errors.append("manifest has no repository identity")

    def verify(mapping: object, label: str) -> None:
        if not isinstance(mapping, dict):
            errors.append(label + " must be a path/hash mapping")
            return
        for relative, expected in mapping.items():
            try:
                actual = digest(local_file(root, relative))
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    errors.append(f"invalid hash: {relative}")
                elif actual != expected:
                    errors.append(f"changed instruction source: {relative}")
            except (OSError, ValueError) as exc:
                errors.append(str(exc))

    shared = data.get("shared_sources", {})
    verify(shared, "shared_sources")
    if not isinstance(shared, dict):
        shared = {}
    for relative in SHARED_PATHS:
        if relative not in shared:
            errors.append("untracked shared instruction source: " + relative)

    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        return errors + ["manifest must contain an agent roster"]
    seen: set[str] = set()
    definitions: set[str] = set()
    for row in agents:
        if not isinstance(row, dict):
            errors.append("agent entry must be an object")
            continue
        name = row.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            errors.append("invalid agent name")
            continue
        if name in seen:
            errors.append("duplicate agent: " + name)
        seen.add(name)
        if row.get("kind") not in {"worker", "orchestrator", "reviewer"}:
            errors.append("invalid agent kind: " + name)
        definition = row.get("definition")
        if not isinstance(definition, str) or not definition.endswith(".md"):
            errors.append("invalid definition path: " + name)
            continue
        if definition.startswith(".claude/agents/") and definition != f".claude/agents/{name}.md":
            errors.append("definition path/name mismatch: " + name)
        definitions.add(definition)
        verify({definition: row.get("sha256")}, "definition")
        verify(row.get("sources"), name + " sources")
        verify(row.get("skills"), name + " skills")
        if not row.get("sources"):
            errors.append("no authoritative source for " + name)
        try:
            text = local_file(root, definition).read_text(encoding="utf-8")
            if re.search(r"(?m)^omitClaudeMd:\s*true\s*$", text):
                errors.append("agent opts out of standing rules: " + name)
            if row.get("kind") == "worker":
                for marker in ("CLAUDE.md", "doctrine/agent_delivery.md", "Learning disposition",
                               "owning orchestrator", "Hermes", CONTRACT_VERSION):
                    if marker not in text:
                        errors.append(f"missing delivery contract ({marker}): {name}")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    actual = {p.relative_to(root).as_posix() for p in (root / ".claude/agents").glob("*.md")}
    for relative in sorted(actual - definitions):
        errors.append("untracked runtime agent: " + relative)
    for name in SUPPLEMENTAL:
        if name not in seen:
            errors.append("missing independent final reviewer: " + name)
    required = data.get("required_dispatches", [])
    if not isinstance(required, list) or any(not isinstance(x, str) or x not in seen or x in SUPPLEMENTAL for x in required):
        errors.append("required_dispatches must name registered non-reviewer native agents")
    skill_link = root / ".claude/skills"
    uses_skills = any(isinstance(row, dict) and row.get("skills") for row in agents)
    if uses_skills and (not skill_link.is_dir() or skill_link.resolve() != (root / "skills").resolve()):
        errors.append("native skill discovery must resolve .claude/skills to skills")

    generator = data.get("generator")
    try:
        if generator is None:
            return sorted(set(errors))
        path = local_file(root, generator)
        if path.suffix != ".py" or not str(generator).startswith("scripts/"):
            raise ValueError("generator must be a local Python script under scripts/")
        result = subprocess.run([sys.executable, str(path), "--check"], cwd=root,
                                text=True, capture_output=True, timeout=30)
        if result.returncode:
            errors.append("generated definitions differ from source: " +
                          (result.stdout + result.stderr).strip()[:1500])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        errors.append(str(exc))
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    errors = check(args.root)
    for error in errors:
        print("FAIL " + error)
    if not errors:
        print("ok agent definitions, skills, and shared instruction hashes match")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
