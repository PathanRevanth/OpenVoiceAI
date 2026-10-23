# Voice-First AI

A **voice-first agentic control plane** built on **Pydantic AI v2** — one framework for the
orchestrator, every specialist agent, and the governance layer around them.

```
        EXPERIENCE (Next.js 16 + Better Auth)          CONTROL PLANE (FastAPI)
   ┌──────────────────────────────────────────┐   ┌─────────────────────────────────┐
   │  Voice UI (orb, mic→PCM, TTS fallback)   │   │  Identity (Better Auth JWT/JWKS)│
   │  Chat UI (SSE streaming, approvals)      │◄──┤  Policy (RBAC, guardrails)      │
   │  Console (agents, audit, usage, MCP)     │   │  Audit (hash-chained) · Usage   │
   └──────────────────────────────────────────┘   │  Approvals (human-in-the-loop)  │
                                                  │  Registries (agents/skills/MCP) │
                                                  └────────────────┬────────────────┘
                                                                   │
                                                    ┌──────────────▼───────────────┐
                                                    │   ORCHESTRATOR (Pydantic AI) │
                                                    │  planning · delegation ·     │
                                                    │  skills · memory · spend     │
                                                    └──────────────┬───────────────┘
                                                   delegate_task   │  (harness SubAgents)
                          ┌───────────────┬────────────────┬───────┴──────────┐
                          ▼               ▼                ▼                  ▼
                     GitHub Agent    Slack Agent    MongoDB Agent      Research Agent
                     (SKILL.md +     (SKILL.md +    (SKILL.md +        (SKILL.md +
                      tools/MCP)      tools/MCP)     tools/MCP)         WebSearch)
```

## Tech stack (latest stable, August 2026)

| Layer          | Technology                                              |
| -------------- | ------------------------------------------------------- |
| Agents         | [Pydantic AI](https://pypi.org/project/pydantic-ai/) 2.35 + [Harness](https://pypi.org/project/pydantic-ai-harness/) 0.27 + [Skills](https://pypi.org/project/pydantic-ai-skills/) 1.4 |
| Voice          | `pydantic_ai.realtime` (OpenAI gpt-realtime / Gemini Live / Grok Voice) |
| API            | FastAPI 0.141, SSE streaming, WebSocket voice bridge     |
| Persistence    | SQLAlchemy 2 async (SQLite dev / PostgreSQL prod)        |
| Observability  | Logfire 4.41 (OpenTelemetry)                             |
| Frontend       | Next.js 16.3, React 19, Tailwind 4                       |
| Auth           | Better Auth 1.7 (email/password, admin plugin RBAC, JWKS) |

## Quickstart (zero API keys needed)

The platform runs in **demo mode** out of the box: a scripted model answers so you can
exercise the full UI, delegation surface, guardrails, approvals, and audit — then add a
key to go live.

**1. Backend** (Python 3.13, [uv](https://docs.astral.sh/uv/)):

```bash
cd backend
uv sync
uv run uvicorn voicefirst.main:app --port 8000
# health: http://localhost:8000/health  ·  docs: http://localhost:8000/docs
```

**2. Frontend** (Node 22, pnpm):

```bash
cd frontend
pnpm install
cp .env.example .env.local   # optional in dev; defaults are sane
pnpm dlx @better-auth/cli@latest migrate --config src/lib/auth.ts -y
pnpm dev                     # http://localhost:3000
```

**3. Sign up** at `http://localhost:3000/login`. To promote the first account to admin:

```bash
cd frontend && node -e "
const Database = require('better-sqlite3');
new Database('voicefirst.auth.db')
  .prepare(\"UPDATE user SET role='admin' WHERE email=?\")
  .run('you@company.com');
console.log('promoted');"
```

**4. Talk to it** — tap the orb (browser STT/TTS loop) or type. Try:

- *"Search GitHub for pydantic-ai"* → routes to the GitHub specialist
- *"Aggregate MRR by region"* → MongoDB specialist
- *"Research the latest on Model Context Protocol"* → Research specialist

## Platform features

Beyond the assistant itself, the control plane ships:

- **Per-agent API + webhooks** — every active agent is invokable over its own
  endpoint with `vf_ak_` keys (Agents page → Access), plus a public manifest.
- **Triggers** — schedule agent runs on an interval or daily (UTC); results
  POST to your webhook and land in Observability.
- **Knowledge (RAG)** — tenant document library; chunked, embedded via your
  connected provider (keyword fallback offline), searchable by the
  orchestrator's `knowledge_search` tool.
- **Agent Studio** — edit each agent's SKILL.md live; saves are versioned and
  hot-reload the fleet. Includes a red-team scanner (10 adversarial cases,
  resilience score).
- **Evals** — datasets of input/expected-pattern cases with deterministic
  grading and pass-rate history per agent.
- **Chains** — sequential multi-agent workflows with `{{prev}}` /
  `{{step_N}}` output templating.
- **LLM Connections** — bring-your-own keys, encrypted at rest, per purpose
  (agents / voice), with tiered latest-model catalogs and optional model
  fallback chains.
- **Human-approved plans** — for multi-step requests the orchestrator drafts a
  plan (harness `Planning`); the run pauses and the chat renders it as an
  AI-Elements-style plan card. Nothing delegates until the operator approves —
  declining sends the model back to revise. Live step statuses stream into the
  card (`plan.proposed` / `plan.task` SSE events). Disable with
  `VOICEFIRST_REQUIRE_PLAN_APPROVAL=false`.
- **Approvals** — mutating tool calls pause for human sign-off; decision
  webhooks notify external systems.
- **Observability** — run traces with usage/cost, a live SSE event stream,
  and a hash-chained audit log.
- **Budget alerts** — daily spend is checked per run; 80% / 100% crossings
  emit warnings surfaced live in the UI.
- **Personal access tokens** — `vf_pat_...` tokens to script the platform
  API with your identity and role.
- **Multimodal chat** — attach images/PDFs to messages for vision-capable
  models.
- **Persistent memory** — the orchestrator remembers user context across
  conversations (per-user namespaced store).

## Enterprise feature pack

- **Trace replay** — any stored run can be regenerated from its recorded
  message history, optionally on a different model (`POST
  /api/runs/{id}/replay`, plus a Replay panel in the Observability drawer).
  This is the quality loop: reproduce, fix, prove the fix.
- **PII redaction guardrail** — compliance-grade output redaction for emails,
  phone numbers, SSNs, IP addresses, and Luhn-validated card numbers
  (typed markers like `[REDACTED-EMAIL]`). Toggle at runtime via
  `PATCH /api/policies` (`redact_pii`) — no restart.
- **Compliance exports** — the audit trail (with hash chain fields) and
  per-run usage export as signed CSV/JSON for regulators and chargeback
  (`GET /api/audit/export`, `GET /api/usage/export`; ops+, admin sees all
  tenants).
- **Per-user cost attribution** — usage summaries include a per-user
  breakdown; the Usage page renders the chargeback/showback table.
- **Voice analytics** — every realtime session (browser or Twilio) records
  time-to-first-word, turns, barge-ins, duration, and outcome
  (`voice_sessions` table; `GET /api/voice/analytics`). The Observability
  page shows the voice quality dashboard.
- **Event subscriptions** — first-class webhook fan-out: subscribe any
  endpoint to event patterns (`run.*`, `approval.*`, `budget.warning`, `*`),
  per-subscription signing secret (encrypted at rest), automatic retry with
  backoff, and dead-lettering after 10 consecutive failures. Settings →
  Event subscriptions, with one-click signed test delivery.
- **A2A agent card** — standard Agent2Agent discovery at
  `/.well-known/agent-card.json` (protocol 0.3.0, all enabled agents as
  skills) alongside the A2A JSON-RPC `message/send` endpoint.
- **Native Pydantic AI 2.35 runtime features**:
  - `UsageLimits(total_tokens_limit=…)` enforced on every run path
    (chat, resume, replay, webhooks, triggers).
  - `CancellationToken` wired to run cancellation — cancel unwinds the
    agent graph cleanly (`RunCancelled` → run marked `cancelled`).
  - `limit_model_concurrency` — a shared provider-pool cap
    (`VOICEFIRST_MODEL_CONCURRENCY_LIMIT`, default 16) across all agents,
    plus per-agent run ceilings (`VOICEFIRST_AGENT_MAX_CONCURRENCY`).
  - **Native-or-local web tools** — the Research agent upgrades to the
    provider's native web search/fetch (OpenAI/Google) automatically and
    falls back to keyless local tools everywhere else
    (`VOICEFIRST_NATIVE_WEB_TOOLS`, default on). Three search paths total:
    native → ddgs → HTML scraper.
  - **Tool output limits** — tool returns over 20k chars are truncated
    (head+tail) before entering history, so one huge payload can't bloat
    every later model request.
  - **FallbackModel chains** — LLM Connections with a same-provider
    fallback model resolve to a pydantic-ai `FallbackModel`: the fallback
    takes over automatically on provider errors and rate limits.
  - **Cross-conversation memory** — the orchestrator's `conversation_search`
    tool finds past exchanges for the calling user (tenant-scoped), so
    context survives across sessions.
  - **Thinking control** — `VOICEFIRST_THINKING_LEVEL`
    (off/minimal/low/medium/high) enables provider-portable reasoning.
  - **Native OTel instrumentation** — `logfire.instrument_pydantic_ai()`
    runs at boot when a Logfire token is set: spans for every model
    request, tool call, and delegation with zero code changes.
  - The Research agent uses the official keyless DuckDuckGo tool (`ddgs`)
    on top of its HTML-scraper fallback.

### Harness capability pack (pydantic-ai-harness 0.29)

Every agent now runs the full production capability stack:

- **Prompt-injection defender** — tool results (GitHub readmes, Slack
  messages, web pages) are scanned for injected instructions before the
  model sees them; high-risk results are blocked. Complements the input
  guardrail, which only covers user input.
- **Step persistence** — every run writes a durable per-step event log with
  continuable snapshots and a tool-effect ledger (`voicefirst_steps.db`):
  crash-resumable runs and an exact "what did this run touch?" record.
- **System reminders** — cache-safe re-injection of behavioral guidance
  (plan discipline for the orchestrator, citation discipline for research).
- **Cache-bust monitor** — warns when the prompt-cache prefix collapses,
  the silent token-cost multiplier on long agent runs.
- **Media offloading** — attachment parts over 512 KB are externalized to a
  content-addressed store instead of riding along in stored history.
- **Code mode** — the GitHub/MongoDB/Research specialists can route tool
  results through sandboxed Python (monty): analysis and reshaping happen
  in-code instead of extra model round-trips.
- **Dynamic workflows** — the orchestrator gained `run_workflow`: it writes
  a sandboxed script that fans out, chains, or votes across specialists in
  one governed run (bounded at 20 agent calls).
- **Advisor** — the orchestrator can request a second-model critique before
  risky decisions (max 2 uses/run, `VOICEFIRST_ADVISOR_*`).
- **Capability creation (Agent Studio)** — admin-gated: when enabled, the
  orchestrator may draft new capability files for admin review.
- **Spend limits everywhere** — specialists now enforce the same per-run and
  daily budgets as the orchestrator (previously only the orchestrator did).

## Production hardening (built in)

- **Health probes** — `GET /health` (liveness) and `GET /ready` (DB ping +
  trigger-scheduler liveness) for Kubernetes/Docker orchestration.
- **Prometheus metrics** — `GET /metrics` exposes request counters and
  latency histograms per route (disable with `VOICEFIRST_METRICS_ENABLED=false`).
- **Rate limiting** — token-bucket per caller (bearer-token hash or client
  IP) on `/api/*`, with `429` + `Retry-After`; probe paths are exempt.
  Tune with `VOICEFIRST_RATE_LIMIT_PER_MINUTE`.
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, and `Permissions-Policy` on every response.
- **Request IDs + access logs** — every request gets `X-Request-ID`
  (propagated from the edge when present); access logs are plain text or
  JSON (`VOICEFIRST_LOG_FORMAT=json`) for log shippers. Unhandled errors
  return a JSON 500 carrying the request id — never a stack trace.
- **Signed webhooks** — set `VOICEFIRST_WEBHOOK_SECRET` and every outbound
  webhook (approvals, trigger results) is signed with
  `X-VoiceFirst-Signature: sha256=<hmac>` (GitHub/Stripe-style scheme).
- **Input budgets** — chat attachments are size-capped and base64-validated
  at the schema layer; message lengths bounded per schema.
- **Run cancellation** — `POST /api/runs/{id}/cancel` aborts in-flight
  streaming runs and marks them `cancelled` in the run history.
- **Hardened containers** — backend image runs as a non-root user with a
  container healthcheck and `--proxy-headers`; compose services use
  `restart: unless-stopped` and gate on dependency health.
- **Startup config validation** — boot logs loud warnings for a default
  `VOICEFIRST_SECRET_KEY`, disabled auth, or SQLite storage.

### Production checklist

1. `VOICEFIRST_SECRET_KEY` — strong random value (encrypts stored provider keys).
2. `VOICEFIRST_AUTH_MODE=betterauth` + `VOICEFIRST_AUTH_JWKS_URL` / `_BASE_URL`.
3. PostgreSQL DSN in `VOICEFIRST_DATABASE_URL` (SQLite is dev-only).
4. `VOICEFIRST_WEBHOOK_SECRET` if anything consumes your webhooks.
5. Terminate TLS at your proxy; keep `VOICEFIRST_CORS_ORIGINS` pinned to your
   frontend origin(s).
6. Scrape `/metrics` and alert on `/ready`.

## Going live

1. **LLM Connections (bring your own keys)** — sign in, open **Platform → LLM
   Connections**, and add a key per purpose: *Agents* (OpenAI / Anthropic / Google chat
   models) and *Voice* (OpenAI realtime / Google Gemini Live). Every key is tested live
   against its provider on save, then **encrypted at rest** (`VOICEFIRST_SECRET_KEY`,
   AES via Fernet) and scoped to your user. Keys are never returned to the browser and
   never stored client-side — runs resolve the model from your active connection.
2. Or configure platform-wide defaults in `backend/.env`: `VOICEFIRST_DEFAULT_MODEL`
   and the matching provider key (user connections take precedence).
3. For server-side speech-to-speech set `VOICEFIRST_REALTIME_MODEL` + key, or just add
   a Voice connection in the UI (it wins).
4. Optional integrations: `VOICEFIRST_GITHUB_TOKEN`, `VOICEFIRST_SLACK_BOT_TOKEN`,
   `VOICEFIRST_MONGODB_URI`, remote MCP servers via `VOICEFIRST_GITHUB_MCP_URL` etc.
   For GitHub you can skip the static token: set `VOICEFIRST_GITHUB_CLIENT_ID` /
   `VOICEFIRST_GITHUB_CLIENT_SECRET` (an OAuth app) and each operator connects their
   own account under **Settings → Integrations → Connect GitHub** — the exchanged
   token is stored server-side, applied live, and survives restarts.
5. Optional tracing: `VOICEFIRST_LOGFIRE_TOKEN`.

## Safety model (enforced in code, not prompts)

| Layer          | Mechanism                                                          |
| -------------- | ------------------------------------------------------------------ |
| Identity       | Better Auth JWT (EdDSA) verified against JWKS; session-token fallback |
| RBAC           | `viewer < ops < admin`; destructive tools require admin            |
| Tool approval  | Mutating tools (`requires_approval=True`) pause the run; the orchestrator surfaces `DeferredToolRequests`; operators approve/deny in the UI and the run resumes with `DeferredToolResults` |
| Guardrails     | Input (prompt-injection patterns), output (secret redaction), tool-call RBAC (harness guardrails) |
| Spend limits   | Per-day USD + per-run token budgets (harness `SpendLimits`)         |
| Audit          | Append-only, hash-chained log with `/api/audit/verify`             |

## Docker

```bash
cp .env.example .env   # fill in what you need
docker compose up --build
```

## Project layout

```
backend/
  src/voicefirst/
    agents/        factory (orchestrator + specialists), tools, deps, runtime
    api/           routes, chat SSE service, voice WebSocket, schemas
    control_plane/ auth, policy, registry, approvals, audit, usage, events
    db/            async SQLAlchemy models + session
    skills/        SKILL.md per agent (orchestrator, github, slack, mongodb, research)
  tests/           pytest suite (demo model, TestModel approval-pause test)
frontend/
  src/app/         chat (voice-first), console, login, Better Auth route
  src/components/  auth context, top nav, voice orb
  src/lib/         Better Auth server/client, backend API client (JWT)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the control-plane design.
