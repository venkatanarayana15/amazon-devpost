# DocOps Commander — Architecture

## Overview

DocOps Commander is an **MCP-native operations agent** for document-processing failures, presented as a simulated Alexa+ skill.

Two surfaces share one brain:

1. **MCP server** (`POST /mcp`, Streamable HTTP, protocol `2025-11-25`) — the product surface. Any MCP client (Claude Desktop, an Alexa+ skill runtime, a custom agent) can discover and call nine tools.
2. **Operator console** (`/`) — a light, Amazon-style single-page app that is itself a **real MCP client**: it performs `initialize` → `notifications/initialized` → `tools/list` → `tools/call` over the same endpoint. Nothing in the UI is hard-wired to private REST shortcuts.

The whole stack runs from one `requirements.txt` (FastAPI, uvicorn, pydantic). No npm, no node, no build step.

## Request path

```
MCP client (browser console or external agent)
        │  JSON-RPC 2.0 over POST /mcp
        ▼
mcp_server.py ── initialize / tools/list / tools/call
        │
        ▼
planner.py ── intent parsing → orchestration → narration → state payload
        │
        ├─ tools.py   ── diagnose_jobs → prioritise → build_batches → draft_action
        │                execute_action, recall_memory (each call appends a Trace step)
        ├─ domain.py  ── policy engine (first match wins) + case construction
        ├─ db.py      ── SQLite: decisions, messages, pending_actions, audit
        └─ llm.py     ── optional narration polish; deterministic no-op without a key
```

The console renders **only** the `structuredContent` returned by `tools/call`: cards, stats, plan, trace, memory, audit. The same payload is returned by `GET /api/state`, which exists for tests and for clients that prefer plain REST.

## MCP server

| Property | Value |
|---|---|
| Endpoint | `POST /mcp` (also `DELETE /mcp` to end a session) |
| Transport | Streamable HTTP, request/response (no server-initiated SSE stream) |
| Protocol version | `2025-11-25` (minimum required by the Alexa+ track) |
| Session | `Mcp-Session-Id` header issued on `initialize`, echoed on later calls |
| Capabilities | `tools.listChanged: false` |
| Notifications | `notifications/initialized` → `202` with empty body |
| Batch | JSON-RPC batches accepted |
| Errors | `-32700` parse, `-32600` invalid request, `-32601` unknown method, `-32602` invalid params/unknown tool, `-32603` tool failure |
| `GET /mcp` | `405` with `Allow: POST, DELETE` — correct for a POST-only Streamable HTTP server |

Every `tools/call` returns both human-readable `content` (one text block) and `structuredContent` (the full UI payload), with `isError` set for refusals such as double approval.

### Tool surface

| Tool | Arguments | Returns |
|---|---|---|
| `orchestrate` | `utterance`, optional `session_id` | Intent, narrated reply, cards, stats, trace, `pending_action`, options, memory, audit |
| `get_board` | — | Cards, stats, memory, audit |
| `diagnose_jobs` | optional `doc_type`, `vendor` | Failing cases grouped by pattern, scoped stats, trace |
| `prioritise_cases` | optional `limit` (1–50) | Ranked cases |
| `draft_resolution_plan` | optional `session_id` | Three-segment plan and `action_id`; **executes nothing**; records a `pending` decision |
| `approve_plan` | `action_id`, optional `actor` | Executed changes, new board state, summary |
| `reject_plan` | `action_id`, optional `reason` | Rejection result; nothing executes |
| `recall_decisions` | optional `vendor`, `query` | Matching decisions and full decision list |
| `reset_demo` | — | Synthetic dataset and memory restored |

`approve_plan` is the only path to execution. Re-approving a resolved action returns `isError` with “Action is already approved”, so a client cannot double-execute.

## Orchestration (the agent loop)

```
parse_intent()                 # resolve / diagnose / recall / hold / help
    ↓
diagnose_jobs()  → prioritise()  → build_batches()
    ↓                                   ↓
diagnose reply                  retry / human_review / escalate
    ↓                                   ↓
                                    draft_action() → pending_actions row + decision(outcome=pending)
                                            ↓
                                    approval (console modal or approve_plan)
                                            ↓
                                    execute_action() → jobs move, audit written,
                                    decision(outcome) updated, retries recover after 3 s
```

Ambiguity rule: if a resolve request names neither automation nor manual triage, the agent asks one question and offers two concrete options instead of guessing. Once answered, the follow-up is planned directly.

Intent fallback: utterances outside document-operations vocabulary are routed to **memory**, not to diagnosis — the agent never answers an unrelated question with an incident report, and never substitutes the nearest stored decision for a genuine miss.

## Policy engine (`domain.py`) — first match wins

1. `VENDOR_MISMATCH` → escalate (`POLICY-VENDOR-04`)
2. SLA already breached **and** enterprise vendor → escalate (`POLICY-SLA-08`)
3. `DUPLICATE_DOCUMENT` → human review (`POLICY-DUP-01`)
4. `MISSING_REQUIRED_FIELD` → human review (`POLICY-FIELD-03`)
5. amount ≥ $50,000 → human review (`POLICY-RISK-07`)
6. `LOW_OCR_CONFIDENCE`: confidence ≥ 0.65 **and** attempts < 3 **and** amount < $25,000 → auto-retry (`POLICY-OCR-05`), else human review (`POLICY-OCR-06`)
7. `UPSTREAM_TIMEOUT`, attempts < 3 → auto-retry (`POLICY-RETRY-01`)
8. `SCHEMA_VALIDATION_ERROR`, attempts < 3 → auto-retry (`POLICY-RETRY-02`)
9. fallback → human review

## Data

- `data/sample_jobs.json` — 17 synthetic jobs (`JOB-2041` … `JOB-2057`): vendor, document type, amount, received hours, SLA hours, failure code/confidence/attempts.
- Four document types: invoice, KYC form, bill of lading, proof of delivery.
- Nine fictional vendors, including Orion Logistics.
- Expected initial board: **11 failed**, **$211,470** at risk, 7 inside the SLA window, 1 breached.
- Expected plan: retry 5 (`JOB-2041/2042/2046/2048/2050`), review 3 (`JOB-2043/2045/2049`), escalate 3 (`JOB-2044/2047/2053`).
- SQLite tables: `decisions` (title, action types, jobs, vendors, policies, outcome, detail), `pending_actions` (segments, policies, status), `messages`, `audit`.

## Frontend (`frontend/`)

Vanilla ES2020, no framework, light Amazon-style palette (`#EDEFF0` page, `#FF9900` brand, `#FFD814` primary CTA). Explicitly not a dark dashboard.

- **`app.js`** — MCP client (`MCP_URL = '/mcp'`, `initialize` with `protocolVersion 2025-11-25`, session header, `tools/call`), plus rendering:
  - conversation with typing indicator, option chips, plain-language notes
  - inline plan card and a compact approval modal (segments, risk note, policy chips, approve/reject)
  - receipt state on the plan card after approval (`Approved` badge, executed summary)
  - four stat tiles, case list, memory list, audit list
  - recall highlighting: matched decisions get an amber rule and the Memory tab gets a dot until opened
  - 3-second recovery poll, toasts, trace disclosure, keyboard/ESC handling
- **`styles.css`** — light tokens, 2-column shell that fits a 1280×800-class viewport without page scroll, single column below 900 px, visible focus rings, `prefers-reduced-motion` support, thin custom scrollbars inside panes.
- **`index.html`** — semantic landmarks, `aria-live` conversation, `aria-pressed` tabs, focus-trapped modal, data-URI favicon, no external assets.

## HTTP API (for tests and REST clients)

| Path | Method | Notes |
|---|---|---|
| `/api/health` | GET | Liveness, job count, narration mode |
| `/api/meta` | GET | Policies, failure codes, thresholds, vendors, MCP endpoint and tool list |
| `/api/state` | GET | Cards, stats, memory, audit |
| `/api/command` | POST | `{"text": "…"}` → same payload as `orchestrate` |
| `/api/actions/{id}/approve` | POST | Same effect as `approve_plan` |
| `/api/actions/{id}/reject` | POST | Same effect as `reject_plan` |
| `/api/reset` | POST | Same effect as `reset_demo` |
| `/mcp` | POST/DELETE/GET | MCP transport (GET returns 405 by design) |

## Deterministic narration

Without `LLM_API_KEY`, every reply is produced by pure Python functions, so the demo is reproducible and inspectable. `llm.polish()` is a no-op in that mode. With a key, narration is polished but the facts still come from the tool layer.

## Verification

```bash
python scripts/smoke_test.py   # 37 HTTP assertions: scoped diagnosis, approval gate,
                               # plural copy, recovery, memory, honest recall miss, reset
python scripts/mcp_smoke.py    # 28 MCP assertions: handshake, session header, 9 tools,
                               # schemas, approval gate, double-approval refusal,
                               # protocol errors, GET 405, reset
```

Both are stdlib-only and exit non-zero on failure.

## Design decisions

- **MCP first, REST second.** The console proves the tool contract an external agent would use; REST exists for tests and curl.
- **Approval is a tool, not a UI convention.** `draft_resolution_plan` cannot execute; `approve_plan` is the only executor. The gate survives any client.
- **Memory is written at draft time.** A plan becomes a `pending` decision the moment it is drafted, so approval and rejection both have a record even if the client disconnects.
- **No fabricated recall.** If nothing matches, the agent says so rather than presenting the latest decision as an answer.
- **Deterministic by default.** No API key, no network dependency, identical behaviour on every run.
