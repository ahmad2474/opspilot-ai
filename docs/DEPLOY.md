# Deploying OpsPilot AI for free

Two hosted pieces, both on free tiers, plus the DynamoDB tables you already have:

| Piece | Host | Why |
|---|---|---|
| `opspilot-frontend` (Next.js 14) | **Vercel** (Hobby) | Built by the Next.js team; `next-auth` + `middleware.ts` work with zero config. |
| `opspilot-backend` (FastAPI) | **Render** (free web service, Docker) | Builds `opspilot-backend/Dockerfile` straight from the repo via `render.yaml`. |
| DynamoDB tables | AWS (already provisioned) | Permanent free tier: 25 GB + 25 RCU/WCU. Nothing to change. |

Order matters slightly: the backend needs the frontend's URL (for CORS) and the
frontend needs the backend's URL (for API calls). Deploy the backend first,
then the frontend, then go back and set CORS.

---

## 0. Generate the secrets once, keep them in a scratch file

Run these locally and paste the outputs in the steps below. Both platforms need
the **same** `AUTH_SHARED_SECRET` and `ADMIN_EMAIL`.

```bash
# AUTH_SHARED_SECRET  (paste into BOTH Render and Vercel)
openssl rand -base64 32

# NEXTAUTH_SECRET  (Vercel only)
openssl rand -base64 32

# ADMIN_PASSWORD_HASH  (Vercel only) -- base64 of a bcrypt hash of your login password
cd opspilot-frontend
node -e "require('bcryptjs').hash(process.argv[1], 10).then(h => console.log(Buffer.from(h).toString('base64')))" "your-password-here"
```

You also need the read-only AWS access key pair for the `opspilot-readonly`
IAM user and at least one LLM key (Groq or Gemini). If you ran
`scripts/setup.py` locally they're already in `opspilot-backend/.env` — copy
them from there.

---

## 1. Backend on Render

1. https://dashboard.render.com → **New +** → **Blueprint**.
2. Connect GitHub if prompted, choose the `opspilot-ai` repo, branch `main`.
   Render reads `render.yaml` at the repo root and shows one service:
   `opspilot-backend` (Docker, Free).
3. It will prompt for every `sync: false` value. Fill in:

   | Key | Value |
   |---|---|
   | `AWS_ACCESS_KEY_ID` | read-only IAM user key |
   | `AWS_SECRET_ACCESS_KEY` | read-only IAM user secret |
   | `OPSPILOT_EC2_INSTANCE_ID` | your demo instance id (`i-…`) |
   | `GROQ_API_KEY` | Groq key (or leave blank if using Gemini) |
   | `GEMINI_API_KEY` | Gemini key (or leave blank if using Groq) |
   | `AUTH_SHARED_SECRET` | the first `openssl` output from step 0 |
   | `ADMIN_EMAIL` | the email you'll log in with |
   | `OPSPILOT_CORS_ORIGINS` | put `http://localhost:3000` for now — you'll replace it in step 3 |

   If your primary LLM is Gemini rather than Groq, also change
   `OPSPILOT_LLM_PRIMARY_PROVIDER` to `gemini`. `AWS_REGION` defaults to
   `us-east-1`; change it if your tables live elsewhere.
4. **Apply**. First build takes ~3–5 min (pip installs boto3, langchain, etc.).
5. When it's live, note the URL — `https://opspilot-backend-XXXX.onrender.com`.
   Check it: `curl https://opspilot-backend-XXXX.onrender.com/health` → `{"status":"ok"}`.
   (`/docs` is intentionally disabled when `OPSPILOT_APP_ENV=prod`.)

**Free-tier caveat:** Render spins the service down after 15 min idle and
cold-starts in ~30–60 s on the next request. The first galaxy scan after a
break will look hung — that's the wake-up, not a bug. To avoid it, create a
free monitor at https://cron-job.org (or UptimeRobot) that GETs `/health`
every 10 minutes.

---

## 2. Frontend on Vercel

1. https://vercel.com/new → **Import** the `opspilot-ai` repo.
2. In the import screen:
   - **Root Directory** → click *Edit* → `opspilot-frontend`. (Required —
     the repo root has no `package.json`.)
   - Framework Preset: Next.js (auto-detected once the root dir is set).
   - Build/Output settings: leave defaults.
3. **Environment Variables** (add all before the first deploy so the build
   picks up `NEXT_PUBLIC_*`):

   | Key | Value |
   |---|---|
   | `NEXT_PUBLIC_API_BASE_URL` | the Render URL from step 1.5, **no trailing slash** |
   | `NEXTAUTH_URL` | your Vercel production URL — leave blank for the very first deploy, fill it in after (step 2.5) |
   | `NEXTAUTH_SECRET` | second `openssl` output from step 0 |
   | `ADMIN_EMAIL` | same as on Render |
   | `ADMIN_PASSWORD_HASH` | the base64 string from step 0 |
   | `AUTH_SHARED_SECRET` | **identical** to the Render value |

4. **Deploy**. ~2 min.
5. Copy the production URL (`https://opspilot-ai-XXXX.vercel.app`). Go to
   *Settings → Environment Variables*, set `NEXTAUTH_URL` to that exact URL,
   then *Deployments → ⋯ → Redeploy* so the value takes effect.

---

## 3. Close the loop: CORS

Back in Render → `opspilot-backend` → **Environment** → set
`OPSPILOT_CORS_ORIGINS` to the Vercel production URL (`https://…vercel.app`,
no trailing slash). Save — Render redeploys automatically (~1 min since the
image is cached).

---

## 4. Smoke test

1. Open the Vercel URL → you should land on `/login`.
2. Sign in with `ADMIN_EMAIL` + the password you hashed.
3. Dashboard → run a galaxy scan. If Render was asleep, wait ~60 s and retry once.
4. Chat → ask "what's running in us-east-1" → you should get a tool-trace answer.

### If something's off

| Symptom | Cause |
|---|---|
| Login page loops / "Invalid credentials" | `ADMIN_PASSWORD_HASH` pasted as a raw `$2b$…` hash instead of base64, or `NEXTAUTH_URL` wrong. |
| Every API call 401s after login | `AUTH_SHARED_SECRET` differs between Render and Vercel, or `ADMIN_EMAIL` differs. |
| Browser console shows CORS errors | `OPSPILOT_CORS_ORIGINS` doesn't exactly match the Vercel origin (scheme + host, no path, no trailing slash). |
| First request after a while takes ~1 min | Render free-tier cold start. Add the keep-alive ping from step 1. |
| Scan returns empty / AccessDenied | AWS keys on Render are wrong or the IAM policy from `docs/iam-policy.json` isn't attached. |

---

## Later: custom domain / preview deploys

- Vercel preview deployments (one per PR) get their own `*.vercel.app` URL,
  which the backend's CORS list won't include. Either append them to
  `OPSPILOT_CORS_ORIGINS` (comma-separated) as needed or just test on
  production.
- If you add a custom domain on Vercel, update **both** `NEXTAUTH_URL` (Vercel)
  and `OPSPILOT_CORS_ORIGINS` (Render).
