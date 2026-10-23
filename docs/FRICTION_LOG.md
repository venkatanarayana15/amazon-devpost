# Friction log

Three issues hit while building DocOps Commander. Each is something a reference example or a spec sentence would have prevented.

## 1. Streamable HTTP is described for streaming servers

- **Task:** expose the agent over MCP `2025-11-25` without operating an SSE stream.
- **Expected:** a clear statement of what a request/response-only server must return for `GET /mcp`.
- **Actual:** the transport section is written around servers that push messages. It was not obvious whether `GET` should return `405`, an empty SSE stream, or `200` with no events. Returning the SPA (because `StaticFiles` was mounted at `/`) made the mistake silent.
- **Workaround:** `POST /mcp` for JSON-RPC, `DELETE /mcp` for session teardown, `GET /mcp` → `405` with `Allow: POST, DELETE`, documented in `docs/ARCHITECTURE.md` and asserted by `scripts/mcp_smoke.py`.
- **Suggestion:** add a normative "request/response only" profile to the transport documentation, with the exact status codes for `GET` and `DELETE`.
- **Severity:** Medium — the failure looks like a routing bug, not a protocol choice.

## 2. Human approval has no first-class primitive

- **Task:** make sure a tool call cannot move 11 production jobs without a human saying yes.
- **Expected:** guidance for a "requires confirmation" tool result that clients render natively.
- **Actual:** until elicitation is broadly supported, approval has to be modelled as two tools (`draft_resolution_plan`, `approve_plan`) plus client-side state. Deciding what happens on timeout, double approval, or a disconnected client was left to us.
- **Workaround:** drafting records a `pending` decision in SQLite; `approve_plan` is the only executor; a second approval returns `isError: "Action is already approved"`; rejection is recorded as durably as approval; the console renders a modal and an "Approved" receipt. Verified in both smoke suites.
- **Suggestion:** publish a reference pattern for approval-gated tools: the two-tool shape, the pending-state record, the double-execution guard, and a minimal UI sketch.
- **Severity:** Medium — every agent that touches money or customer records will re-invent this.

## 3. Session header casing breaks naive clients silently

- **Task:** keep one MCP session across `initialize`, `tools/list` and `tools/call`.
- **Expected:** header names are case-insensitive, so `Mcp-Session-Id` should be readable as sent.
- **Actual:** common HTTP stacks lowercase header names on the wire. A client that converts response headers to a plain dict and looks up `Mcp-Session-Id` gets `None`, sends no session header, and the server — correctly — creates a fresh session for every call. Nothing errors; conversation context quietly disappears.
- **Workaround:** case-insensitive header lookup in `scripts/mcp_smoke.py`; the suite now asserts that `initialize` issues a session id and that later calls reuse it.
- **Suggestion:** say explicitly in the transport docs that clients must compare header names case-insensitively (and show lowercase names in examples).
- **Severity:** Low to reproduce, high to notice — it produces no error at all.
