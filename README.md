# Healing Agent — Automated Instagram Content Pipeline

> An end-to-end data pipeline that turns CC0 media, real-time emotional signals, and AI-generated scripts into reviewed, captioned, and calendar-scheduled Instagram posts — with zero manual curation of raw assets.

---

## Architecture Overview

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                     HEALING AGENT — PIPELINE ARCHITECTURE                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  ┌───────────────────────────────────────────────────────────────────────┐  ║
║  │  STAGE 01 · INGESTION  (automated · Mon / Wed / Fri)                  │  ║
║  │                                                                       │  ║
║  │  RSS Feeds ─────────────► step1   Emotional climate pulse            │  ║
║  │                            step3   Keyword gen     (Gemini 2.5)      │  ║
║  │                            step3b  Content scripts (carousel+quote)  │  ║
║  │                                                                       │  ║
║  │  Pexels ──┐                                                           │  ║
║  │  Pixabay  ├────────────── ► step4   Footage pool                     │  ║
║  │  Coverr ──┘                 step4b  Image pool                       │  ║
║  │                                                                       │  ║
║  │  Jamendo ───┐                                                         │  ║
║  │  Freesound  ├───────────── ► step5   Audio pool                      │  ║
║  │  Archive ───┘                                                         │  ║
║  └───────────────────────────────┬───────────────────────────────────────┘  ║
║                                  │                                           ║
║                                  ▼                                           ║
║  ┌───────────────────────────────────────────────────────────────────────┐  ║
║  │  STAGE 02 · SCORING & FILTERING                                       │  ║
║  │                                                                       │  ║
║  │  step6   Score footage + audio  ┐  ≥ 8.5 → pool/priority/            │  ║
║  │  step6b  Score images           ├─ ≥ 7.0 → pool/standard/            │  ║
║  │           (Gemini · batches/20) ┘  < 7.0 → rejected (never saved)    │  ║
║  │                                                                       │  ║
║  │  step7   Download footage + audio →  data/weekly_batch/YYYY-MM-DD/   │  ║
║  │  step7b  Download images          →  data/pool/images/ (persistent)  │  ║
║  └───────────────────────────────┬───────────────────────────────────────┘  ║
║                                  │                                           ║
║                                  ▼                                           ║
║  ┌───────────────────────────────────────────────────────────────────────┐  ║
║  │  STAGE 03 · RENDERING  (on-demand, per format)                        │  ║
║  │                                                                       │  ║
║  │  editor_carousel.py → slide_01..N.jpg · manifest.json · caption.txt  │  ║
║  │  editor_story.py    → quote images · per-image captions              │  ║
║  │  editor_reels.py    → draft_music | draft_sound | draft_voice .mp4   │  ║
║  │                                                                       │  ║
║  │  Output: data/pending/<render_name>/                                  │  ║
║  └───────────────────────────────┬───────────────────────────────────────┘  ║
║                                  │                                           ║
║                                  ▼                                           ║
║  ┌───────────────────────────────────────────────────────────────────────┐  ║
║  │  STAGE 04 · HUMAN REVIEW GATE  (Flask dashboard · SSE live logs)      │  ║
║  │                                                                       │  ║
║  │  Carousels → slide-by-slide preview · caption · Postable / Skip      │  ║
║  │  Reels     → video player · variant tabs (music / sound / voice)     │  ║
║  │  Stories   → image grid · per-image verdict                          │  ║
║  │                                                                       │  ║
║  │  postable     → copy to data/approved/   + auto-schedule             │  ║
║  │  not_postable → copy to data/not_approved/ (audit trail)             │  ║
║  └───────────────────────────────┬───────────────────────────────────────┘  ║
║                                  │                                           ║
║                                  ▼                                           ║
║  ┌───────────────────────────────────────────────────────────────────────┐  ║
║  │  STAGE 05 · SCHEDULING  (monthly calendar · auto-slot assignment)     │  ║
║  │                                                                       │  ║
║  │  Approved post → next open slot by format → schedule_assignments.json │  ║
║  │  Modal: slideshow preview · caption copy · open folder in file manager│  ║
║  └───────────────────────────────┬───────────────────────────────────────┘  ║
║                                  │                                           ║
║                                  ▼                                           ║
║  ┌───────────────────────────────────────────────────────────────────────┐  ║
║  │  FEEDBACK LOOP  (weekly · Sunday · world_bible.json)                  │  ║
║  │                                                                       │  ║
║  │  feedback.py → high/low tags · rejected IDs · post learnings          │  ║
║  │               ↳ seeds keyword generation on every next run            │  ║
║  └───────────────────────────────────────────────────────────────────────┘  ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## Pipeline Steps — In Depth

### Step 1 — Emotional Climate
RSS feeds from mental-health and wellness outlets are parsed to derive a real-time emotional signal — the **climate keyword** — that seeds everything downstream. No auth required; failsafe if feeds are unreachable.

### Steps 3 & 3b — Keyword and Script Generation
Gemini 2.5 Flash expands the climate into **15 search keywords** and **5 emotional hooks**, then generates three complete **carousel scripts** (7-slide narratives) and **10 power quotes**. All output lands in `data/scrape/intelligence/` as structured JSON.

### Steps 4, 4b & 5 — Multi-source Media Ingestion

Six APIs queried in priority order with graceful fallthrough:

| Source | Media | Tier | Notes |
|---|---|---|---|
| Pexels | footage + photos | Primary | |
| Pixabay | footage + photos | Secondary | 24 h disk cache (TOS requirement) |
| Coverr | footage | Tertiary | |
| Freesound | audio | Primary | OAuth 2 |
| Jamendo | audio | Secondary | |
| Internet Archive | audio | Tertiary | |

Pixabay's 24 h cache is **mandatory at the ingestion layer** — not optional — to comply with their API Terms of Service.

### Steps 6 & 6b — AI Scoring

Gemini 2.5 Flash scores each asset **1–10** against the channel's emotional identity (warm · slow · atmospheric · nervous system). Submitted in **batches of 20** to stay inside the 1,500 req/day quota.

```
score ≥ 8.5  →  pool/{footage|audio|images}/priority/
score ≥ 7.0  →  pool/{footage|audio|images}/standard/
score < 7.0  →  dropped — never touches disk
```

### Steps 7 & 7b — Download with 9-Layer Defence

Every byte passes a layered security chain before reaching disk:

| Layer | Threat prevented |
|---|---|
| HTTPS enforcement | Protocol downgrade / plaintext interception |
| Domain allowlist (initial URL) | SSRF — internal network access via crafted URL |
| Domain allowlist (final `resp.url`) | Redirect-chain bypass (allowed host → attacker) |
| Content-Type check | HTML error pages saved as media files |
| Content-Length header check | Declared-huge-file before streaming |
| Per-chunk byte counter | Streaming past the size limit |
| `.tmp` write + atomic `Path.replace()` | Partial / corrupt files appearing complete |
| `finally: tmp_path.unlink()` | Orphaned `.tmp` files on any error path |
| Magic-bytes validation | Wrong file type or disguised executables |

**Images** go to a **persistent pool** (`data/pool/images/`) that accumulates across runs.  
**Footage + audio** go to a **weekly batch** folder keyed by run date (`data/weekly_batch/YYYY-MM-DD/`).

### Stage 3 — Rendering

Three specialised editor scripts produce publication-ready output from pooled assets:

- **`editor_carousel.py`** — PIL-rendered 1:1 slides with typography, background image, and brand overlay. Each carousel folder ships `manifest.json` (slide texts), all slide JPEGs, and `caption.txt` (Gemini-generated Instagram caption, or world-bible keyword fallback).
- **`editor_story.py`** — single-frame quote images with per-image `_caption.txt` files.
- **`editor_reels.py`** — three `.mp4` variants per reel: `draft_music` / `draft_sound` / `draft_voice`, assembled with MoviePy + FFmpeg. Reviewer picks the variant at approval time.

### Stage 4 — Human Review Gate

Flask dashboard with **Server-Sent Events** for live log tailing. Five panels:

| Stage | Purpose |
|---|---|
| Media pool | Rate raw assets g/b before rendering |
| Rendered output | Preview carousels, reels, stories |
| Intelligence | Keyword history, world bible |
| Approve to post | Final per-post verdict |
| Timeline | Monthly posting calendar |

The **verdict engine** (`final_api.py`):
- `postable` → `shutil.copytree()` to `data/approved/<format>/` + schedule assignment written
- `not_postable` → moved to `data/not_approved/` — preserved for audit
- **Verdict reversal** — deletes the approved copy and removes the schedule slot atomically

### Stage 5 — Auto-Scheduling

`schedule_api.py` maps each approved post to the **next open calendar slot** matching its format, using a forward-search up to 52 weeks. Assignments persist in `schedule_assignments.json` and survive process restarts.

```json
{
  "slot_id": "tue_carousel",
  "slot_day": "tuesday",
  "slot_time": "07:00",
  "week_start": "2026-05-25",
  "post_key": "2026-05-16_003/carousel_01",
  "format": "carousel",
  "folder_path": "data/approved/carousel/2026-05-16_003_carousel_01",
  "thumbnail_url": "data/pending/2026-05-16_003/carousel_01/slide_01.jpg",
  "caption": "..."
}
```

Stale past-dated slots are **pruned and re-assigned** to future dates on every Timeline load, so the pipeline self-heals if the dashboard isn't opened for a week.

### Feedback Loop — `world_bible.json`

`feedback.py` (runs Sunday) accumulates channel signal across runs:
- **High-performing tags** → weighted more heavily in the next keyword generation pass
- **Post learnings** — free-text notes attached to verdict decisions, stored permanently
- **Rejected asset IDs** — blocklist prevents re-download across runs
- All signal stored in a single JSON document that grows indefinitely

---

## Persistent State

| File / Directory | Purpose |
|---|---|
| `data/world_bible.json` | Accumulated channel knowledge — grows across runs |
| `data/post_review.json` | Verdict store, keyed by `post_key` |
| `data/schedule_assignments.json` | Timeline slot assignments |
| `config/posting_schedule.json` | Slot definitions (day · time window · format) |
| `.pixabay_cache.json` | Pixabay API response cache (TOS requirement) |
| `data/pool/images/` | Persistent scored image pool |
| `data/weekly_batch/YYYY-MM-DD/` | Per-run footage + audio downloads |
| `data/approved/` | Passed verdict — organised by format |
| `data/not_approved/` | Rejected — audit trail |

---

## Tech Stack

| Tool | Role |
|---|---|
| Python 3.12 | Pipeline orchestration, all step logic |
| Flask + SSE | Review dashboard + real-time log streaming |
| Gemini 2.5 Flash (Vertex AI) | Scoring, keyword generation, caption writing |
| PIL / Pillow | Carousel and story slide rendering |
| MoviePy + FFmpeg | Reel assembly and audio mixing |
| stable-ts | Audio alignment for voice variants |
| feedparser | RSS climate parsing (no auth required) |
| Pexels / Pixabay / Coverr | CC0 footage and photos |
| Freesound / Jamendo / Archive | CC0 audio |

---

## Design Principles

**Step isolation** — each pipeline function returns its output and is independently re-runnable. A failure in step5 does not prevent step6 from running on already-fetched assets.

**Fail-safe downloads** — 9-layer defence chain. A compromised CDN redirect or a malicious API response cannot write arbitrary content to disk.

**Audit trail, not deletion** — rejected posts are moved, never deleted. Every verdict is stored with date, reviewer note, and chosen variant.

**No vendor lock-in on scoring** — the Gemini prompt is a plain string; the model is a single constant. Swapping providers is a one-line change.

**TOS compliance by design** — Pixabay caching is enforced at the API layer. `licence_url` is stored per asset for Content ID audits.

**Persistent learning** — `world_bible.json` turns every run into training signal for the next. High-performing emotional tags compound over time without any external database.

---

## Quickstart

```bash
# Clone
git clone https://github.com/H4nn3ng/Project-MEDIA.git
cd Project-MEDIA

# Virtual environment
python -m venv agent1
source agent1/bin/activate   # Windows: agent1\Scripts\activate
pip install -r requirements.txt

# Secrets
cp .env.example .env
# Fill in your API keys — see table below

# Run the pipeline
python agent.py

# Launch the dashboard
python run_dashboard.py          # → http://localhost:5500
python run_dashboard.py --port 8080 --no-browser
```

### API Keys Required

| Variable | Where to get it |
|---|---|
| `PEXELS_API_KEY` | api.pexels.com |
| `PIXABAY_API_KEY` | pixabay.com/api/docs |
| `COVERR_API_KEY` | coverr.co |
| `FREESOUND_CLIENT_ID` + `_SECRET` | freesound.org/apiv2/apply |
| `JAMENDO_CLIENT_ID` | devportal.jamendo.com |
| `GOOGLE_APPLICATION_CREDENTIALS` | Vertex AI service account JSON |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID |

---

## Project Structure

```
healing-agent/
├── agent.py                   # Full pipeline (step1 → step8, 2 100 lines)
├── editor_carousel.py         # Carousel slide renderer (PIL)
├── editor_story.py            # Story / quote post renderer (PIL)
├── editor_reels.py            # Reel assembler (MoviePy + FFmpeg)
├── editor_shared.py           # Shared PIL utilities
├── feedback.py                # Sunday world_bible updater
├── run_dashboard.py           # Flask entrypoint (port 5500)
│
├── dashboard/
│   ├── app.py                 # All Flask routes
│   ├── final_api.py           # Verdict engine + render detail API
│   ├── review_api.py          # Media pool review API
│   ├── schedule_api.py        # Timeline slot assignment engine
│   ├── state.py               # SSE state + circular log buffer
│   └── static/
│       ├── app.js             # Full SPA — pipeline · review · timeline
│       └── app.css
│
├── config/
│   └── posting_schedule.json  # Slot definitions (day · time window · format)
│
└── data/
    ├── pool/                  # Persistent scored media pool
    ├── pending/               # Rendered, awaiting review
    ├── approved/              # Passed verdict — ready to post
    ├── not_approved/          # Rejected — audit trail
    ├── world_bible.json       # Accumulated channel knowledge
    ├── post_review.json       # Verdict store
    └── schedule_assignments.json
```

---

*Built to run three times a week and stay out of the way. Every decision that requires human judgement surfaces in the dashboard — everything else is automated.*
