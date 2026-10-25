# Deployment

The app runs on three managed hosts:

| Piece | Host | URL |
| --- | --- | --- |
| Next.js experience layer | Vercel (project `openvoiceai`, root `frontend`) | https://openvoiceai.vercel.app |
| FastAPI control plane + voice WebSocket | Render (Docker, free web service) | `https://openvoiceai-backend.onrender.com` |
| Auth database | Neon Postgres `neondb` (Vercel Marketplace, free plan) | via `DATABASE_URL` |
| Backend database | Neon Postgres `voicefirst` (same Neon project) | via `VOICEFIRST_DATABASE_URL` |

Vercel deploys automatically on every push to `master`. Render does too, once
the **Render GitHub App** is installed for `PathanRevanth/OpenVoiceAI`; Render
receives push events through that app, not a classic repository webhook.

The Render service uses `rootDir: backend`, so it rebuilds only when a push
changes files under `backend/`. Frontend-, docs-, and blueprint-only commits are
intentionally skipped: restarting the free backend for unrelated changes would
force a cold start. Backend data now lives in Postgres, so skipping those
deploys no longer risks losing anything.

## Frontend (Vercel)

- Root directory: `frontend`
- Framework preset: Next.js
- Build: `pnpm build` (Vercel auto-detects `pnpm-lock.yaml`)
- Environment variables:
  - `BETTER_AUTH_SECRET` — 32+ random bytes, stable across deploys
  - `BETTER_AUTH_URL` — `https://openvoiceai.vercel.app`
  - `NEXT_PUBLIC_API_URL` — the Render service URL (no trailing slash)
  - `DATABASE_URL` — injected by the Neon integration (Production + Preview)

`frontend/src/lib/auth.ts` uses Postgres when `DATABASE_URL` is present and
falls back to local SQLite otherwise, so `pnpm dev` keeps working with no
external services.

To redeploy after changing an env var:

```bash
vercel --prod --yes
```

## Backend (Render)

`render.yaml` at the repo root defines a free Docker web service:

- Build context: `backend`
- Dockerfile: `backend/Dockerfile`
- Health check: `/health`
- Auto-deploy: commits on `master` that change `backend/`

Deploy it once:

1. Open https://render.com/deploy?repo=https://github.com/PathanRevanth/OpenVoiceAI
2. Sign in with GitHub as **PathanRevanth**.
3. Enter the two secrets when prompted:
   - `VOICEFIRST_ANTHROPIC_API_KEY`
   - `VOICEFIRST_GOOGLE_API_KEY`
4. Apply the blueprint. The first build takes a few minutes.

The blueprint sets `VOICEFIRST_AUTH_MODE=betterauth`, the JWKS URL
(`https://openvoiceai.vercel.app/api/auth/jwks`), CORS for the Vercel origin,
and generates `VOICEFIRST_SECRET_KEY`.

`VOICEFIRST_DATABASE_URL` is deliberately not in `render.yaml`: it contains a
credential, so it is set on the service instead (dashboard or API). The value is
the Neon **unpooled** endpoint with the async driver and SSL flag:

```text
postgresql+asyncpg://<user>:<password>@<endpoint>.c-14.us-east-1.aws.neon.tech/voicefirst?ssl=require
```

Use the direct endpoint rather than the `-pooler` host for SQLAlchemy/asyncpg.
Changing an env var does **not** trigger a deploy while
`autoDeployTrigger: commit` is set, so trigger one manually afterwards.

### Auto-deploy

The service has `autoDeployTrigger: commit` and tracks `master`. It also needs
the Render GitHub App to be authorized for the repository:

1. Open https://github.com/apps/render/installations/new.
2. Sign in as **PathanRevanth**.
3. Grant access to `PathanRevanth/OpenVoiceAI` (all repositories also works).
4. Push a commit that changes `backend/`. The Render deploy list should show
   `trigger: new_commit` within a minute. Pushes that only touch the frontend
   or docs are skipped because of `rootDir: backend`.

If pushes still do not appear, confirm the app is installed at
https://github.com/settings/installations and that the repository is included.
A classic repo webhook is not used by Render and will not fix auto-deploy.
Blueprint-only changes such as `render.yaml` do not trigger a deploy either;
apply the blueprint or trigger a deploy manually.

### Output limits

Long deliverables are capped at 32,768 output tokens normally and 65,536 for
thinking runs. The previous 16,384 cap cut a token-dense HTML/SVG page off
mid-tag at 35,072 characters. With the higher cap, the same request completed
locally at 48,250 characters (20,206 output tokens) and in production at 59,709
characters (24,872 output tokens, $0.133332), both with a closed `</html>` and
closing fence. The persisted assistant message matched the streamed text
exactly.

### Free-tier note

Render free web services sleep after ~15 minutes without traffic. The first
request after sleep takes 30–60 seconds. Voice sessions keep the service awake
while connected, and the app shell polls every 12 seconds while open. The login
page also fires a one-shot `/health` request on mount, so the instance starts
waking while the visitor is typing. For an always-warm demo, add an uptime
monitor that pings `https://<service>.onrender.com/health` every 10 minutes.

### Persistence

Backend history is stored in Neon Postgres, not the container filesystem. The
control plane uses the `voicefirst` database in the same Neon project as auth;
Better Auth stays in `neondb`. Both are free-plan Neon databases, so they
survive Render redeploys, restarts, and free-tier sleep.

On startup, `Database.create_all()` creates any missing tables (21 today) and
adds model columns that are not yet present. The schema-evolution probe is
dialect-aware and each `ALTER TABLE` runs in a savepoint, so a failed change on
PostgreSQL cannot abort the surrounding transaction and roll back `create_all`.

Verified on 2026-10-25: a chat turn created conversation
`conv_46f2d959cbc34092` with two messages, a full Render rebuild
(`dep-dbes0oss728c73d8flhg`) came up `live`, and the conversation and messages
were still returned by `/api/conversations/{id}` and present in Neon.

The old SQLite file at `/data/voicefirst.db` is **not** migrated. Conversations
created before the switch remain in the retired SQLite volume; only data created
after the Postgres cutover is durable.

## Demo account

A dedicated demo admin is provisioned in production:

- Email: `demo@openvoiceai.app`
- Role: `admin` (Better Auth `role` column in `neondb.public."user"`)
- Password: shared privately with the project owner; never commit it here

The account is email-verified and can use chat, voice, projects, approvals, and
Agent Studio. To rotate it, sign in, change the password, or update the role in
Neon.

## Post-deploy checklist

```bash
# frontend
curl -i https://openvoiceai.vercel.app/login

# backend
curl -i https://<service>.onrender.com/health

# auth -> JWT -> JWKS
curl -i https://openvoiceai.vercel.app/api/auth/jwks
```

Then open the site and sign in with the demo account (or create a new one), and
start a voice session. Browser mic access requires HTTPS (Vercel provides it).

## Production verification (2026-10-25)

Checked against the live deployment, not just locally:

- Vercel and Render both auto-deployed the same push: commit `dbfe667`
  appeared in the Render deploy list with `trigger: new_commit` and reached
  `live`, while Vercel built and aliased the frontend.
- Vercel sign-up and sign-in return `200`; `/api/auth/token` issues an EdDSA
  JWT and `/api/auth/jwks` serves the matching key.
- The Render control plane accepts that JWT (`/api/conversations` returns
  `200`), so the Vercel → Render auth chain works end to end.
- Text chat streamed a complete SSE answer through `/api/chat` with no browser
  console errors.
- Voice connected over `wss://openvoiceai-backend.onrender.com/api/voice/ws`
  in `realtime` mode with `google:gemini-3.1-flash-live-preview`, 16 kHz input
  and 24 kHz output. A spoken turn returned transcripts plus synthesized
  speech, and the backend logged first audio 1.15 s after the caller stopped
  speaking.
- The long-artifact prompt that previously truncated at 35,072 characters
  completed in production with the 32k cap: 59,709 characters, 24,872 output
  tokens, closed `</html>` and fence, persisted identically.
- The live app rendered that artifact in the preview panel: 1,755 lines and a
  59,845-character `srcdoc` iframe, with the Northwind hero visible and no
  truncation.
- Re-ran the production voice probe after the deploy: realtime Gemini session
  connected in 673 ms, user and assistant transcripts streamed, 38 speech
  frames arrived, and no errors were reported.

### Postgres cutover (2026-10-25)

- Backend suite: **175 passed** after making schema evolution dialect-aware.
- Created Neon database `voicefirst`; startup created all **21 tables**.
- Set Render `VOICEFIRST_DATABASE_URL` to the unpooled Neon DSN and deployed
  `7f32a3f`; health returned `200`.
- Created the demo admin (`demo@openvoiceai.app`), signed in through Vercel,
  confirmed the JWT carried `role: admin`, and listed conversations with it.
- Sent a chat turn: conversation `conv_46f2d959cbc34092`, two messages, one run,
  all visible in Neon.
- Triggered a full Render rebuild (`dep-dbes0oss728c73d8flhg`), waited for
  `live`, and re-listed conversations: the same conversation and both messages
  were returned. Data now survives redeploys.
- Re-ran the production voice probe with the demo admin after the switch:
  realtime Gemini connected in 700 ms, user and assistant transcripts streamed,
  15 speech frames arrived, and no errors were reported.
- Re-ran the long-artifact prompt on Postgres: 44,850 characters, 19,431 output
  tokens, $0.106127, closed `</html>` and fence, and the persisted assistant
  message matched the streamed text exactly.

## Troubleshooting

- **Sign-up 500 on Vercel** — `DATABASE_URL` missing or the Neon integration
  is not connected to the current environment. Run `vercel env ls`.
- **CORS error in the browser** — add the frontend origin to
  `VOICEFIRST_CORS_ORIGINS` on Render and redeploy.
- **Voice connects but no audio** — check the backend logs for provider key
  errors; `VOICEFIRST_REALTIME_MODEL` must be a realtime-capable model.
- **WebSocket closes immediately** — the JWT must be valid against the JWKS URL
  configured on the backend. Verify `/api/auth/token` returns a token.
- **Backend healthy but conversations disappear** — confirm
  `VOICEFIRST_DATABASE_URL` points at `/voicefirst` with `?ssl=require`; the
  container falls back to ephemeral SQLite when it is unset.
- **Env var changed but no new deploy** — with `autoDeployTrigger: commit`,
  env-var edits do not deploy. Trigger a deploy (dashboard or
  `POST /v1/services/{id}/deploys`).
- **`Missing or null Origin` from Better Auth** — scripted sign-ins must send an
  `Origin: https://openvoiceai.vercel.app` header; browsers send it
  automatically.
