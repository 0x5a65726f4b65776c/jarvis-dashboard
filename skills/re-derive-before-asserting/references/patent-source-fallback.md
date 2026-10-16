# Reading a granted patent when the usual sources are blocked

Load when a pass needs the actual claims, specification, or bibliographic record of a
granted US patent and the obvious host refuses. The chain below is in order. Stop at the
first step that returns the real document.

**Read the primary text.** A patent summarized from a prior capture, a landscape file, or
a search-result snippet is not the patent. Claim scope questions are decided on claim
language, and claim language is the thing that gets paraphrased first.

---

## The chain, in order

### 1. USPTO Patent Public Search, driven through the headless browser

`https://ppubs.uspto.gov/pubwebapp/` — the issuing office's own system, and the one that
works. This container has Chromium pre-installed at `/opt/pw-browsers`; `curl` alone cannot
drive it.

- Load the app, type the patent number into Quick Search as `<number>.pn.`, and click
  Search. That one real UI search is what creates an authenticated session.
- A single-document pull is then available directly:
  `GET /api/patents/highlight/US-<number>-B2?queryId=…&includeSections=true` returns one
  JSON document with `claimsHtml`, `descriptionHtml`, and the bibliographic and prosecution
  fields (`assigneeName`, `applicantName`, `applicationNumber`, `numberOfClaims`,
  `continuityData`, `applicationFilingDate`).
- For anything broader than one document, replay the session's `x-access-token` (from
  `localStorage`) and `caseId` (from the session-bootstrap response) against
  `POST /api/searches/counts` and `POST /api/searches/searchWithBeFamily`. **Both return
  400 until a session has run one real UI search first.** Field-code syntax: `"<name>".AS.`
  for assignee (quote multi-word names), `"<CPC code>".CPC.` for classification,
  `AND @APD>="YYYYMMDD"<="YYYYMMDD"` for a filing-date range — the `@` is required.
- Anchor every query to an assignee or a classification. Unanchored free-text over-matches
  into the hundred-thousands at USPTO's scale and is not a signal.

### 2. The granted PDF on Google's patent-image host

`patentimages.storage.googleapis.com` serves the granted document as a PDF and is a
different host from the Google Patents web front end, so it can answer when the front end
does not. Read it as pages, not as extracted text — the claim pages are typeset images in
many grants, and an extractor reporting a corrupt file usually means an image layer.

### 3. Espacenet

`worldwide.espacenet.com` — the European Patent Office's register. Carries US grants with
family data and is the best source for what else is in the family.

### 4. WIPO PatentScope

`patentscope.wipo.int` — the international publication record. Useful for the PCT member
of the family and for national-phase entries the US record does not show.

### 5. The tenant's own document libraries

If the patent was ever filed, prosecuted, or diligenced on behalf of an entity in this
stack, a copy may already exist in a SharePoint document library. This is last because it
is a copy of the record rather than the record, and its provenance has to be established
before it is cited — but it is a real source and it does not depend on egress at all.

---

## What is blocked at this session's egress

Recorded because a blocked host looks exactly like an empty record, and the two lead to
opposite conclusions.

| Host | Observed |
|---|---|
| `patents.google.com` | HTTP 503 to a direct fetch, consistently, across passes |
| `patft.uspto.gov`, `ped.uspto.gov`, `assignment.uspto.gov`, `assignment-api.uspto.gov`, `legacy-assignment.uspto.gov`, `search.patentsview.org`, `bulkdata.uspto.gov` | Refused at the CONNECT/tunnel stage — a policy denial before any USPTO application logic ran |
| `api.uspto.gov` (Open Data Portal) | Reachable and real, returns 401 — it wants an API key this identity does not hold |
| `worldwide.espacenet.com`, `patentscope.wipo.int` | HTTP 403 to a direct fetch; not retried through the headless browser, which is the untested step |

**Historical hosts.** PatentsView (`search.patentsview.org`) was shut down on 2026-03-20 and its
data moved to the USPTO Open Data Portal bulk datasets; its row above is a record of what was
observed, not a route. The replacement route is the Open Data Portal (`api.uspto.gov`, key
required) or a read-only server built on it, once the stack has reviewed and pinned one.

**The remedy is the environment's network policy, not a workaround.** These are egress
refusals, not properties of the sources. Anything recorded about them belongs in
this stack's verification ledger (scripts/verified.py) with an expiry, never as a
flat sentence somewhere that hardens into a standing refusal. Re-run the probe before
relying on any row above.

---

## Cross-checking what comes back

- Two captures of the same claim text that agree **byte for byte**, including a typesetting
  artifact, confirm the artifact is in the issued text rather than in a transcription. That
  is strong evidence. Four copies of one upstream document, however, are one source.
- Distinguish the **claims** from the **specification** every time a number is quoted. A
  figure that appears only in a worked example or in a dependent claim is not a limitation
  of the independent claims, and a pass that reports it as one has overstated scope.
- State the **assignee of record exactly as the office shows it**, and state anything in
  flight as in flight. An expected transfer is not a completed one.
