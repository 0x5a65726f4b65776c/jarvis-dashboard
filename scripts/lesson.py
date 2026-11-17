#!/usr/bin/env python3
"""Lesson recorder and evidence gate. See doctrine/agent_delivery.md.

Records a candidate lesson to capture/lessons.jsonl and refuses the shapes that
are known to rot: incident narration, a rule with no mechanism, a near-duplicate
of one already recorded, a negative claim about a tool (those belong in the
verification ledger, with an expiry), and a write aimed at a protected surface.

This script never writes doctrine, a skill, or CLAUDE.md. It records the lesson
and names the surface; the edit itself is a PR.

    lesson.py add --surface skills/competitive-intel/SKILL.md \
        --rule "Read references/firm-context.md before scoping a competitor run" \
        --why "the positioning and prior findings are already there, so a cold run rediscovers them"
    lesson.py list [--surface PATH] [--json]
    lesson.py check
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
import learning_evidence as evidence

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LEDGER = os.path.join(REPO, "capture", "lessons.jsonl")
STATES = ("open", "applied", "validated", "reopened", "superseded", "moved")

# Nick's, or the shared identity. A lesson landing here is proposed, never written.
PROTECTED = (
    "CLAUDE.md",
    "doctrine/preamble.md",
    "doctrine/identity.md",
    "doctrine/voice.md",
)
PROTECTED_PREFIXES = ()

# A rule carrying one of these is an incident report wearing a rule's clothes.
NARRATION = (
    (re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b"), "a date"),
    (re.compile(r"\b(PR|pull request|issue|ticket)\s*#?\d+", re.I), "a PR/issue/ticket number"),
    (re.compile(r"\bUPDATE\s*:", re.I), "an 'UPDATE:' append — fix the original sentence instead"),
    (re.compile(r"\b(this session|last session|earlier today|yesterday|today we)\b", re.I),
     "a reference to when it happened"),
    (re.compile(r"\b(I |we )?(tried|attempted|discovered|realized|found out)\b", re.I),
     "a narrative verb — state the rule, not the discovery"),
)

# A negative claim about the environment is a verification-ledger row with an
# expiry, not a lesson. Recorded as a lesson it hardens into a standing refusal.
#
# The trap here is that "Do not X, because Y" is the commonest shape of a GOOD
# rule, and a naive negation match refuses it. The distinction is the subject: a
# claim about the environment has a tool or endpoint as its subject ("the token
# cannot enumerate /users"); an imperative prohibition has none ("Do not
# reference a prior meeting"). So a negation only counts against a rule when an
# environment noun sits beside it, and never when the rule opens with it.
IMPERATIVE_OPENER = re.compile(
    r"^\s*(?:do ?n[o']t|do not|never|avoid|don't|skip|stop|cut|refuse to)\b", re.I)
NEGATION = re.compile(
    r"\b(?:does ?n[o']t|do ?n[o']t|cannot|can ?not|can't|won't|will not"
    r"|unable to|impossible|not supported|unsupported|inert|no longer works?)\b", re.I)
ENV_NOUN = re.compile(
    r"\b(?:tool|token|endpoint|api|graph|planner|sharepoint|mailbox|browser|cli|script"
    r"|permission|role|grant|scope|credential|secret|command|module|package|binary"
    r"|list|site|drive|library|tenant|registration|mcp|webhook|cron|routine)s?\b"
    r"|\$[A-Z_]{3,}|/(?:users|groups|sites|planner|me)\b|\bgraph_[a-z]+", re.I)
# Inherently environmental: no subject test needed.
ENV_ONLY = (
    re.compile(r"\b(?:returns?|throws?|gives?|got|responds? with)\s+(?:a\s+)?(?:40[0-9]|50[0-9])\b", re.I),
    re.compile(r"\b(?:is|are|was|were)\s+(?:broken|unavailable|unsupported|not supported)\b", re.I),
    re.compile(r"\bAuthorization_RequestDenied\b|\bpermission denied\b", re.I),
)


def reads_as_negative_environment_claim(rule):
    if any(p.search(rule) for p in ENV_ONLY):
        return True
    if IMPERATIVE_OPENER.match(rule):
        return False
    return bool(NEGATION.search(rule) and ENV_NOUN.search(rule))

# Setup state Nick can fix. The fix is durable; the failure is not.
ENVIRONMENT_FAILURE = (
    re.compile(r"\bcommand not found\b", re.I),
    re.compile(r"\b(not installed|uninstalled|missing binary|fresh install)\b", re.I),
    re.compile(r"\b(unconfigured|not configured) (credential|key|secret|token)", re.I),
)

MIN_RULE_CHARS, MAX_RULE_CHARS = 20, 400
MIN_WHY_CHARS, MAX_WHY_CHARS = 15, 300
DUPLICATE_THRESHOLD = 0.6

STOPWORDS = {
    "the", "and", "for", "that", "with", "this", "from", "into", "when", "then",
    "than", "not", "but", "are", "was", "has", "had", "its", "it's", "you", "your",
    "before", "after", "because", "which", "what", "have", "does", "will", "been",
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tokens(text):
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 3 and t not in STOPWORDS}


def similarity(a, b):
    """Jaccard over content tokens. Lexical, deliberately: two rules stated in
    different words about the same thing still share their nouns."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def load(path=None):
    path = path or LEDGER
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit("lessons.jsonl line %d is not JSON: %s" % (n, exc))
    return rows


def save(rows, path=None):
    evidence.atomic_text(path or LEDGER, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def lesson_id(surface, rule):
    return "L-" + hashlib.sha256(("%s\n%s" % (surface, rule)).encode()).hexdigest()[:8]


def norm_surface(surface):
    value = (surface or "").strip()
    while value.startswith("./"):
        value = value[2:]
    return value


def validate(surface, rule, why, existing, *, allow_protected=False):
    """The shared learning shape rules and this repository's protected destinations.
    Returns a list of problems; empty means the lesson may be recorded."""
    surface = norm_surface(surface)
    problems = evidence.surface_problems(REPO, surface, PROTECTED, PROTECTED_PREFIXES,
                                         allow_protected=allow_protected)
    if not surface:
        problems.append("--surface is required: a lesson with no home is a note")
    elif os.path.isabs(surface) or ".." in surface.split("/"):
        problems.append("--surface must stay within this repository")
    elif not allow_protected and surface in PROTECTED:
        problems.append(
            "%s is protected — propose the line to Nick in the reply or a PR body; "
            "recording it here would imply it is already in force" % surface)
    elif not os.path.exists(os.path.join(REPO, surface)) and not surface.endswith(".md"):
        problems.append("%s does not exist in the repo — name the file the lesson belongs in" % surface)

    if len(rule) < MIN_RULE_CHARS:
        problems.append("rule is %d chars; under %d is a label, not a rule" % (len(rule), MIN_RULE_CHARS))
    if len(rule) > MAX_RULE_CHARS:
        problems.append("rule is %d chars; over %d is a paragraph — split it" % (len(rule), MAX_RULE_CHARS))
    if not why or len(why) < MIN_WHY_CHARS:
        problems.append("--why is required: a rule with no mechanism gets followed until it is inconvenient, "
                        "then dropped, because nobody knows what it was protecting")
    elif len(why) > MAX_WHY_CHARS:
        problems.append("why is %d chars; over %d stops being one clause" % (len(why), MAX_WHY_CHARS))

    for pattern, label in NARRATION:
        if pattern.search(rule):
            problems.append("rule contains %s — the rule has to stand without the incident behind it" % label)

    if reads_as_negative_environment_claim(rule):
        problems.append(
            "rule reads as a negative claim about the environment. Those go in the verification "
            "ledger with an expiry, not here: recorded as a lesson it becomes a refusal the stack "
            "cites against itself after the cause is fixed. Use: verified.py add")

    if any(p.search(rule) or p.search(why or "") for p in ENVIRONMENT_FAILURE):
        problems.append(
            "this is setup state, not a durable rule. Record the fix (the install command, the "
            "config step, the variable to set), not the failure")

    for row in existing:
        score = similarity(rule, row.get("rule", ""))
        if score >= DUPLICATE_THRESHOLD:
            problems.append(
                "%.0f%% overlap with %s on %s (%r). The same lesson twice is one rule — "
                "strengthen that one instead, or pass --replaces %s if this supersedes it"
                % (score * 100, row.get("id"), row.get("surface"), row.get("rule", "")[:90], row.get("id")))
            break

    return problems


def cmd_add(args):
    args.surface = norm_surface(args.surface)
    rule, why = " ".join(args.rule.split()), " ".join((args.why or "").split())
    rows = load()
    existing = [r for r in rows if r.get("state") not in ("moved", "superseded")]

    if args.replaces:
        target = next((r for r in existing if r.get("id") == args.replaces), None)
        if target is None:
            raise SystemExit("--replaces %s: no active lesson with that id" % args.replaces)
        existing = [r for r in existing if r.get("id") != args.replaces]

    problems = validate(args.surface, rule, why, existing, allow_protected=False)
    if problems:
        print("REFUSED (%d)" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1

    row = {
        "id": lesson_id(args.surface, rule),
        "recorded": now_iso(),
        "surface": args.surface.strip().lstrip("./"),
        "rule": rule,
        "why": why,
        "trigger": args.trigger or "",
        "state": "open",
        "replaces": args.replaces or None,
    }
    rows.append(row)
    if args.replaces:
        for r in rows:
            if r.get("id") == args.replaces:
                r["state"], r["superseded_by"] = "superseded", row["id"]
    save(rows)

    print("recorded %s -> %s" % (row["id"], row["surface"]))
    print("  %s" % rule)
    print("\nNot yet in force. The edit to %s is a PR." % row["surface"])
    return 0



def cmd_apply(args):
    """Install a lesson only against real target content and an executed check."""
    rows = load()
    if len(args.ids) != 1 or not args.receipt:
        raise SystemExit("apply requires one lesson and --receipt from lesson.py verify")
    row = find_row(rows, args.ids[0])
    installed = evidence.promotion(REPO, row, args.receipt)
    if args.surface and args.surface != installed["surface"]:
        raise SystemExit("--surface disagrees with the tested installation")
    if row.get("promotion"):
        row.setdefault("promotion_history", []).append(row["promotion"])
    row.update(state="applied", applied=installed["at"], applied_to=installed["surface"],
               promotion=installed)
    save(rows)
    print("applied %s; later-run validation is still required" % row["id"])
    return 0


def find_row(rows, ident):
    row = next((r for r in rows if r.get("id") == ident), None)
    if row is None:
        raise ValueError("no lesson with id: %s" % ident)
    return row


def cmd_verify(args):
    row = find_row(load(), args.id)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    receipt = evidence.run_check(
        REPO, lesson_id=row["id"], run_key=args.run_key,
        surface=args.surface or row["surface"], anchor=args.anchor or row["rule"],
        criterion=args.criterion, test_file=args.test_file, inputs=args.input,
        command=command, receipt=args.receipt, timeout=args.timeout)
    print("%s %s: %s" % ("PASS" if receipt["passed"] else "FAIL", row["id"], args.receipt))
    return 0 if receipt["passed"] else 1


def cmd_observe(args):
    rows = load()
    row = find_row(rows, args.id)
    observed = evidence.observe(REPO, row, run_key=args.run_key, result=args.result,
                                evidence=args.evidence, loaded_sha=args.loaded_sha, note=args.note,
                                context_receipt=args.context_receipt)
    row.setdefault("observations", []).append(observed)
    row["state"] = "validated" if args.result == "pass" else "reopened"
    save(rows)
    print("%s %s: %s" % (row["state"], row["id"], args.run_key))
    return 0


def cmd_status(args):
    rows = load()
    if args.ids:
        rows = [find_row(rows, ident) for ident in args.ids]
    statuses = [evidence.assess(row, REPO) for row in rows]
    if args.json:
        print(json.dumps(statuses, indent=2))
    else:
        for status in statuses:
            print("%s %s%s" % (status["id"], status["status"],
                                ": " + "; ".join(status["problems"]) if status["problems"] else ""))
    return 0


def cmd_move(args):
    """Mark lessons as belonging to another stack and export them for it.

    A lesson that governs files this repository does not have never reaches the
    stack it corrects; that is an entity-separation leak, not a ledger entry. The
    row is not deleted -- the ledger is history -- it changes state to "moved",
    names the destination repository and the date, and is written out in full to
    --export so the destination's own lesson.py can ingest it. Same in-place state
    transition as `apply`; a moved row is never re-checked here."""
    rows = load()
    wanted = set(args.ids)
    missing = wanted - {r.get("id") for r in rows}
    if missing:
        raise SystemExit("no lesson with id: %s" % ", ".join(sorted(missing)))
    moved = []
    for r in rows:
        if r.get("id") not in wanted:
            continue
        if r.get("state") == "moved":
            print("skipped %s (already moved to %s)" % (r["id"], r.get("moved_to")))
            continue
        r["prior_state"] = r.get("state")
        r["state"] = "moved"
        r["moved_to"] = args.to
        r["moved_on"] = now_iso()[:10]
        moved.append(r)
    save(rows)
    if args.export and moved:
        with open(args.export, "a", encoding="utf-8") as fh:
            for r in moved:
                out = {k: v for k, v in r.items()
                       if k not in ("state", "moved_to", "moved_on", "prior_state")}
                out["state"] = r["prior_state"]
                out["moved_from"] = os.path.basename(REPO)
                fh.write(json.dumps(out, ensure_ascii=False) + "\n")
    print("moved %d to %s: %s" % (len(moved), args.to, ", ".join(r["id"] for r in moved) or "none"))
    return 0


def cmd_list(args):
    rows = load()
    if args.surface:
        rows = [r for r in rows if r.get("surface") == args.surface.strip().lstrip("./")]
    if args.state:
        rows = [r for r in rows if r.get("state") == args.state]
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("no lessons recorded")
        return 0
    by_surface = {}
    for r in rows:
        by_surface.setdefault(r.get("surface", "?"), []).append(r)
    for surface in sorted(by_surface):
        print("\n%s" % surface)
        for r in by_surface[surface]:
            mark = {"open": " ", "applied": "+", "validated": "v", "reopened": "!",
                    "superseded": "~", "moved": ">"}.get(r.get("state"), "?")
            print("  %s %s  %s" % (mark, r.get("id"), r.get("rule")))
            print("       why: %s" % r.get("why"))
    open_n = sum(1 for r in rows if r.get("state") in ("open", "reopened"))
    print("\n%d lesson(s), %d open" % (len(rows), open_n))
    return 0


def cmd_check(args):
    """Re-validate every open lesson against the current shape rules. A gate that
    only ever ran at write time cannot catch a rule tightened afterwards."""
    rows = load()
    failures = 0
    for i, row in enumerate(rows):
        problems = []
        if not row.get("id") or row.get("state") not in STATES:
            problems.append("row has no id or an unknown state %r" % row.get("state"))
        if row.get("state") in ("open", "reopened"):
            others = [r for j, r in enumerate(rows)
                      if j != i and r.get("state") in ("open", "reopened") and r.get("id") != row.get("id")]
            problems += validate(row.get("surface", ""), row.get("rule", ""), row.get("why", ""),
                                 others, allow_protected=True)
        if row.get("promotion") or args.evidence:
            problems += evidence.assess(row, REPO)["problems"]
        if problems:
            failures += 1
            print("%s (%s)" % (row.get("id"), row.get("surface")))
            for p in problems:
                print("    - %s" % p)
    print("%d open lesson(s) checked, %d with problems" % (
        sum(1 for r in rows if r.get("state") == "open"), failures))
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="record a candidate lesson")
    a.add_argument("--surface", required=True, help="repo-relative file the lesson belongs in")
    a.add_argument("--rule", required=True, help="imperative rule, no dates or incident detail")
    a.add_argument("--why", required=True, help="one clause: the mechanism it protects against")
    a.add_argument("--trigger", default="", help="what prompted it (a correction, a technique)")
    a.add_argument("--replaces", default="", help="id of the lesson this supersedes")
    a.set_defaults(fn=cmd_add)

    l = sub.add_parser("list", help="show recorded lessons")
    l.add_argument("--surface", default="")
    l.add_argument("--state", default="", choices=("",) + STATES)
    l.add_argument("--json", action="store_true")
    l.set_defaults(fn=cmd_list)

    ap_apply = sub.add_parser("apply", help="install a lesson with content and executed regression evidence")
    ap_apply.add_argument("ids", nargs="+", help="lesson ids")
    ap_apply.add_argument("--surface", default="", help="the file that now carries it")
    ap_apply.add_argument("--receipt", default="", help="receipt produced by lesson.py verify")
    ap_apply.set_defaults(fn=cmd_apply)

    vr = sub.add_parser("verify", help="execute a bounded regression check and retain its result")
    vr.add_argument("id")
    vr.add_argument("--run-key", required=True)
    vr.add_argument("--surface", default="")
    vr.add_argument("--anchor", default="", help="exact installed text; defaults to the lesson rule")
    vr.add_argument("--criterion", required=True, help="bounded failure this check detects")
    vr.add_argument("--test-file", required=True)
    vr.add_argument("--input", action="append", default=[], help="additional file covered by the check")
    vr.add_argument("--receipt", required=True, help="new repository-relative JSON receipt")
    vr.add_argument("--timeout", type=int, default=120)
    vr.set_defaults(fn=cmd_verify)

    ob = sub.add_parser("observe", help="record a later check; recurrence reopens the mechanism")
    ob.add_argument("id")
    ob.add_argument("--run-key", required=True)
    ob.add_argument("--result", required=True, choices=("pass", "fail"))
    ob.add_argument("--evidence", required=True, help="later executed verification receipt")
    ob.add_argument("--loaded-sha", required=True, help="instruction revision actually loaded in the later run")
    ob.add_argument("--context-receipt", required=True, help="immutable agent-gate context receipt for that later run")
    ob.add_argument("--note", required=True, help="bounded outcome and what it does not establish")
    ob.set_defaults(fn=cmd_observe)

    st = sub.add_parser("status", help="show evidence state without trusting legacy applied labels")
    st.add_argument("ids", nargs="*")
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=cmd_status)

    mv = sub.add_parser("move", help="mark lessons as another stack's and export them")
    mv.add_argument("ids", nargs="+")
    mv.add_argument("--to", required=True, help="destination repository name")
    mv.add_argument("--export", default="", help="append the full rows here for the destination")
    mv.set_defaults(fn=cmd_move)
    c = sub.add_parser("check", help="re-validate every open lesson")
    c.add_argument("--evidence", action="store_true", help="also fail legacy applied rows without receipts")
    c.set_defaults(fn=cmd_check)

    argv = list(sys.argv[1:] if argv is None else argv)
    command = []
    if argv[:1] == ["verify"] and "--" in argv:
        split = argv.index("--")
        argv, command = argv[:split], argv[split + 1:]
    args = ap.parse_args(argv)
    if args.cmd == "verify":
        args.command = command
    try:
        if args.cmd in ("add", "apply", "observe", "move"):
            with evidence.ledger_lock(LEDGER):
                return args.fn(args)
        return args.fn(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
