---
name: decision-packet
description: Prepare and deliver anything that needs Nick's decision — an escalation, a brief line, an approval request, a bid/no-bid, a hold-or-release call, or the drain of a pending register queue. Invoke this whenever work is about to produce a sentence addressed to Nick that asks him to choose, approve, release, sign, spend, or decline, and whenever a queue of pending items is about to be surfaced to him. Also invoke it on "what does Nick need to decide," "escalate this," "put this in the brief," "he needs to approve X," "drain the register," or any reply that was about to contain a row number, a list name, an item identifier, or a link as the thing he must act on. This skill encodes what CLAUDE.md already requires in "The rule that governs every reply," "What maximal leverage means," and "How a decision reaches Nick," so a seat can follow it without rereading the whole standing instruction.
---

# Decision Packet

**Nick never opens a register.** SharePoint, Planner and the registers are this stack's
bookkeeping. They are not his interface and he is not a line-item approver. A decision
reaches him in the daily brief or in a reply, in plain language, already decided except
for his override.

The measure, unchanged from CLAUDE.md: **can Nick act in under a minute without opening
anything else.** Everything below is that sentence made checkable.

## 1. The four required elements

Every decision put to Nick carries all four. A packet missing one is not ready.

| Element | What it is | Failure if missing |
|---|---|---|
| **The decision** | What must be decided, in plain words, no identifier | He has to open something to find out what it is |
| **The recommendation** | One option, with the reason in a clause | He is handed a survey and does the analysis himself |
| **The do-nothing outcome** | What happens if he never answers | He cannot price the cost of ignoring it |
| **The reversibility date** | The date it stops being reversible, **with its external source named** | He cannot tell a real clock from this stack's own scheduling |

Write them in that order. The decision and the recommendation belong in the first two
sentences; the other two follow.

## 2. The default is the recommendation, and silence executes it

Every decision carries a default, and **the default is the recommendation**. If Nick does
not answer by the stated date, the recommendation executes and the register records that
it executed on the default. Silence is an answer, and the packet says so out loud so he
knows what his silence buys.

State it in the packet itself: *"Say nothing and that is what happens."* Never in the
form of a follow-up question.

## 3. Four categories never execute on silence

These always wait for an explicit yes, per the hard constraints:

1. **Spending money.**
2. **Sending anything outside the company.**
3. **Signing anything.**
4. **Any claim about the product or the company made to an outside party.**

A packet in one of these categories still carries all four elements, but its do-nothing
outcome is *"nothing goes out"* — never *"the recommendation executes."* Approval is per
item, never per category.

## 4. The two-sentence test

**A question that cannot be stated in two sentences with a recommendation is not ready to
go to Nick.** Two ways forward, both correct:

- **Work it further** until the recommendation exists. Most "options" are one option and
  two rejected ones that were never rejected in writing.
- **Decide it.** If it is reversible, in scope, and inside a seat's lane, the stack owns
  it and records the decision. Reaching for Nick is not neutral; it spends the minute the
  packet was supposed to save.

No options menus. Give the recommendation and the reason, not a survey. A packet that
lists A, B and C with balanced weight has moved the analysis onto his desk.

## 5. No register identifier is ever the unit of action

**Never name a row, list, item number, or link as the thing to act on.** "Row 83,"
"ProposedChanges 112," "approve rows 14, 16, 17" are meaningless to someone who does not
open the list and has said so. Name the decision.

The identifier is bookkeeping and stays in the register. If a packet needs a pointer for
the stack's own traceability, it lives in the capture file or the register row, not in
the line addressed to Nick.

The same rule kills the link. A packet that ends in a URL has asked him to open
something, which is the one thing the format exists to prevent.

## 6. Never invent a deadline

A date given to Nick **names its external source**: a filing date, a comment period, a
counterparty's date, a statute, a contract, a bid opening. If there is no external date,
say so plainly — *"no deadline, this can wait."*

- **An internal review-by date is this stack's own scheduling.** Label it internal. Never
  present it as a deadline and never escalate against it.
- **Never borrow a date across items.** A date that belongs to one item is not evidence
  about another. A solicitation's bid-opening date reappearing as the decision-relevance
  date on an unrelated policy item is an invented deadline wearing a real one's clothes,
  and it survives every later pass because it looks sourced.
- Re-derive the date from its source at the moment the packet is written. See
  `skills/re-derive-before-asserting/SKILL.md`; a borrowed date is the same failure as a
  borrowed figure.

Inventing urgency teaches him to ignore the dates that are real, which costs more than
the item ever gained.

## 7. A queue is not a decision list

A pending queue surfaced row by row is noise, and **a queue nobody drains is this stack's
failure, not his.** Before any queue reaches Nick:

1. **Cluster by the question underneath, never by the row.** Dozens of rows are usually
   about a handful of decisions. Answer the underlying question once and the rows resolve
   without him reading any of them.
2. **Strip the records.** A transcription of something that already happened, or of an
   executed document, asserts nothing and is not a proposal. Filing a record as a proposal
   asks him to approve his own acts. Those go to their canonical list with their
   provenance and never enter the approval queue.
3. **Propose a standing rule** for each cluster that recurs — approved once, replacing
   repeated adjudication. Thereafter only an exception escalates.
4. **State the drain condition.** Every queue says what empties it and who owns that.
5. **What survives goes in the brief**, one line each with its date, and he answers there.

A queue that requires him to open a list will not be drained. It does not merely
accumulate — **it drops live items**, and a comment period that closed while its row sat
Pending was not decided against, it was never reached.

## 8. Worked examples

`references/worked-examples.md` holds the bad and good version of the same decision, drawn
from real packets this stack got wrong: the policy corpus hold, the borrowed deadline, the
options menu, and the sixty-eight-row queue. Read it before writing a packet of a kind you
have not written before.

The shortest one, because it is the whole skill in two lines:

> **Bad.** "Row 83 is pending, DecisionRelevantBy 2026-09-28. Please approve or advise."
>
> **Good.** "Your fourteen security policy documents claim controls the company does not have.
> Recommendation: hold them from external use until the wording matches what is in place.
> Say nothing and that is what happens. No external deadline — the date on this item is
> our own review scheduling, not a filing."

Same decision. One costs him a SharePoint session and returns nothing; the other is
answerable in the time it takes to read.

## 9. Judgment, recorded so it can be scored

Added 2026-10-16 (change review CR-021, CR-023, CR-024). These make the recommendation's
quality measurable; none of them adds a line to what Nick reads beyond the number.

**Write the recommendation first.** Where the task allows, draft the recommendation and its
probability before reading Nick's stated view of the matter, then compare. Sycophancy is
measured in every major assistant, and the fix that holds is structural: form the judgment
in a context that does not contain the preference.

**Numbers, not probability words.** "Likely," "a good chance," "unlikely" do not appear.
Write "about 30%". A word cannot be scored and two readers hear different numbers in it.

**Base rate first** for any estimate of time, cost, win odds or counterparty behavior: name
the reference class, its rate, its source and its n, then adjust and say why. If no reference
class exists, say so; the estimate is speculation tier.

**Record one forecast** per packet in this stack's prediction ledger
(scripts/predict.py, `add`): a resolvable question, the probability, the base rate, a
resolve-by date with its source. A bid or no-bid records p(award).

**Three hypotheses, one bad for us.** Before evaluating, list at least three explanations
of the same evidence, including one that is bad for us. (The full competing-hypotheses
matrix is not used: controlled studies found no gain.)

**Key-assumptions check.** List each load-bearing assumption and rate it solid, caveated or
unsupported. **An unsupported load-bearing assumption blocks the packet**: work it until it
is supported, or decide without it.

**Signposts.** The premortem ("It is <date>; this failed. Why?") yields one to three dated,
observable signals the stack can check, recorded with `--signpost`. The weekly run checks
them and reopens the decision when one fires.

**Under pushback, name the fact or hold.** If Nick disagrees, either name the new fact that
changes the recommendation, or hold it and say in one sentence why. Every reversal is logged
with the fact that caused it (this stack's prediction ledger, scripts/predict.py, `reverse`); a reversal with no new fact is
refused, because it is deference, not judgment.

**Journal the decision, not the outcome.** On resolution, record decision quality (sound on
what was known then) separately from outcome quality. Good decision with a bad outcome was
luck; only a bad decision earns a lesson.

## Verification

Read the packet against this list before it goes out. Any "no" sends it back.

- [ ] Four elements present, in order.
- [ ] The recommendation is one option, not a survey.
- [ ] The default is stated, and it is the recommendation — or the item is in one of the
      four categories and the packet says nothing goes out without an explicit yes.
- [ ] No row number, list name, item identifier, or link anywhere in the text.
- [ ] Every date names an external source, or is labeled internal and not escalated.
- [ ] The whole thing is answerable without opening anything else.
- [ ] It fits in two sentences plus the two consequence lines. If it does not, it was not
      ready.
- [ ] Every probability is a number; any time, cost, win or counterparty estimate names its
      base rate and reference class.
- [ ] One forecast is recorded in the prediction ledger.
- [ ] No load-bearing assumption is rated unsupported.
- [ ] One to three dated signposts are recorded.

## Pitfalls

- **Permission-seeking is not a decision packet.** "Want me to…" on work already
  authorized is a reply defect, not an escalation. Do the work, then report.
- **An acronym costs the minute the packet was meant to save.** Expand it once on first
  use — POA&M (Plan of Action and Milestones), SDVOSB (Service-Disabled Veteran-Owned Small
  Business) — then use the short form.
- **"Waiting on Nick" without naming what for** is an open loop wearing a closed loop's
  clothes. Name the decision or it is not waiting on him.
- **Do not raise an item under a standing do-not-raise instruction.** The seat that owns
  the register dispositions it so it stops appearing; it never reaches a packet, a brief, or a calendar
  feed.
- **Times in Eastern, never Z.**
- **A packet is not a status report.** Which seat ran, what was dispatched, how a source
  was verified, what was committed — none of it appears. The answer, the decision, the
  number.

## No invented durations

**State no elapsed time between turns.** There is no clock between them, so any interval
a reply asserts is invented, and an invented number put in front of Nick as fact is the
defect the verification discipline exists to refuse -- applied to the one quantity nobody
thinks to check.

Refer to position in the conversation instead: the previous draft, the last reply, the
first version, before the rewrite. A date is fine when it comes from the record -- a
commit date, a solicitation date, a file's own timestamp -- because those are sourced.
"Ten minutes ago," "earlier today" and "an hour ago" are not.
