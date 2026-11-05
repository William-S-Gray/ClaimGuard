  # Deploying ClaimGuard 360° — Render (backend) + Vercel (frontend)

The backend runs on **Render** (Docker) with a managed **Redis**, using your existing
**Neon** Postgres. The frontend is a static SPA on **Vercel**.

Because `*.vercel.app` and `*.onrender.com` are **different sites**, the refresh-token
cookie is `SameSite=None; Secure` and the API uses **CORS pinned to your Vercel origin**.
Both platforms serve HTTPS, so `Secure` cookies work out of the box.

> Deploy order matters (there's a chicken-and-egg between the two origins):
> **1) backend → 2) frontend → 3) point backend CORS at the frontend → 4) put the backend
> host in the frontend CSP.**

---

## 1. Backend on Render

> **Must be a Docker service.** The image pins Python 3.12. A plain Python web service makes
> Render use Python 3.14 + `pip install`, which fails building `pydantic-core` from source
> (no wheel for 3.14). Always pick **Docker**.

### 1a. Paid plans — Blueprint (auto-wires everything)

1. **New → Blueprint**, point it at this repo. Render reads [`render.yaml`](./render.yaml)
   and creates `claimguard-api` (Docker web) + `claimguard-redis`. `JWT_SECRET_KEY` is
   generated automatically; `REDIS_URL` is wired automatically.
2. In **claimguard-api → Environment**, set the `sync: false` secrets:
   | Var | Value |
   |-----|-------|
   | `DATABASE_URL` | Neon **direct** (non-pooler) URL, e.g. `postgresql+asyncpg://…neon.tech/neondb?ssl=require` |
   | `FIRST_ADMIN_EMAIL` | your admin login email |
   | `FIRST_ADMIN_PASSWORD` | a strong, unique password (**not** the demo default — the app refuses to boot otherwise) |
   | `CORS_ORIGINS` | leave blank for now; set in step 3 |

### 1b. Free plan — manual Docker service (Blueprints are paid)

1. **New → Web Service** → connect this repo, then set:
   | Setting | Value |
   |---------|-------|
   | Language / Runtime | **Docker** |
   | Root Directory | `backend` |
   | Dockerfile Path | `docker/Dockerfile` |
   | Instance Type | **Free** |
   | Health Check Path | `/api/v1/health/liveness` |
   | Start Command | *leave blank* (the image runs migrations + seed + gunicorn) |
2. Add all environment variables by hand (no Blueprint auto-wiring on Free):
   ```
   ENVIRONMENT=production
   DEBUG=false
   DEMO_MODE=false
   LOG_JSON=true
   COOKIE_SECURE=true
   COOKIE_SAMESITE=none
   SEED_ON_STARTUP=true
   WEB_CONCURRENCY=1
   CLIENT_IP_HEADER=true-client-ip
   JWT_SECRET_KEY=<openssl rand -hex 32>
   DATABASE_URL=<Neon direct URL with ?ssl=require>
   FIRST_ADMIN_EMAIL=<your admin email>
   FIRST_ADMIN_PASSWORD=<strong password — NOT the demo default>
   ```
   - **`WEB_CONCURRENCY=1`** — Free has 512 MB RAM; 2 gunicorn workers risk OOM.
   - **`CLIENT_IP_HEADER=true-client-ip`** — the client IP behind rate limiting, login
     lockout and the audit log. Render passes the client's own `X-Forwarded-For`
     through, so that header can be forged; Cloudflare (in front of Render) overwrites
     `True-Client-IP`. Without it the app counts from the right of `X-Forwarded-For`
     (`FORWARDED_IP_HOPS`, default 1), which suits the bundled nginx but not Render.
   - **Do not set `PORT`** — Render injects it; the container binds `$PORT`.
   - **`CORS_ORIGINS`** — add after the frontend deploy (step 3). Until then the API
     accepts no cross-site requests (blank never means "any origin").
   - **`JWT_SECRET_KEY`** — generate with `openssl rand -hex 32` (or
     `python3 -c "import secrets; print(secrets.token_hex(32))"`). Keep it secret; don't
     rotate it later or all live sessions are invalidated.
3. **Redis (optional on Free).** With `WEB_CONCURRENCY=1` you can skip it — the app falls
   back to in-memory brute-force lockout + rate-limiting (single worker, so realtime still
   works); it just logs a harmless `redis.unavailable` warning. For real Redis:
   **New → Key Value** (free tier) → copy its **Internal** URL → add `REDIS_URL=<that url>`.

> **Render can't convert a service's runtime after creation.** If an existing service is
> building as Python (log shows `Using Python version …` / `pip install`), delete it and
> create a new one with **Language = Docker** — editing won't switch it.

### 1c. Free plan — native Python (no Docker)

If the Docker option is giving you trouble, deploy on Render's **native Python** builder
instead. The repo pins Python **3.12** via [`backend/.python-version`](./backend/.python-version)
(Render's 3.14 default has no `pydantic-core` wheel and fails compiling Rust on the
read-only build FS; 3.12 installs everything from prebuilt wheels).

1. **New → Web Service** → connect this repo:
   | Setting | Value |
   |---------|-------|
   | Language | **Python 3** |
   | Root Directory | `backend` |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `bash scripts/entrypoint.sh` |
   | Instance Type | **Free** |
   | Health Check Path | `/api/v1/health/liveness` |
2. Add the **same environment variables** as §1b (step 2) — including `WEB_CONCURRENCY=1`,
   `JWT_SECRET_KEY`, `DATABASE_URL`, `FIRST_ADMIN_*`. Redis is optional (§1b step 3).

The start command runs the same migrations + seed + gunicorn as the Docker image.

### Then (all options)

Deploy. On first boot the container runs Alembic migrations and seeds **RBAC + your admin
only** (no demo data, because `DEMO_MODE=false`). Watch the logs for `seed.done`, then hit
`https://<your-api>.onrender.com/api/v1/health/readiness`. Note the service host, e.g.
`claimguard-api.onrender.com`.

> After the first successful deploy you may set `SEED_ON_STARTUP=false` (the seed is
> idempotent, so leaving it on is harmless).

## 2. Frontend on Vercel

1. **New Project** → import this repo → set **Root Directory = `frontend`**. Vercel detects
   Vite and uses [`frontend/vercel.json`](./frontend/vercel.json).
2. **Settings → Environment Variables** (Production), using the backend host from step 1.4:
   | Var | Value |
   |-----|-------|
   | `VITE_API_BASE_URL` | `https://claimguard-api.onrender.com/api/v1` |
   | `VITE_WS_URL` | `wss://claimguard-api.onrender.com/api/v1/ws` |
   | `VITE_DEMO_MODE` | `false` (or omit — real builds ship an empty login form) |
3. Deploy. Note the production URL, e.g. `https://claimguard.vercel.app`.

## 3. Point the backend at the frontend (CORS)

In Render → `claimguard-api` → Environment, set:

```
CORS_ORIGINS = https://claimguard.vercel.app
```

Use the **exact** origin (scheme + host, no trailing slash). For multiple origins, use a
comma-separated list. Save → Render redeploys. (Preview deployments have per-branch URLs;
add them here if you need CORS for previews.)

## 4. Put the backend host in the frontend CSP

Edit [`frontend/vercel.json`](./frontend/vercel.json) and replace **both** `YOUR-BACKEND`
placeholders in the `Content-Security-Policy` `connect-src` with your Render host:

```
connect-src 'self' https://claimguard-api.onrender.com wss://claimguard-api.onrender.com;
```

Commit + push → Vercel redeploys. Without this, the browser blocks the SPA's API and
WebSocket calls.

---

## Verify

- Open the Vercel URL → log in with `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD`.
- DevTools → **Network**: the login response sets `cg_refresh` with `HttpOnly; Secure;
  SameSite=None`; API calls carry `Authorization: Bearer …`.
- **Console** is clean (build strips all `console.*`).
- Reload the page → you stay logged in (access token is re-minted from the cookie).
- **Application → Cookies**: no tokens in localStorage; only the httpOnly refresh cookie.

## Notes / limitations for this setup

- **No Celery workers** (per the chosen plan): scheduled/background tasks (e.g. periodic
  rescoring) don't run. The API, realtime WebSocket, brute-force lockout, and rate limiting
  all work (Redis-backed).
- **Web concurrency**: the API runs under gunicorn with `WEB_CONCURRENCY` uvicorn workers
  (default 2). Realtime events fan out across workers via a Redis pub/sub backplane, so
  scaling workers/instances is safe. Raise `WEB_CONCURRENCY` for a bigger plan.
- **Notification delivery** (email/WhatsApp) is opt-in. Without credentials, dispatch is
  logged only (in-app bell + WebSocket still work). To enable, set on the Render service:
  - **Email (SMTP)**: `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_USER`, `SMTP_PASSWORD`,
    `SMTP_FROM`, `SMTP_STARTTLS` (default true). Case assignments then email the assignee.
  - **WhatsApp (Twilio)**: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`
    (e.g. `+14155238886`).
- **Render free web** spins down on idle (cold starts). Use the `starter` plan (default in
  the blueprint) to stay warm.
- **Secrets** live in Render/Vercel env settings — never commit them. `JWT_SECRET_KEY` is
  generated by Render and stable across deploys (don't rotate it casually or you invalidate
  all live sessions).
- To later add a **custom domain** (`api.example.com` + `app.example.com`), you can switch
  `COOKIE_SAMESITE` back to `lax` for a stronger CSRF posture, since subdomains of one site
  are same-site.
