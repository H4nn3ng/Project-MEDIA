# Healing Agent

Automated Instagram content pipeline for healing and wellness. Runs three times a week (Mon / Wed / Fri) and produces scored batches of carousel, reel, and story content ready for review and posting.

## What it does

1. **Scrape** — pulls CC0 footage, images, and audio from Pexels, Pixabay, Coverr, Freesound, and Jamendo
2. **Score** — rates each asset using Gemini 2.5 against emotional relevance and niche fit
3. **Render** — assembles carousels, reels, and stories from approved pool assets
4. **Review** — dashboard preview per format with caption copy and approve/reject
5. **Schedule** — posting rhythm editor aligned to Mon/Wed/Fri cadence
6. **Intelligence** — Gemini-generated keywords, emotional hooks, and run history

## Run

```bash
pip install -r requirements.txt

# Run the pipeline
python agent.py

# Run the dashboard
python run_dashboard.py        # http://localhost:5500
python run_dashboard.py --port 8080 --no-browser
```

## Environment variables

Copy `.env.example` and fill in your keys:

```
PEXELS_API_KEY
PIXABAY_API_KEY
COVERR_API_KEY
FREESOUND_CLIENT_ID
FREESOUND_CLIENT_SECRET
JAMENDO_CLIENT_ID
GEMINI_API_KEY
GOOGLE_APPLICATION_CREDENTIALS
```

## Tech stack

- Python · Flask
- Gemini 2.5 — scoring and intelligence
- Anthropic Vertex — editor agent
- MoviePy · stable-ts — video and audio editing
- Pexels · Pixabay · Coverr · Freesound · Jamendo — media sources
