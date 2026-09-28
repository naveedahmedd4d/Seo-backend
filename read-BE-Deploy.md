# Backend Deployment Guide

Complete guide for deploying the **SEO Insight AI** backend on **Render**, connecting it to the **Vercel** frontend, and using the **CI/CD pipeline**.

---

## Live URLs

| Service | URL |
|---------|-----|
| **Backend (Production)** | https://seo-backend-xgzu.onrender.com |
| **Health check** | https://seo-backend-xgzu.onrender.com/health |
| **Frontend (Production)** | https://launchtrendai.vercel.app |

---

## Architecture

```
Developer pushes code to GitHub
        │
        ├── GitHub Actions (CI)  →  pip install → pytest
        │
        └── Render (CD)          →  build → deploy FastAPI
                │
                └── Serves API for Vercel frontend
```

| Layer | Platform | Role |
|-------|----------|------|
| **CI** | GitHub Actions | Run tests before/after merge |
| **CD** | Render | Build and deploy the FastAPI app |
| **Database** | Render PostgreSQL (or external) | Production data |

---

## Part 1: Render deployment setup

### Start command

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### Build command

```bash
pip install -r requirements.txt
```

### Required environment variables

| Variable | Example / notes |
|----------|-----------------|
| `DATABASE_URL` | PostgreSQL connection string from Render Postgres |
| `SECRET_KEY` | Long random string for JWT signing |
| `JWT_ALGORITHM` | `HS256` |
| `JWT_EXPIRATION` | `3600` |
| `CORS_ORIGINS` | `["https://launchtrendai.vercel.app"]` — **no trailing slash** |
| `SERPAPI_KEY` | SerpAPI key |
| `TAVILY_API_KEY` | Tavily key |
| `GROQ_API_KEY` | Groq key |
| `APIFY_API_KEY` | Platform-owner fallback for Apify. Normal Reddit Trends requests use the encrypted per-user key saved in dashboard Settings. |
| `APIFY_REDDIT_ACTOR_ID` | Optional Reddit Actor override; defaults to `fatihtahta~reddit-scraper-search-fast` |
| `API_PUBLIC_URL` | Optional fallback when request host headers are unavailable (e.g. background jobs). Social post `media_url` is derived from the incoming request (`Host` / `X-Forwarded-*`) when possible. |
| `FRONTEND_URL` | `https://launchtrendai.vercel.app` — OAuth callback redirects, and the base URL for the Technology & AI infographic-rendering call |
| `INFOGRAPHIC_RENDER_SECRET` | Shared secret sent as `x-infographic-secret` when calling `FRONTEND_URL + /api/infographic/render`. Must exactly match the same-named var on Vercel. If unset, Technology & AI articles just generate with 0 infographics (non-fatal). |
| `INFOGRAPHIC_BRAND_COLOR` | Optional. Hex colour for the 16:9 infographic frame (default `#2563eb`). |
| `INFOGRAPHIC_BRAND_NAME` | Optional. Site name shown in the infographic footer (e.g. `trendifypress.com`). No footer when unset. |
| `INFOGRAPHIC_BRAND_LOGO_URL` | Optional. `https` logo URL shown next to the footer name. |

See `ENV_TEMPLATE.txt` for the full list.

### CORS rules

```env
CORS_ORIGINS=["https://launchtrendai.vercel.app"]
```

**Wrong (breaks frontend login):**

```env
CORS_ORIGINS=["https://launchtrendai.vercel.app/"]
CORS_ORIGINS=https://launchtrendai.vercel.app
```

### Database migrations

After first deploy or schema changes, run against production DB:

```bash
alembic upgrade head
```

Use Render Shell or run locally with production `DATABASE_URL`.

### Reddit Trends scheduled collection

Create a Render Cron Job that uses the same repository, environment variables, and database as the web service:

```bash
python -m app.jobs.reddit_trends
```

Run it every six hours (`0 */6 * * *`). The job reads each account's saved 6- or 12-hour interval, skips accounts that are not due, prevents overlapping runs, and uses the encrypted Apify key already configured for that account. Manual collection and cached/force-refresh behavior remain available from the Reddit Trends tab even without the cron job.

### Bot Automation scheduler

Run the due-bot worker once per minute from the platform scheduler:

```bash
python -m app.jobs.bot_automation
```

The worker only queues active bots whose `next_run_at` has passed. Per-schedule idempotency keys prevent a due slot from being executed twice.

### Verify deployment

```bash
curl https://seo-backend-xgzu.onrender.com/health
```

Expected: `{"status":"healthy"}`

First request after idle may take 30–60 seconds (Render free tier cold start).

---

## Part 2: CI/CD pipeline

### What runs automatically

| Event | GitHub Actions | Render |
|-------|----------------|--------|
| Push to `main` | Run pytest | Production deploy |
| Push to feature branch | Runs on PR | — |
| Pull request | Run pytest on PR | — |

**You do not run a separate deploy command for Render.** Push to GitHub and both CI and CD run (when Render auto-deploy is enabled).

### Workflow file

Location: `.github/workflows/ci.yml`

Steps on every push/PR to `main`:

1. Setup Python 3.12
2. `pip install -r requirements.txt -r requirements-dev.txt`
3. `pytest tests/ -v` with test env vars (SQLite in-memory, no real Postgres needed)

Check results: GitHub → **Actions** → **Backend CI**

---

## Part 3: Commands

### Deploy to production (most common)

```powershell
cd d:\seo\backend

git add .
git commit -m "Describe your change"
git push origin main
```

GitHub Actions runs tests; Render deploys to production.

### Safer workflow (branch + PR)

```powershell
cd d:\seo\backend

git checkout -b feature/my-change
# edit files...

git add .
git commit -m "Add new feature"
git push origin feature/my-change
```

Then on GitHub: open a **Pull Request** → wait for green **Backend CI** → **Merge to main** → Render deploys.

### Test locally before push (optional)

**Windows (PowerShell):**

```powershell
cd d:\seo\backend
.\scripts\ci-check.ps1
```

**Mac / Linux / Git Bash:**

```bash
cd backend
chmod +x scripts/ci-check.sh
./scripts/ci-check.sh
```

**Manual:**

```powershell
cd d:\seo\backend
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
$env:SECRET_KEY="ci-test-secret-key"
$env:DATABASE_URL="sqlite:///:memory:"
pytest tests/ -v
```

### Run backend locally (development)

```powershell
cd d:\seo\backend
.\venv\Scripts\Activate.ps1

# Create .env from ENV_TEMPLATE.txt with local DATABASE_URL

uvicorn app.main:app --reload
```

API at http://localhost:8000  
Docs at http://localhost:8000/docs

### Manage production users

```powershell
cd d:\seo\backend
.\venv\Scripts\Activate.ps1
python scripts/manage_user.py --help
```

---

## Part 4: One-time setup checklist

### Render

- [ ] Git repo connected to Render web service
- [ ] Production branch = `main`
- [ ] Build command: `pip install -r requirements.txt`
- [ ] Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- [ ] Auto-Deploy enabled
- [ ] All env vars set (`DATABASE_URL`, `SECRET_KEY`, `CORS_ORIGINS`, API keys)
- [ ] Database migrations run (`alembic upgrade head`)

### GitHub

- [ ] `.github/workflows/ci.yml` pushed to `main`
- [ ] Actions tab shows green **Backend CI** runs
- [ ] Optional: branch protection on `main` (require PR + status check **Test**)

### Frontend connection

- [ ] Vercel has `NEXT_PUBLIC_API_URL=https://seo-backend-xgzu.onrender.com`
- [ ] Render `CORS_ORIGINS` includes `https://launchtrendai.vercel.app` (no trailing slash)

---

## Part 5: Troubleshooting

### Render

| Symptom | Cause | Fix |
|---------|-------|-----|
| 502 / service unavailable | Wrong start command or crash on boot | Check Render logs; verify `DATABASE_URL` |
| Slow first request | Cold start (free tier) | Wait and retry; upgrade plan if needed |
| DB connection errors | Wrong `DATABASE_URL` | Copy Internal URL from Render Postgres |
| Missing tables | Migrations not run | `alembic upgrade head` |

### CORS / frontend

| Symptom | Cause | Fix |
|---------|-------|-----|
| Frontend login fails with 200 OK | CORS origin mismatch | Fix `CORS_ORIGINS` — no trailing slash |
| CORS error in browser | Wrong domain in allow list | Use exact `https://launchtrendai.vercel.app` |

### CI / GitHub Actions

| Symptom | Cause | Fix |
|---------|-------|-----|
| CI not running | Workflow not on `main` | Push `.github/workflows/ci.yml` |
| `ModuleNotFoundError` | Missing deps | Ensure `requirements.txt` is complete |
| Tests fail in CI | Code regression | Run `.\scripts\ci-check.ps1` locally |

---

## Part 6: Project files reference

| File | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | GitHub Actions CI pipeline |
| `requirements-dev.txt` | pytest + pytest-asyncio for CI/local tests |
| `pytest.ini` | Pytest config (asyncio mode) |
| `scripts/ci-check.ps1` | Local CI script (Windows) |
| `scripts/ci-check.sh` | Local CI script (Mac/Linux) |
| `ENV_TEMPLATE.txt` | Environment variable template |
| `scripts/manage_user.py` | CLI to manage users in production DB |

---

## Quick reference

| Goal | Command |
|------|---------|
| Deploy to production | `git push origin main` |
| Test before push | `.\scripts\ci-check.ps1` |
| Run API locally | `uvicorn app.main:app --reload` |
| Manual Render deploy | **Not needed** — push triggers deploy |
| Check CI status | GitHub → Actions → Backend CI |
| Check deploy status | Render → Seo-backend → Events |
| Check backend health | https://seo-backend-xgzu.onrender.com/health |
