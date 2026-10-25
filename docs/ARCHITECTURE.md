# Architecture

## Design principles

1. **One agent framework end to end.** The orchestrator and every specialist is a Pydantic AI
   v2 `Agent`. No second agent framework is introduced.
2. **Governance lives in the control plane, not in prompts.** Approval requirements, RBAC,
   spend limits, and audit trails are enforced in code around the agent loop.
3. **Context is budgeted.** The orchestrator's only tool is `delegate_task`; specialists own
   their domain tools. Skills (SKILL.md) load on demand; compaction clears old tool results.
4. **Identity comes from the experience layer.** Better Auth owns login; the control plane
   verifies JWTs via JWKS and maps the `role` claim onto RBAC. No parallel user store.

## Layers

### Experience (frontend/)
- **Login** — Better Auth email/password; roles `admin | ops | viewer` via the admin plugin.
- **Assistant (`/chat`)** — voice orb + chat. Voice: when a realtime provider key exists,
  mic PCM streams over WebSocket into a `pydantic_ai.realtime` speech-to-speech session and
  assistant audio streams back; otherwise browser SpeechRecognition + SpeechSynthesis drive
  the same socket protocol with orchestrator text turns. Text: SSE streaming with tool
  activity and inline approval cards.
- **Console (`/console`)** — pending approvals, agent/skill/MCP registries, usage stats,
  and the hash-chained audit log.

### Control plane (backend control_plane/)
| Service          | Responsibility |
| ---------------- | -------------- |
| `auth.py`        | Verifies Better Auth JWTs (EdDSA via JWKS) or session tokens; maps `role` → `PolicyContext` |
| `policy.py`      | Tool risk classification (`read`/`mutating`/`destructive`), role checks, guardrail factories (injection blocker, secret redactor, tool RBAC) |
| `registry.py`    | Agents (code-defined + DB status), skills (SKILL.md discovery), MCP servers (URL config → `MCPToolset` construction) |
| `approvals.py`   | Records paused mutating tool calls; approve/deny API; builds `DeferredToolResults` for resume |
| `audit.py`       | Append-only hash chain (`sha256(prev + canonical entry)`), verification endpoint |
| `usage.py`       | Per-run token/cost capture; 30-day aggregates by agent and day |
| `events.py`      | In-process pub/sub (SSE fan-out), optional Redis Streams mirror |

### Orchestration (backend agents/)
- **`factory.py`** — builds specialists (domain tools + SKILL.md instructions + guardrails +
  compaction) and the orchestrator (`delegate_task` via harness `SubAgents`, harness
  `Planning`, `SpendLimits` budgets, `Skills` on-demand loading, `output_type=[str,
  DeferredToolRequests]` so approval pauses surface as run output).
- **`runtime.py`** — composition root wiring DB, events, audit, usage, approvals,
  registries, identity, and the agent instances; seeds the registries on boot.
- **`tools/`** — GitHub (REST), Slack (Web API), MongoDB (driver or seeded demo store),
  Research (DuckDuckGo + fetch). Mutating tools set `requires_approval=True`. When MCP
  server URLs are configured, the registries attach MCP toolsets instead of/alongside
  built-ins.

### Data model
`agents`, `skills`, `mcp_servers` (registries) · `conversations`, `runs`, `messages`
(experience) · `approvals` (human-in-the-loop, includes serialized paused-run state) ·
`audit_log` (hash chain) · `usage` (tokens/cost).

## Run lifecycle

```
chat request ──► conversation upsert ──► run row (status=running)
      │
      ▼
orchestrator.iter(user prompt, history)
      │  SSE: text.delta / tool.started / tool.finished
      ├──► mutating tool call? ──► approvals recorded, state persisted,
      │        run.status=awaiting_approval, SSE approval.requested
      │        … operator decides …
      │        POST /api/runs/{id}/resume ──► DeferredToolResults ──► run continues
      ▼
run completed: usage recorded, messages persisted, SSE run.finished
```

## Voice modes

Voice and chat models are selected separately. Anthropic (and other chat providers)
never speak; Gemini Live / OpenAI Realtime never run specialists.

| Mode | Path |
| ---- | ---- |
| `realtime` | Mic PCM → voice companion (`pydantic_ai.realtime`, Gemini Live) for live speech. Final user transcripts are handed to the **chat orchestrator** (Anthropic by default) on the same conversation. Tool/artifact events stream to the UI; spoken cues are injected back into the Live session so the caller hears progress. A new utterance cancels/replaces an in-flight run (live corrections: "make it white"). |
| `browser-fallback` | Browser SpeechRecognition → WebSocket `text` turn → orchestrator run → transcript; SpeechSynthesis renders the reply locally |

The conversational layer and the work layer share one conversation, guardrails, and audit.

## Extending

- **New specialist**: add `skills/<name>/SKILL.md`, a tool module, one entry in
  `_SPECIALIST_TOOL_BUILDERS`, and register an MCP URL if one exists.
- **New MCP server**: `POST /api/mcp` (admin) or set `VOICEFIRST_*_MCP_URL`.
- **Stricter safety**: lower `VOICEFIRST_MUTATING_MIN_ROLE`, or set
  `VOICEFIRST_REQUIRE_APPROVAL_FOR_MUTATIONS=false` to rely on role checks only.
