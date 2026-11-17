#!/usr/bin/env python3
"""Bind the dashboard maintenance session to the shared delivery contract."""
from pathlib import Path
from check_agent_fidelity import check, write_manifest

ROOT = Path(__file__).resolve().parents[1]


def main():
    definitions = {p.stem for p in (ROOT / ".claude/agents").glob("*.md")}
    if definitions != {"hermes-reviewer"}:
        raise SystemExit("Runtime roster changed; review the authoritative definition mapping")
    write_manifest(ROOT, "jarvis-dashboard", None, [{
        "name": "dashboard-maintainer", "kind": "orchestrator", "definition": "CLAUDE.md",
        "sources": ["CLAUDE.md", "AGENTS.md", "scripts/build_agent_fidelity.py"], "skills": []}])
    errors = check(ROOT)
    if errors:
        raise SystemExit("\n".join(errors))
    print("Dashboard maintenance context and final reviewer verified")


if __name__ == "__main__":
    main()
