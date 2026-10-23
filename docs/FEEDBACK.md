# Product feedback

Feedback from building DocOps Commander for the Amazon AppDev 2026 Alexa+ track. Every note below comes from something that actually happened in this repository, not from a template.

## Tool: MCP (spec 2025-11-25, Streamable HTTP)

**Worked well.** The tool contract is the cleanest agent surface I have integrated. `tools/list` with JSON Schema, `tools/call` returning both `content` and `structuredContent`, and JSON-RPC error codes gave one predictable path for both the console and external agents. `structuredContent` meant the UI never needed a private API: the same payload renders the board, the plan and the memory panel.

**Needs work.** The transport section is written around servers that stream. For a request/response server the required behaviour of `GET /mcp` is easy to misread; we settled on `405` with `Allow: POST, DELETE`, and `DELETE /mcp` for session teardown, but the spec would benefit from a short "request/response only" profile. Header casing was also a sharp edge: `Mcp-Session-Id` is lowercased on the wire by common HTTP stacks, so a naive case-sensitive lookup in a client silently starts a new session on every call. The spec should state that clients must compare header names case-insensitively.

**Onboarding.** Hand-writing the handshake took an afternoon: `initialize` → `notifications/initialized` → `tools/list` → `tools/call` is four small JSON objects. No SDK was required, which made the server auditable end to end.

**Would build again.** Yes. A tool surface is a better product boundary than a REST API for an agent, because discovery, descriptions and error semantics travel with the tools.

## Tool: FastAPI + uvicorn

**Worked well.** Pydantic models kept the tool payload stable, static hosting and the JSON API ran from one process, and the whole install stayed at three packages. `python -m uvicorn backend.main:app` was a reliable single command on Windows with no build step.

**Needs work.** Mount order is a real footgun: mounting `StaticFiles` at `/` before including the MCP router makes `/mcp` return the SPA instead of a 405/JSON-RPC response. This is documented, but the failure mode is silent. A warning when a mount shadows an existing route would save an hour.

**Onboarding.** Under ten minutes from `pip install` to a working JSON endpoint. Auto-generated `/docs` was useful for sanity-checking the REST surface while the MCP transport was still being written.

**Would build again.** Yes for prototypes and small services; the single-process deployment is exactly right for a hackathon demo.

## Tool: SQLite (standard library)

**Worked well.** Durable decision memory with zero services. Recording a decision at draft time, flipping its outcome on approval, and querying it for recall took a few dozen lines. The same file also holds the audit trail and pending actions, so a reset is one transaction.

**Needs work.** The default journal mode plus a 3-second recovery poll can produce `database is locked` under concurrent writes if a request arrives during a write. We keep writes short and single-threaded in practice, but a documented WAL recommendation for polling demos would help.

**Onboarding.** Immediate. No migrations needed for a demo schema; `sqlite3` and a `CREATE TABLE IF NOT EXISTS` was enough.

**Would build again.** Yes for a single-node demo, with WAL enabled from the start.

## Tool: Vanilla JavaScript + Fetch (the console's MCP client)

**Worked well.** Streamable HTTP is a `POST` with JSON, so the whole client is a
forty-line `fetch` wrapper: `initialize`, `notifications/initialized`, `tools/list`,
`tools/call`. No SDK, no bundle, no build step — the page is auditable with View
Source, and the same `structuredContent` payload renders the board, the plan and the
memory panel. Same-origin hosting meant no CORS configuration at all.

**Needs work.** There is no browser-side MCP client to lean on, so session handling,
JSON-RPC error decoding, notification vs. request framing and notification ordering
are all hand-rolled. The session header casing trap described above bites here first.
A small reference client (or a documented fetch pattern) would remove an afternoon of
re-deriving the same four calls.

**Onboarding.** A few hours from an empty `app.js` to a conforming client, mostly
spent on the handshake rather than the UI.

**Would build again.** Yes — the page is the strongest proof that the tool contract
works for an arbitrary client, not just for our own tests.

## Tool: Python standard library HTTP client (verification scripts)

**Worked well.** `urllib.request` was sufficient to write two conformance suites with no dependencies at all, which keeps `requirements.txt` honest and lets a judge verify the MCP server without installing anything.

**Needs work.** Header handling is case-sensitive when you convert `HTTPMessage` to a plain dict, which is how the session-header bug above surfaced. That is a useful lesson, not a defect.

**Onboarding.** None — it ships with Python.

**Would build again.** Yes for smoke tests; a real client would use an SDK.
