# Deployment

The app runs as three managed pieces:

| Piece | Host | URL |
| --- | --- | --- |
| Next.js experience layer | Vercel (project `openvoiceai`, root `frontend`) | https://openvoiceai.vercel.app |
| FastAPI control plane + voice WebSocket | Render (Docker, free web service) | `https://openvoiceai-backend.onrender.com` |
| Auth database | Neon Postgres (Vercel Marketplace, free plan) | via `DATABASE_URL` |

Both Vercel and Render are connected to `PathanRevanth/OpenVoiceAI` and deploy
automatically on every push to `master`.

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
- Auto-deploy: every commit on `master`

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

### Free-tier note

Render free web services sleep after ~15 minutes without traffic. The first
request after sleep takes 30–60 seconds. Voice sessions keep the service awake
while connected; for an always-warm demo, add an uptime monitor that pings
`https://<service>.onrender.com/health` every 10 minutes.

### Persistence

The container writes SQLite to `/data` (ephemeral on the free plan). Data
survives while the instance is running but resets on redeploy/restart. Use a
paid disk or point `VOICEFIRST_DATABASE_URL` at Postgres for durable history.

## Post-deploy checklist

```bash
# frontend
curl -i https://openvoiceai.vercel.app/login

# backend
curl -i https://<service>.onrender.com/health

# auth -> JWT -> JWKS
curl -i https://openvoiceai.vercel.app/api/auth/jwks
```

Then open the site, create an account, and start a voice session. Browser mic
access requires HTTPS (Vercel provides it).

## Troubleshooting

- **Sign-up 500 on Vercel** — `DATABASE_URL` missing or the Neon integration
  is not connected to the current environment. Run `vercel env ls`.
- **CORS error in the browser** — add the frontend origin to
  `VOICEFIRST_CORS_ORIGINS` on Render and redeploy.
- **Voice connects but no audio** — check the backend logs for provider key
  errors; `VOICEFIRST_REALTIME_MODEL` must be a realtime-capable model.
- **WebSocket closes immediately** — the JWT must be valid against the JWKS URL
  configured on the backend. Verify `/api/auth/token` returns a token.
