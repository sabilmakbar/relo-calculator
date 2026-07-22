# Deployment

A single Docker container with no database and no secrets — any container platform works.

```bash
docker build -t relo-calculator .
docker run -p 8000:8000 relo-calculator
```

No environment variables are required.

## Free hosting (2026)

Genuinely free, no-credit-card options for a Docker FastAPI app:

| Platform | Free tier | Sleep | Notes |
|---|---|---|---|
| **Render** | 750 hrs/mo, no CC | 15 min idle → ~30–60s cold start | Easiest: connect GitHub, auto-detects the Dockerfile |
| **Hugging Face Spaces** | 2 vCPU / 16 GB, no CC | Sleeps after long idle | Docker Space; the `sync-to-huggingface` Action mirrors the repo and injects the Space config (`sdk`/`app_port`) — set an `HF_TOKEN` secret to enable it; endpoints are public |
| **Google Cloud Run** | 2M req/mo, scale-to-zero | Scale-to-zero cold start | Best performance, but **requires** a card on file |

Avoid for "free": **Fly.io / Heroku** (no free tier), **Railway** (credit-based), **Koyeb** (free Starter tier removed after the Feb 2026 Mistral acquisition — $29/mo entry).

## Render specifics

Render injects a `$PORT` env var and expects the app to bind to it. To make the image portable (Render / Cloud Run / HF), keep the `CMD` honoring `$PORT`:

```dockerfile
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

The app answers `HEAD /` with 200 so platform **health checks** pass (Render probes with `HEAD`).

## Notes

- On any free tier you share an egress IP, so **Yahoo Finance will 429 more often** — the Frankfurter → currency-api fallback chain handles it, so FX still resolves.
- Render deploys from `main`, so changes must reach `main` to go live.
