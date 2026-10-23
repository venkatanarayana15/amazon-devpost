# Submission packet — Build, Ship, Shape: Amazon Developer Hackathon

**Track:** Alexa+ (self-hosted MCP server + simulated Alexa+ experience)
**Deadline:** Friday, October 23, 2026, 12:00 PM Pacific (October 24, 12:30 AM IST)
**Judging:** November 9–20, 2026.

Everything in this file is copy meant for the Devpost form. The full answers behind
the summaries live in [`FEEDBACK.md`](FEEDBACK.md) and [`FRICTION_LOG.md`](FRICTION_LOG.md).

---

## 1. Field-by-field pastes

### Project name

```
DocOps Commander
```

### Tagline / elevator pitch (≤ 200 characters)

```
An agent that diagnoses document-processing failures, drafts a policy-cited plan, and executes only after a human approves — a self-hosted MCP server with a real MCP client console.
```

### Longer one-liner (≤ 140 characters, for socials)

```
An Alexa+ style agent that turns document-processing failures into policy-cited plans — executed only after a human approves.
```

### Built with (tag list)

```
python, fastapi, uvicorn, pydantic, sqlite, model-context-protocol, mcp, json-rpc,
streamable-http, vanilla-javascript, html, css
```

### Choose your track

```
Alexa+
```

### Mini challenges entered

```
None — this submission is entered in the Alexa+ primary track only.
```

(We did not use an AWS runtime service and made no separate open-source
contribution during the window, so neither mini challenge is claimed.)

### Links

| Field | Value |
|---|---|
| Code repository | `https://github.com/<user>/docops-commander` — **public, MIT** |
| Demo video (< 3 min) | `https://youtu.be/<id>` — **public on YouTube** |
| Try it out | No hosted instance; judges run it locally in two commands (see §5) |
| Architecture | `docs/ARCHITECTURE.md` in the repo |

### "Try it out" / testing instructions for judges

```bash
git clone https://github.com/<user>/docops-commander
cd docops-commander
python -m venv .venv
source .venv/bin/activate            # Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010
```

Open `http://127.0.0.1:8010`. No API key, no network access, no paid services.
The 17-job synthetic dataset and the SQLite database are created on first start.

Expected on load: **11 failing · $211,470 at risk · 0 handled**, and the MCP pill
reads **MCP 2025-11-25 · 9 tools**.

Try these three utterances (or click the chips):

| Utterance | Expected result |
|---|---|
| `Why are today's invoice jobs failing?` | 9 invoices, `$211,470` at risk, policy citation, first case JOB-2047 |
| `Resolve safe failures and escalate high-risk documents` | Approval card: retry 5 · review 3 · escalate 3, 7 policies; **nothing runs** |
| Click **Approve plan** | 11 jobs handled, 5 retries recover; audit + memory update |
| `What did we decide about Vendor Orion?` | Original plan, jobs, policies and outcome, highlighted in Memory |
| `Hold everything for manual triage` | Hold recorded; no jobs move |
| **Reset demo** | Back to 11 failing, memory cleared |

Protocol proof (second terminal, server still running):

```bash
python scripts/smoke_test.py   # 37 checks, expect ALL 37 CHECKS PASSED
python scripts/mcp_smoke.py    # 28 checks, expect ALL 28 MCP CHECKS PASSED
```

Both are standard-library only and exit non-zero on regression.
`GET /mcp` returns `405` with `Allow: POST, DELETE` by design; `POST /mcp` is the
JSON-RPC endpoint. `GET /api/meta` advertises the endpoint, transport, protocol
version and tool list.

---

## 2. Devpost story blocks

### Inspiration

Document-operations teams can see their failures. They have dashboards full of red
tiles. What they do not have is an answer to the three questions that actually
unblock the queue: **which cases are safe to automate, which need a person, and what
did we decide last time?**

That gap is not a visibility problem, it is a judgement problem — and it is exactly
the shape of work an agent should do, provided a human keeps the last word. We built
DocOps Commander around that constraint: the agent can diagnose, price the exposure,
and draft a plan, but it cannot move a single document until someone approves.

### What it does

1. **Diagnoses** failures by pattern — low OCR confidence, missing required fields,
   vendor master-data mismatch, schema validation errors, upstream timeouts — with
   SLA pressure and dollar exposure, scoped to the question asked.
2. **Prioritises** open cases by SLA risk, value and failure class.
3. **Drafts** a three-segment plan (retry · human review · escalate) that cites the
   seven policies it applied and explains the risk in plain language.
4. **Waits.** The plan is recorded as `pending`; `draft_resolution_plan` cannot
   execute. `approve_plan` is the only executor, and a second approval is refused
   at the protocol layer.
5. **Remembers.** Every approval and rejection is durable, so "what did we decide
   about Vendor Orion?" returns the original plan, affected jobs, policies and
   outcome — or says honestly that nothing matches.

Two surfaces, one brain: a self-hosted **MCP server** (`POST /mcp`, protocol
`2025-11-25`, Streamable HTTP, nine tools) and a light Amazon-style **console** that
is itself a real MCP client — it performs `initialize` → `notifications/initialized`
→ `tools/list` → `tools/call` in the browser, and every panel it renders comes from
`structuredContent`. Nothing in the UI takes a private shortcut.

### How we built it

- **Transport first.** One FastAPI process hosts the MCP endpoint, the JSON API and
  the static console. The MCP layer is a small JSON-RPC dispatcher written by hand:
  `initialize`, `tools/list`, `tools/call`, batch requests, session ids, and the
  standard error codes (`-32700`, `-32600`, `-32601`, `-32602`, `-32603`).
- **Policy engine, first match wins.** Nine ordered rules in `domain.py` decide
  retry, human review or escalation from failure class, confidence, attempts, amount,
  vendor tier and SLA state. The plan simply groups cases by the rule that fired, so
  every segment has a citation.
- **Approval as a tool, not a UI convention.** Drafting writes a `pending_actions`
  row plus a `pending` decision. `approve_plan` executes and flips the decision;
  `reject_plan` records a rejection just as durably. The gate survives any client.
- **Durable memory.** SQLite holds decisions, pending actions, messages and audit —
  no services, one transaction to reset the demo.
- **Deterministic narration.** Without an API key, every reply is produced by pure
  Python, so the demo is reproducible and inspectable. An optional polish step can
  rewrite sentences, but the facts always come from the tool layer.
- **Verification as a product feature.** 37 HTTP assertions and 28 MCP conformance
  assertions, both standard-library only, covering scope, plural copy, the approval
  gate, double-approval refusal, recovery, recall misses and protocol errors.

### Challenges we ran into

- **The spec is written for streaming servers.** For a request/response-only server,
  `GET /mcp` has no obvious required behavior. Returning the SPA because
  `StaticFiles` was mounted at `/` made the wrong choice silent. We settled on
  `405` with `Allow: POST, DELETE`, and assert it.
- **Header casing.** HTTP stacks lowercase `Mcp-Session-Id`. A naive client lookup
  silently starts a new session on every call. The fix is a case-insensitive lookup,
  now covered by a test that asserts the session id is issued and reused.
- **Approval has no first-class primitive.** Until elicitation is broadly supported,
  an approval gate is two tools plus client state. Deciding what happens on timeout,
  double approval or a disconnected client was on us; the pending record written at
  draft time is what makes all three answerable.
- **Honest recall is harder than confident recall.** Early versions returned the
  nearest decision for any question. That is worse than no memory. The agent now
  classifies intent first and admits a miss.

### Accomplishments we are proud of

- The approval gate holds **at the protocol layer**: a second `approve_plan` returns
  `isError: "Action is already approved"`, so no client can double-execute.
- The console is not a mock MCP client. It speaks the same handshake and tool calls
  an external agent would; the UI has no private data path.
- 65 automated checks pass from a clean checkout, with no API key and no network.
- The demo closes the loop end to end: diagnose → price → plan → approve → execute →
  recall, with an audit trail for every job.
- Recall is honest: it cites the original decision, or says nothing matched.

### What we learned

A tool surface is a better product boundary than a REST API for an agent: discovery,
descriptions and error semantics travel with the tools. But the hard part is not
calling tools — it is designing the point where the agent must stop. Approval needs
to be data (a pending record, an executor tool, a double-execution guard), not a
dialog box. And durable memory changes the conversation: once the agent can say what
happened last time, the operator stops re-explaining the queue.

### What's next

- Wire the same nine tools into a hosted Alexa+ skill runtime when partner tooling
  becomes available; the MCP surface is already the integration point.
- Editable policy authoring, so an operations lead can change a threshold without a
  deploy — with the same approval gate on policy changes.
- Per-vendor outcome feedback: track whether retries actually recovered, and use it
  to tighten the retry rule.
- SSO and a read-only auditor role; the audit table already has the shape for it.

### How it was significantly updated

The project was created during the hackathon submission window (August 31 – October
23, 2026) specifically for the Alexa+ track. There is no pre-existing codebase.

---

## 3. Product feedback (required, per tool)

Full answers to all five required questions are in [`FEEDBACK.md`](FEEDBACK.md).
Paste that file, or this summary, into the feedback field.

| Tool / API / SDK | Used for | Worked well | Needs work | Onboarding | Build again |
|---|---|---|---|---|---|
| **MCP 2025-11-25 (Streamable HTTP)** | The product surface: 9 tools, handshake, structured results | Cleanest agent contract we have integrated; `structuredContent` drives the whole UI | Transport docs are streaming-first; `GET /mcp` semantics unclear; header casing trap | One afternoon to hand-write the handshake; no SDK needed | **Yes** — discovery + descriptions + errors travel with the tools |
| **FastAPI + uvicorn + pydantic** | MCP endpoint, JSON API, static console, one process | Stable payloads, three-package install, no build step | Mount order can shadow `/mcp` with the SPA, silently | Under 10 minutes to a working endpoint | **Yes** for a single-process demo |
| **SQLite (stdlib)** | Decisions, pending actions, audit, messages | Durable memory with zero services; reset is one transaction | Default journal mode plus polling can hit "database is locked" | Immediate; no migrations needed | **Yes** for single-node, with WAL on from the start |
| **Vanilla JS + Fetch (browser MCP client)** | The console's real MCP client | Streamable HTTP is just `POST`; no SDK, no build step, fully auditable | Header lookup is the sharp edge; hand-rolled session, typing and JSON-RPC error handling | A few hours to a conforming client | **Yes** — the page proves the contract an external agent uses |
| **Python stdlib `urllib`** | The two verification suites | Conformance tests with zero dependencies; judges verify without installing anything | Response headers arrive case-sensitively when converted to a plain dict | None — ships with Python | **Yes** for smoke tests |

### Feature requests (optional, priority rated)

1. **A native "requires confirmation" tool result.** — **Critical.**
   Agents that touch money or customer records all re-implement an approval gate as
   two tools plus client state. A standard pending/confirm/decline result shape, with
   defined timeout and double-execution behavior, would make the safe path the easy
   path and let clients render it natively.

2. **A request/response-only profile for Streamable HTTP.** — **Important.**
   State exactly what a server that does not hold an SSE stream must return for
   `GET /mcp` (we use `405` with `Allow: POST, DELETE`) and for session teardown
   (`DELETE /mcp`). Today the failure looks like a routing bug, not a protocol choice.

3. **A public Alexa+ skill simulator that points at a self-hosted MCP endpoint.**
   — **Important.**
   The gated tooling is unavailable to hackathon developers by design, so every team
   builds its own client to prove the integration. A public simulator (endpoint URL
   in, conversation and card rendering out) would let teams test against something
   closer to the real runtime, and would make card/carousel rendering testable.

4. **`structuredContent` rendering examples for cards and carousels.** — **Nice-to-have.**
   The spec makes results machine-readable, but the expected visual vocabulary is
   left to the client. A small reference of list/card/carousel payloads would help
   teams build surfaces that look native.

---

## 4. Friction log (optional, scored — up to 10% bonus)

Full entries with task, expected vs. actual, severity, workaround and suggestion are
in [`FRICTION_LOG.md`](FRICTION_LOG.md). Summary:

| # | Friction | Severity | Workaround |
|---|---|---|---|
| 1 | Streamable HTTP is described for streaming servers; `GET /mcp` behavior for a request/response server is unspecified, and a shadowing static mount makes the mistake silent | Medium | `POST` for JSON-RPC, `DELETE` for teardown, `GET` → `405` + `Allow`, asserted in the suite |
| 2 | Human approval has no first-class primitive; two-tool modelling, timeouts and double approval are left to the developer | Medium | Pending record at draft time, single executor, double-approval refusal, durable rejection |
| 3 | Session header casing: lowercased `Mcp-Session-Id` silently starts a new session | Low to reproduce, high to notice | Case-insensitive lookup; test asserts issue and reuse |

---

## 5. Demo video

Script and exact narration: [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md). Target **2:45**.
Must be public on YouTube or Vimeo and under three minutes.

Shot list:

| Time | Shot |
|---|---|
| 0:00 | Board: **11 failing · $211,470 at risk** |
| 0:15 | `Why are today's invoice jobs failing?` → scoped reply |
| 0:45 | `Resolve safe failures and escalate high-risk documents` → plan card + approval modal (3 segments, risk note, 7 policy chips) |
| 1:15 | Click **Approve plan** → receipt + toast |
| 1:35 | Board settles: **0 failing · $0 at risk · 11 handled**, audit fills |
| 1:55 | `What did we decide about Vendor Orion?` → recall + Memory highlight |
| 2:20 | **Help** modal: "This page is a real MCP client" |
| 2:35 | Title card + impact line |

Optional 3-second cut: terminal running `python scripts/mcp_smoke.py`, ending on
`ALL 28 MCP CHECKS PASSED`.

Before every take: open the app, click **Reset demo**, confirm 11 failing.

---

## 6. Screenshots (in repo root)

| File | Shows |
|---|---|
| `final-01-welcome.png` | Board on load, composer, MCP pill |
| `final-02-diagnose.png` | Scoped diagnosis with patterns and exposure |
| `final-03-approval.png` | Approval modal: 3 segments, risk note, policy chips |
| `final-04-handled.png` | Post-approval board and audit trail |
| `final-05-recall.png` | Recall reply with the matched decision highlighted |

---

## 7. Submission-day checklist

**Repository**
- [x] `LICENSE` — MIT at repo root, GitHub will surface it in the About panel
- [x] `README.md` — pitch, qualification table, quick start, tool table, screenshots
- [x] `requirements.txt` — fastapi, uvicorn, pydantic only
- [x] `.env.example` — documents optional LLM settings, no secrets
- [x] `.gitignore` — database files, `.env`, `.venv`, caches excluded
- [x] `backend/`, `frontend/`, `data/`, `scripts/`, `docs/`
- [x] No employer or customer data; synthetic only; no secrets
- [ ] Published to GitHub as **public**, repo URL pasted into Devpost
- [ ] Clone into a fresh directory and follow only the README

**Submission**
- [x] Track selected: Alexa+
- [x] Text description written (§2)
- [x] Product feedback per tool (§3)
- [x] Friction log entries (§4) — bonus criterion
- [x] Feature requests with priority (§3)
- [x] 5 screenshots
- [ ] Video recorded, under 3 minutes, uploaded publicly, URL pasted
- [ ] Repo URL and video URL entered on Devpost
- [ ] Submitted before October 23, 12:00 PM Pacific
- [ ] Devpost page opened in a private window to confirm it is live

**Final 30 minutes**
- [ ] `python scripts/smoke_test.py` exits 0
- [ ] `python scripts/mcp_smoke.py` exits 0
- [ ] Reset the demo so the board opens at 11 failing
- [ ] Confirm the GitHub repo is public and does not contain `docops.db`, `.env` or `.venv`
- [ ] Submit

---

## 8. AI-tool disclosure (optional, if the form asks)

```
An AI coding assistant was used for pair-programming, test scaffolding and copy
editing. The architecture, policy engine, tool contracts and demo flow are our own
work, every behavior in the demo is covered by the repository's own smoke suites,
and all data is synthetic.
```
