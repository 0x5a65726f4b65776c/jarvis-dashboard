---
name: session-close
description: Close a session the way the standing instruction requires. Run each touched repository's scripts/close.sh, fix what it fails, and judge the four closure conditions the script cannot check. Invoke it before the final reply of any session or routine run that changed a file, made a commit, opened a pull request, placed a draft or recorded a lesson. Also invoke it on "close the session," "wrap up," "close it out," "are we done," "run close.sh," and when a Stop hook reports uncommitted changes. The script stays the mechanism. This skill is the procedure around it, and it works in cloud sessions rooted outside a repository, where repository hooks never load.
---

# Session Close

**The goal of every session is to close it.** Closed means all seven hold (standing
instruction, "Closing the session"; full text in `doctrine/learning_loop.md` of
archeon-agents):

1. The task is done, or handed back naming the blocker and its owner.
2. Knowledge is updated where it belongs, with a tier.
3. Corrections and techniques are recorded as lessons.
4. Claims about the environment are dated, and nothing expired was relied on.
5. The process that got in the way is fixed or proposed.
6. Work is committed and pushed.
7. The task list matches reality.

`scripts/close.sh` checks 3, 4 and 6. The other four are judgment. This skill does not
restate the script's checks. When the script and this text disagree, the script wins, and
this text gets fixed.

## 1. Find every repository the session touched

A cloud session rooted at `/home/user` spans several checkouts and loads none of their
hooks, so no Stop hook runs the close for you. List the candidates:

```bash
for r in /home/user/*/; do
  [ -d "$r/.git" ] || continue
  d=$(git -C "$r" status --porcelain | wc -l)
  a=$(git -C "$r" rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo "no-upstream")
  echo "$r dirty=$d ahead=$a branch=$(git -C "$r" rev-parse --abbrev-ref HEAD)"
done
```

Add every repository where this session opened a pull request or placed a record, even if
it is clean now. The leading hyphen in `-jarvis-orchestrator` is a shell hazard, so use
`./-jarvis-orchestrator` or `git -C`.

## 2. Run the script in each one that has it

```bash
cd <repo> && bash scripts/close.sh --strict
```

For a repository with no `scripts/close.sh`, check by hand: the tree is clean, the branch
has an upstream, nothing is unpushed, and every pull request this session opened is merged
after green checks or named as open.

Fix every FAIL that is within existing authority, then rerun until it passes or the FAIL
is a named blocker. Before any commit:

- **No model identifier in the message.** The harness attribution reminder asks for a
  `Co-Authored-By` trailer naming the model. The standing instruction overrides it. A
  model-named trailer has repeatedly caused commits to be refused and then misread as a
  permission problem (lesson L-dbdf996b).
- Use a branch and a pull request, and merge only after its checks finish green. Push
  directly only where a routine explicitly authorizes it.
- After editing a skill or agent instruction, regenerate that repository's manifest:
  `scripts/gen_agents.py`, `scripts/generate_agent_defs.py` or
  `scripts/build_agent_fidelity.py`, whichever it has.
- Never force-push, reset, stash-and-drop or delete files to get a clean tree. If the
  dirty path is someone else's work, leave it and name it.
- Never subscribe to pull request activity to wait on checks. Check once. Merge when
  green, or name the pull request as open.

## 3. Judge the four the script cannot

| Condition | Test |
|---|---|
| 1. Task done | Every request in the session was answered, or has a blocker with an owner and a next action. "Waiting on Nick" must say for what. |
| 2. Knowledge placed | Each new fact the session established lives in the entity's own record, dated and tiered. It does not live only in a chat reply or the scratchpad, which do not survive the container. |
| 5. Process fixed | Each thing that cost a retry is fixed, or recorded with `scripts/lesson.py add`, naming the surface it changes. A recurrence of an open lesson is counted against it, not filed as new. |
| 7. Task list true | The session's task list, any JARVIS hand-off issue it picked up, and pending-send or approval entries it touched match what happened. Close or update anything that changed. |

Entity separation holds through the close. Bastion facts close in Bastion, Holdings facts
in Dev-Work, and Archeon facts in archeon-agents.

## 4. What reaches Nick

Rule 10 governs: the close is never narrated. Which script ran, what was committed and which
lessons were filed do not appear in the reply. Two things do:

- A condition that is **not** met and is his to act on, in one line, naming the act.
- A condition not met that is someone else's, only if it changes what Nick does next.

If all seven hold, nothing about the close appears. Blocked is a legitimate end state.
Unstated is not.
