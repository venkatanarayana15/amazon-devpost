# Devpost answers — Build, Ship, Shape: Amazon Developer Hackathon

Paste-ready answers for the 18 fields on the Devpost submission form.
Track: **Alexa+**. Everything below is self-contained; no links to internal docs
are required, though they are included where useful.

Replace `<repo-url>` and `<video-url>` before pasting.

---

## 1. Project name

```
DocOps Commander
```

---

## 2. Elevator pitch (short tagline)

```
An agent that diagnoses document-processing failures, drafts a policy-cited plan, and executes only after a human approves — a self-hosted MCP server with a real MCP client console.
```

Alternate, shorter:

```
A human-in-the-loop agent for document operations: diagnose the failures, draft a policy-cited plan, approve before anything runs.
```

---

## 3. About the project (Markdown)

```markdown
## Inspiration

Document-operations teams can see their failures. They have dashboards full of red
tiles. What they do not have is an answer to the three questions that actually
unblock the queue: **which cases are safe to automate, which need a person, and what
did we decide last time?**

That gap is not a visibility problem, it is a judgement problem — and it is exactly
the shape of work an agent should do, provided a human keeps the last word. DocOps
Commander is built around that constraint: the agent can diagnose, price the
exposure, and draft a plan, but it cannot move a single document until someone
approves.

## What it does

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
is itself a real MCP client. The page performs `initialize` →
`notifications/initialized` → `tools/list` → `tools/call` in the browser, and every
panel it renders comes from `structuredContent`. Nothing in the UI takes a private
shortcut.

## How we built it

- **Transport first.** One FastAPI process hosts the MCP endpoint, the JSON API and
  the static console. The MCP layer is a small JSON-RPC dispatcher written by hand:
  `initialize`, `tools/list`, `tools/call`, batch requests, session ids, and the
  standard error codes (`-32700`, `-32600`, `-32601`, `-32602`, `-32603`).
- **Policy engine, first match wins.** Nine ordered rules decide retry, human review
  or escalation from failure class, confidence, attempts, amount, vendor tier and
  SLA state. The plan simply groups cases by the rule that fired, so every segment
  has a citation.
- **Approval as a tool, not a UI convention.** Drafting writes a `pending_actions`
  row plus a `pending` decision. `approve_plan` executes and flips the decision;
  `reject_plan` records a rejection just as durably. The gate survives any client.
- **Durable memory.** SQLite holds decisions, pending actions, messages and audit —
  no services, and one transaction resets the demo.
- **Deterministic narration.** Without an API key, every reply is produced by pure
  Python, so the demo is reproducible and inspectable. An optional polish step can
  rewrite sentences, but the facts always come from the tool layer.
- **Verification as a product feature.** 37 HTTP assertions and 28 MCP conformance
  assertions, both standard-library only, covering scope, plural copy, the approval
  gate, double-approval refusal, recovery, recall misses and protocol errors.

## Challenges we ran into

- **The spec is written for streaming servers.** For a request/response-only server,
  `GET /mcp` has no obvious required behavior. Returning the SPA because
  `StaticFiles` was mounted at `/` made the wrong choice silent. We settled on `405`
  with `Allow: POST, DELETE`, and assert it in the suite.
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

## What we learned

A tool surface is a better product boundary than a REST API for an agent: discovery,
descriptions and error semantics travel with the tools. But the hard part is not
calling tools — it is designing the point where the agent must stop. Approval needs
to be data (a pending record, an executor tool, a double-execution guard), not a
dialog box. And durable memory changes the conversation: once the agent can say what
happened last time, the operator stops re-explaining the queue.

## What's next

- Wire the same nine tools into a hosted Alexa+ skill runtime when partner tooling
  becomes available; the MCP surface is already the integration point.
- Editable policy authoring, so an operations lead can change a threshold without a
  deploy — with the same approval gate on policy changes.
- Per-vendor outcome feedback: track whether retries actually recovered, and use it
  to tighten the retry rule.
- SSO and a read-only auditor role; the audit table already has the shape for it.
```

---

## 4. Built with (max 15 tags)

```
python, fastapi, uvicorn, pydantic, sqlite, model-context-protocol, mcp, json-rpc, streamable-http, vanilla-javascript, html, css
```

---

## 5. GitHub repo and demo URL

Repository:

```
<repo-url>
```

Demo:

```
No hosted deployment. The project is a self-hosted MCP server and runs locally in
two commands: pip install -r requirements.txt, then
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010. Open
http://127.0.0.1:8010. No API key, no paid services, synthetic data only.
```

---

## 6. Video demo link

```
<video-url>
```

---

## 7. Repository URL for judging and testing

```
<repo-url>
```

The repository is public under the MIT license, with the license visible in GitHub's
About panel. It contains the full source (`backend/`, `frontend/`), the synthetic
dataset (`data/sample_jobs.json`), the setup instructions (`README.md`), the
architecture notes (`docs/ARCHITECTURE.md`) and both verification suites
(`scripts/smoke_test.py`, `scripts/mcp_smoke.py`).

---

## 8. AWS Builder Mini Challenge — yes/no

```
No
```

---

## 9. AWS Builder: which AWS services and how?

```
Not applicable — this submission is entered in the Alexa+ primary track only and
does not use AWS services. The MCP server is self-hosted and runs on a laptop with
Python, FastAPI and SQLite; narration is deterministic, so no hosted model or
database is required to demo or judge the project.
```

---

## 10. Open Source Mini Challenge — yes/no

```
No
```

If a description field is required:

```
Not applicable — no separate open-source contribution was made during the hackathon
window. The primary project itself is released as a new MIT-licensed open-source
repository.
```

---

## 11. Feedback 1 — Which developer tools, APIs, and SDKs did you use, and for what?

```
The project uses the Model Context Protocol (spec 2025-11-25) over Streamable HTTP
as its product surface. The MCP server is hand-written on top of FastAPI and
exposes nine tools — orchestrate, get_board, diagnose_jobs, prioritise_cases,
draft_resolution_plan, approve_plan, reject_plan, recall_decisions and reset_demo —
with JSON Schemas, descriptions, human-readable content and structuredContent.
The browser console is a real MCP client, written in vanilla JavaScript with the
Fetch API, performing initialize, notifications/initialized, tools/list and
tools/call against the same endpoint.

FastAPI serves the MCP endpoint, a small JSON API for tests, and the static
console from one process; uvicorn runs it. Pydantic validates the REST payloads and
keeps the tool results stable. SQLite (standard library sqlite3) stores decision
memory, pending actions, chat messages and the audit trail, so approvals and
rejections survive a restart. Python's standard library urllib and json power the
two conformance suites — 37 HTTP checks and 28 MCP checks — so judges can verify the
project without installing any test dependency.

No Amazon SDK, hosted model or paid service is used. An optional narration-polish
hook can call an OpenAI-compatible chat endpoint if a key is configured, but it is
disabled by default and the demo never depends on it. All data is synthetic.
```

---

## 12. Feedback 2 — What worked well?

```
MCP 2025-11-25 was the cleanest agent contract we have integrated. tools/list with
JSON Schema, tools/call returning both content and structuredContent, and the
standard JSON-RPC error codes gave one predictable path for both the console and any
external agent. structuredContent meant the UI needed no private API at all: the
same payload renders the board, the plan and the memory panel. A tool surface is a
better product boundary than a REST API, because discovery, descriptions and error
semantics travel with the tools.

FastAPI and uvicorn gave a single-process deployment — static files, the JSON API
and the MCP endpoint on one port — with a three-package install and no build step,
and pydantic kept the response shape stable while the transport was still moving.
SQLite delivered durable decision memory with zero services: recording a decision at
draft time, flipping its outcome on approval, and querying it for recall took a few
dozen lines, and the same file also holds the audit trail and pending actions, so a
demo reset is one transaction.

The browser client worked out well too. Streamable HTTP is a POST with JSON, so the
whole client is a small fetch wrapper with no SDK and no bundle; the page is
auditable with View Source and proves the tool contract works for an arbitrary
client, not just for our own tests. Finally, Python's standard library was enough to
write both conformance suites, which keeps requirements.txt honest and lets a judge
verify the MCP server without installing anything.
```

---

## 13. Feedback 3 — What needs work?

```
MCP transport documentation is written around servers that stream. For a
request/response-only server, the required behavior of GET /mcp is easy to misread —
we settled on 405 with Allow: POST, DELETE, plus DELETE /mcp for session teardown,
and assert it. A short "request/response only" profile with exact status codes would
remove the guesswork. Header casing is the other sharp edge: Mcp-Session-Id is
lowercased on the wire by common HTTP stacks, and a naive case-sensitive lookup in a
client silently starts a new session on every call. The spec should state that
header names must be compared case-insensitively and show lowercase names in its
examples. Human approval also has no first-class primitive yet: until elicitation is
broadly supported, every team models an approval gate as two tools plus client
state, and must decide on its own what happens on timeout, double approval or a
disconnected client.

FastAPI has a silent footgun: mounting StaticFiles at "/" before including the MCP
router makes /mcp return the SPA instead of a JSON-RPC response or a 405. A warning
when a mount shadows an existing route would save an hour. SQLite's default journal
mode plus a 3-second recovery poll can hit "database is locked" under concurrent
writes, so a documented WAL recommendation for polling demos would help. The
standard-library HTTPMessage is case-sensitive when you convert it to a plain dict,
which is how the session-header bug surfaced. And in the browser there is no MCP
client to lean on, so session handling, JSON-RPC error decoding and
request-versus-notification framing are all hand-rolled.
```

---

## 14. Feedback 4 — How was your onboarding experience?

```
MCP: hand-writing the handshake took an afternoon — initialize,
notifications/initialized, tools/list, tools/call are four small JSON objects. No SDK
was required, which made the server auditable end to end; the friction was
transport semantics, not the tool model.

FastAPI and uvicorn: under ten minutes from pip install to a working JSON endpoint.
Auto-generated /docs was useful for sanity-checking the REST surface while the MCP
transport was still being written. Pydantic added no noticeable onboarding cost.

SQLite: immediate. No migrations were needed for a demo schema; sqlite3 plus
CREATE TABLE IF NOT EXISTS was enough, and the whole data model fit in one file.

Browser client: a few hours from an empty app.js to a conforming MCP client, mostly
spent on the handshake rather than the interface, since Fetch already does everything
Streamable HTTP needs.

Verification suites: zero onboarding — urllib and json ship with Python, so a judge
can run both suites from a clean checkout without installing a test framework.
```

---

## 15. Feedback 5 — Would you build with these devices and services again?

```
Yes, on every one of them.

MCP: yes. A tool surface is the right product boundary for an agent, and a
self-hosted server keeps the whole contract inspectable. We would keep the same
nine-tool design and add elicitation-based confirmation when clients support it.

FastAPI, uvicorn and pydantic: yes for prototypes and small services. The
single-process deployment — MCP endpoint, JSON API and static console on one port —
is exactly right for a demo and would hold up for an internal tool.

SQLite: yes for a single-node agent with durable memory, with WAL enabled from the
start. It is the shortest path from "the agent should remember" to working code.

Vanilla JavaScript with Fetch: yes. The page being a real MCP client rather than a
mock is the strongest proof the tool contract works for an arbitrary client, and it
cost no build tooling.

Python's standard library: yes for smoke tests. A production client would use an
SDK, but a zero-dependency suite is the right thing to hand a judge.
```

---

## 16. Which AI tools have you leveraged while working on this project?

```
An AI coding assistant (Codebuff / Freebuff) was used as a pair programmer:
scaffolding, the MCP JSON-RPC dispatch loop, the policy-engine tests, copy editing
for the documentation, and reviewing the demo flow. The architecture, the policy
rules, the tool contracts, the approval model and the verification assertions are
our own work, and every behavior shown in the demo is covered by the repository's
own smoke suites. No AI service is required at runtime: narration is deterministic
Python, the optional LLM polish hook in backend/llm.py is disabled by default, and
the demo runs with no API key and no network access. All data is synthetic.
```

---

## 17. Describe the level of learning you/your team derived from the project

```
High. The project started from zero MCP experience and ended with a conformant
server and a conformant browser client, verified by 28 protocol checks: the
initialize handshake, session ids, batch JSON-RPC, tool discovery with JSON Schema,
structuredContent, and the difference between protocol errors and tool failures.

The deeper learning was in agent design rather than protocol mechanics. First, the
approval gate belongs in the tool layer, not in the UI: making draft_resolution_plan
incapable of executing, and approve_plan the only executor with a double-approval
guard, is what makes the safety property survive any client. Second, memory has to
be written at draft time, not on success, or a disconnected client loses the record
of what it almost did. Third, an honest "nothing matches" is a feature — early
versions returned the nearest decision for any question, which is worse than no
memory at all.

We also learned how much of a product can live in the tool boundary: with
structuredContent as the contract, the console has no private data path, and the
same nine tools would serve an Alexa+ skill runtime or any other MCP client. Finally,
writing the two conformance suites changed how the transport was read — the spec's
ambiguities around GET /mcp and header casing were found by testing, not by reading.
```

---

## 18. Did you gain AI value that you can use in your career?

```
Yes. The reusable value is a pattern for human-in-the-loop agents: diagnose, draft,
approve, execute, remember — with the approval gate enforced by the tool contract
rather than by a dialog box. That maps directly onto document operations, finance
approvals, access changes and any workflow where an agent can propose but a person
must decide.

Concretely, three things transfer: (1) designing tools as a product surface, with
discovery, schemas, structured results and honest error semantics; (2) modelling
approval as data — a pending record, a single executor, an idempotency guard, and a
durable rejection — which is what makes an agent auditable; (3) using deterministic
narration so an AI-assisted system can be tested and demonstrated reproducibly, with
the model improving wording but never owning the facts. The project also gave a
practical read on where MCP tooling is strong and where it still needs work, which is
useful for judging when to adopt agent protocols in production.
```

---

## Consistency notes

- The demo video must show the app running; the pitch and feedback describe only
  behavior that the repository implements.
- Do not claim AWS or Open Source mini-challenge entry. The answers above decline
  both explicitly.
- Numbers quoted anywhere in the form (11 failing, $211,470 at risk, 9 tools, 37 + 28
  checks) match the current build.
- If other AI assistants were used during the build, add them to answer 16 rather
  than leaving the list incomplete.
