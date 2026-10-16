---
name: re-derive-before-asserting
description: The verification discipline for any fact, figure, date, claim, or negative finding at the moment it is used. Invoke this whenever a number, a date, an ownership statement, a capability claim, or a "this does not exist" conclusion is about to be written into a capture file, a register row, a brief, a targeting or relationship brief, a deck, a message, or anything else that leaves the stack — and whenever a value is about to be carried forward from a prior summary, a seat description, a reference file, or an earlier pass. Also invoke it on "is this still right," "where did that number come from," "two sources disagree," "I searched and found nothing," "what can we say about X," or any claim ceiling for an outward-facing artifact. Encodes what CLAUDE.md's verification and claim-ledger constraints already require.
---

# Re-derive Before Asserting

A fact does not stay true because it was true when it was written down. It stays true
because someone re-established it from its source at the moment it was used. Everything
below is that sentence made procedural.

The failures this exists to prevent all share one shape: **a value was copied rather than
derived, and the copy outlived the correction.** A corrected bus voltage lived on in a
reference file and was written into thirteen register rows by the next run. A claim ceiling
was copied from a seat's standing description into a briefing prepared for a live
conversation while a parallel pass was establishing that the underlying claim was
unsupported. A company was named as a structural fit for a manufacturing problem before
anyone read its product line. And an event was twice declared not to exist because two web
searches found nothing.

## 1. Re-establish at the moment of use, and name the source

Every figure, date, name, and claim is re-established from its **authoritative source**
when it is written, and the source is named in the artifact.

**Never derive a fact from any of these:**

| Not a source | Why |
|---|---|
| A prior summary or an earlier pass's capture file | It is a copy of a source, and the source may have moved |
| A seat's standing description or an agent prompt | Standing text is written once and read forever |
| A `references/` file under a skill | It is a convenience cache, and caches go stale silently |
| A register row | The row records what was asserted, not what is true |
| Anything quoted earlier in this session | Quoting your own memory of a file is not reading the file |

The authoritative source is the thing that would settle an argument: the granted patent
text from the issuing office, the executed instrument, the filing itself, the chamber's own
member page, the standard's own scope statement, the claim ledger, the tenant's live
response. A news summary is a pointer to a source, not the source.

**Name it inline.** "45 days, per section 4.2 of the executed agreement read today" beats
"45 days" by exactly the amount that matters when the next pass has to decide whether to
trust it.

## 2. A correction is not applied until every copy is swept

The expensive form of this failure is not the wrong number. It is the wrong number
surviving its own correction in a file nobody thought of as a source.

When a figure, date, or claim is corrected:

1. Grep the whole stack for the old value, including reference files, seat descriptions,
   drafts, and register rows. Not just the file that was wrong.
2. Fix each one **in place**. Never append "UPDATE: actually…" beneath the wrong sentence;
   the next reader finds the wrong sentence first.
3. Record where the authoritative value lives, so the next correction has one target.
4. Check what has already consumed the old value. A correction landed after a run that
   wrote thirteen rows has thirteen rows to fix, and they will not fix themselves.

A correction that touches only the artifact Nick complained about buys one clean reply and
nothing more.

## 3. When two values collide, establish what each one measures

**Before declaring either wrong, find out what each one is a measurement of.** Two numbers
for what looks like the same quantity are very often two correct numbers for two different
quantities, and a "correction" that flattens them propagates a new error with the authority
of a fix.

The case that taught this: one voltage figure was the system as actually built and
demonstrated; the other was the figure used as the worked example in the patent's
specification and in two dependent claims. Both were real. Neither was an error. A pass
that called one of them a typo would have been wrong in a way that is very hard to unwind,
because corrections are trusted more than the values they replace.

Procedure when two values collide:

1. Trace each to its own authoritative source before comparing them.
2. Write down what each one measures, in a clause. If the clauses differ, there is no
   conflict — there are two facts, and both get stated with their referent attached.
3. Only if the clauses are identical is one of them wrong, and then the source hierarchy
   decides, not recency.
4. Record the distinction where the collision will recur, so the third pass does not
   relitigate it.

Bare figures invite this. **State a figure with its referent** — built-and-demonstrated
versus disclosed-in-the-specification, vested versus total, system voltage versus component
rating — never bare.

## 4. A claim ceiling is rebuilt from the ledger at the moment the artifact is written

Any outward-facing artifact — a deck, a one-pager, a targeting or relationship brief, an
email, a message someone will carry into a live conversation — carries a claim ceiling:
what may be said about the product and the company, and in what words.

**That ceiling is rebuilt from the claim ledger when the artifact is written.** It is never
copied from a seat's standing description, from a prior artifact, or from the way the claim
was phrased last week. The ledger is the only authority, and it moves.

This is not process hygiene. A co-ownership line about a granted patent survived into a
briefing prepared for a live conversation while a parallel pass was establishing that the
assignee of record is a different entity and that a reassignment is still in progress.
Someone would have walked into a room and said it.

Two hard edges around this:

- **Ownership and title claims are a category of their own.** State the assignee of record
  as the issuing office shows it, and state anything in flight as in flight. Never state a
  transfer as complete because it is expected to complete.
- **Anything said to an outside party is one of the four categories that never executes on
  silence** (CLAUDE.md hard constraints). It waits for an explicit yes, per artifact.

## 5. Absence of evidence is not evidence of absence

**A failed search is a fact about the search, not about the world.**

Two web searches found nothing scheduled, and the conclusion recorded — twice — was that no
such event existed. It existed: a federal program peer review, invitation-only, never
publicly indexed, run by the office that funds the consortium whose annual meeting followed
it in the same city two days later. The evidence was a save-the-date graphic in an asset
library and a page saying the reviewer application had closed.

The rule, for anything restricted, invitation-only, credentialed, or not publicly indexed:

1. **Say Unknown.** Not "does not exist," not "no evidence of," not "appears not to."
   Unknown is a real answer and it is the honest one.
2. **Name who would know** — the program office, the program manager, the organizer, the
   counterparty, the person in the room. An Unknown with a named settler is actionable; a
   bare Unknown is a shrug.
3. **Never convert a failed search into a denial.** A denial travels further than the
   search that produced it and nobody re-checks a settled negative.
4. **Consider what a restricted thing would look like if it existed** and look for that
   instead: a closed application page, an asset uploaded to an organizer's library, a
   laboratory call, a program page that names the office rather than the event, a deck from
   a prior year that states the structural relationship.
5. Before concluding anything negative, **widen the identifier.** A spoken phrase is often
   an acronym heard as a word, or a program name standing in for the event it hosts.

Corollary: a source that returns nothing may be a source that refuses this session's
egress. Check that before concluding the record is empty. See §6.

## 6. A negative finding about a tool or a source goes to the ledger, with an expiry

Negative claims about tools, endpoints, permissions, and sources are the worst category to
put in prose, because they turn into refusals the stack cites against itself for months
after the underlying problem was fixed. This stack has already carried a permission denial
as settled fact for two weeks without retesting it.

- A blocked host, a refused endpoint, a 403, a 503, a tunnel denial → **a row in this
  stack's verification ledger (scripts/verified.py, `add`)**, with what ran, what
  it proved, what it did *not* prove, and an expiry. Never a flat sentence in a capture
  file or a skill.
- **A targeted check is never upgraded to a general claim.** One app registration refused
  on one collection is not "the tenant refuses this."
- **An expired row is not evidence.** The ledger's `cite` refuses one and names the command
  to re-run. Citing an expired row is the same error as citing nothing.
- **A blocked path resolved later supersedes the earlier row** rather than contradicting
  it. "Blocked with curl alone" and "reachable through a headless browser" are both true,
  and the ledger should say so.
- When a host is refused **at this session's egress**, the remedy is the environment's
  network policy, not a workaround, and the report says so rather than inventing a
  permanent limitation.

## Procedure

Before any fact-bearing sentence leaves a pass:

1. **List every figure, date, name, ownership statement, and capability claim in the draft.**
2. For each: **where did this come from just now?** If the answer is a summary, a seat
   description, a reference file, a register row, or memory — re-derive it.
3. **Name the source inline** for anything a later pass could get wrong.
   Before an outward-facing artifact leaves, run **an independent citation check in a
   separate context**: a checker that receives only the claims and their cited sources,
   never the draft, opens each one and confirms the claim is on it. The author never checks
   its own citations; a claim that fails drops to assumption tier.
4. **Check for a collision** with any other value for the same quantity. If one exists,
   run §3 before writing either.
5. **Rebuild the claim ceiling from the ledger** if the artifact faces outward.
6. **Convert every negative finding** into Unknown-with-a-named-settler, or into a ledger
   row with an expiry. Neither becomes a sentence asserting non-existence.
7. **Sweep for the old value** if anything was corrected during the pass.

## Pitfalls

- **A structural story is not a fact.** "They manufacture at scale, we need manufacturing,
  therefore they are a fit" is a shape, not evidence. Read the product line first: a
  wire-and-cable manufacturer and a power-electronics protection panel are different
  process technologies, and naming the fit before checking is a mismatch dressed as a
  solution. Say the narrower true thing.
- **A capture file is a record of a pass, not an authority.** Cite it for what that pass
  established, with its date, never as the source itself.
- **Confidence compounds wrongly.** Four copies of one number are one source, not four.
  Say so when counting corroboration.
- **A correction is trusted more than the value it replaces.** That is exactly why §3
  exists: a wrong correction is more expensive than the original error.
- **Do not write a dead end up as a method.** A sequence of things that did not work is not
  a workflow; record the working path, or record nothing.
- **A source that renders as garbage is often the wrong reader, not a corrupt file.** A
  presentation PDF that a text extractor calls corrupted is usually an image-heavy slide
  deck — read it as pages.

## When the source is a granted patent

`references/patent-source-fallback.md` has the fallback chain, in order, for reading a
granted patent when the usual hosts refuse. It was rediscovered painfully and will be
needed again.
