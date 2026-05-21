# Healing Agent — Dashboard Demo

Portfolio demo of the healing-agent content pipeline dashboard. All data is fixture data — no real API keys or pipeline required.

## What it shows

A six-stage Instagram content production workflow:

1. **Scrape** — automated media sourcing from 5 APIs scored by Gemini 2.5
2. **Sort Media** — review footage, audio, and image candidates; approve to pool
3. **Render** — pool inventory viewer and trigger carousel / story / reel editors
4. **Approve to Post** — slideshow preview per format, caption copy, approve/reject
5. **Schedule** — posting rhythm editor
6. **Intelligence** — Gemini-generated keywords, emotional hooks, run history

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Open [http://localhost:5500](http://localhost:5500).

## Deploy to Railway

1. Push this folder to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
3. Select the repo — Railway auto-detects the Procfile
4. Deploy — live URL in ~60 seconds

No environment variables needed.

## Tech stack

- Python · Flask · Gunicorn
- Vanilla JS · CSS custom properties
- Fixture data only — no API calls
