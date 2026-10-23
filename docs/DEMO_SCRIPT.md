# DocOps Commander — demo script (under 3 minutes)

Target runtime **2:45**. Every quoted line is the copy the current build actually prints (the console applies plural polish, so `failure(s)` appears as `failures` and `2 x` as `2 ×`). Record at 1920×1080 or 1280×720, browser zoom 100%, bookmarks bar hidden.

Before recording: open `http://127.0.0.1:8010`, click **Reset demo**, and confirm the board reads **11 failing · $211,470 at risk · 0 handled**.

---

## 0:00–0:15 — Problem

**Host:** Document teams can see failures. What they cannot see is which cases are safe to automate, which need a person, and what was decided last time.

*[Board visible: 11 failing cases, red and amber tiles, the composer waiting.]*

## 0:15–0:45 — Diagnose

**Host:** Why are today's invoice jobs failing?

*[Type `Why are today's invoice jobs failing?` and press Enter.]*

**On screen:**

> 9 invoice documents are failing right now, carrying $211,470 at risk. The largest patterns are low OCR confidence (3), upstream timeout (2) and missing required field (1). SLA pressure: 5 inside a 12-hour SLA window and 1 already breached. Start with JOB-2047: Meridian Retail, $96,500, 2h overdue, low OCR confidence. Escalate under POLICY-SLA-08. 5 of these match an approved automation policy. Say the word and I will draft the plan for your approval.

**Host:** It scoped the answer to invoices, named the patterns, priced the exposure, and cited the policy that decides the first case.

## 0:45–1:15 — Plan

**Host:** Resolve safe failures and escalate high-risk documents.

*[Type the same sentence. The reply lands with a plan card and the approval modal opens.]*

**On screen:**

> 5 cases match an approved retry policy and involve no human judgement: $35,510 of exposure, with every document under the $25,000 per-document ceiling. 3 need a person (2 × missing required field and suspected duplicate). 3 escalate to the on-call lead (2 × vendor master-data mismatch and low OCR confidence). That is a 3-part plan citing 7 policies. Nothing runs until you approve it; the approval gate is the only way an action reaches the board.

*[Point at the modal: three segments with counts, the risk note, seven policy chips.]*

**Host:** Retry, review, escalate — each segment priced and policy-backed. And nothing has run yet.

## 1:15–1:35 — Approve

**Host:** I'll approve it.

*[Click **Approve plan**. The modal closes; the plan card flips to an “Approved” receipt; a toast confirms recovery.]*

**On screen:**

> Approved. Dispatched 5 retries, queued 3 cases for review and escalated 3 cases.

## 1:35–1:55 — Execute

*[Let the board settle for ~3 seconds. Tiles become **0 failing · $0 at risk · 0 inside SLA · 11 handled**; five retried documents recover; the audit tab fills with one event per job.]*

**Host:** Eleven cases handled in one pass. Five retries already recovered, and every move is in the audit trail.

## 1:55–2:20 — Memory

**Host:** What did we decide about Vendor Orion?

*[Type the question. The reply cites the original plan and outcome; the matched decision card is highlighted in the Memory tab and the tab shows a dot until opened.]*

**On screen:**

> On 23 Oct, 11:11 you approved: retry 5 safe failures, route 6 for human handling. Outcome: dispatched 5 retries, queued 3 cases for review and escalated 3 cases. Policy applied: POLICY-DUP-01, POLICY-FIELD-03, POLICY-OCR-05, POLICY-RETRY-01, POLICY-RETRY-02, POLICY-SLA-08, POLICY-VENDOR-04. Jobs: JOB-2041, JOB-2053. …

**Host:** Same vendor, same jobs, same policies — pulled from durable memory, not from the chat history.

## 2:20–2:35 — The MCP proof

*[Click **Help**. The modal states the console is a real MCP client.]*

**Host:** Under the UI is a self-hosted MCP server speaking protocol 2025-11-25 over Streamable HTTP. This console performs the handshake, lists the nine tools and calls them — and any external agent can do exactly the same against `/mcp`.

*[Optional 3-second cut: terminal running `python scripts/mcp_smoke.py`, ending on “ALL 28 MCP CHECKS PASSED”.]*

## 2:35–2:45 — Impact

**Host:** Less time triaging, fewer unsafe retries, and a decision trail that survives the session.

*[Title card: “DocOps Commander — Alexa+ style agent for document operations.”]*

---

## Production notes

- **Reset before every take.** Without it, the board is already handled and the diagnosis says “Nothing is failing for the invoice queue right now.”
- **One approval only.** The second approval attempt returns `Action is already approved` by design; do not include it in the happy path.
- **The recall timestamp is local time** and will differ from the example; leave it as recorded.
- **No API key or network.** Narration is deterministic, so takes are reproducible.
- **If pressed for time:** cut 0:00–0:15 and 2:20–2:35 first; the core proof is diagnose → plan → approve → recall.
- **Do not show** `docops.db`, `.env`, or editor tabs — synthetic data only, and the repo is public.
