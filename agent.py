"""
Healing Agent — 3x-weekly scraper for CC0 footage and audio.
Runs Monday / Wednesday / Friday. Produces a scored batch + intelligence
summary for same-day or next-day content production.
"""

import argparse
import json
import os
import re as _re
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
from google import genai
import requests
from dotenv import load_dotenv
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()


def get_secret(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise EnvironmentError(f"Required secret '{key}' is missing")
    return value


REQUIRED_KEYS = [
    "PEXELS_API_KEY",
    "PIXABAY_API_KEY",
    "COVERR_API_KEY",
    "FREESOUND_CLIENT_SECRET",
    "GOOGLE_CLOUD_PROJECT",
]


def validate_env() -> None:
    missing = [k for k in REQUIRED_KEYS if not os.environ.get(k)]
    if missing:
        for k in missing:
            print(f"[env] Missing required secret: {k}")
        sys.exit(1)


# Keys read once at startup, passed as parameters — never re-read inside functions
def _load_keys() -> dict:
    keys = {k: get_secret(k) for k in REQUIRED_KEYS}
    keys["JAMENDO_API_KEY"]       = os.environ.get("JAMENDO_API_KEY", "")       # optional — skipped if absent
    keys["UNSPLASH_API_KEY"]   = os.environ.get("UNSPLASH_API_KEY", "")   # optional — skipped if absent
    return keys


GEMINI_KEYWORDS_MODEL = "gemini-2.5-flash"               # step3: creative keyword gen — best aesthetic reasoning
GEMINI_SCORE_MODEL    = "gemini-2.5-flash"               # step6: batch scoring — consistent aesthetic judgment
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash-lite-preview"  # fallback when primary is overloaded (attempt 3+)
GEMINI_CAPTION_MODEL  = "gemini-2.5-pro"                 # step3b: caption copywriting — best creative quality
CLIENT        = None  # Gemini client — initialised in main() after validate_env()

# All paths relative to agent.py's own location — safe regardless of launch dir
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"             # all pipeline data lives under data/

RUN_DATE   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
BATCH_ROOT = DATA_DIR / "scrape"
RUN_DIRS = {
    "footage_priority": BATCH_ROOT / "footage" / "priority",
    "footage_standard": BATCH_ROOT / "footage" / "standard",
    "audio_priority":   BATCH_ROOT / "audio"   / "priority",
    "audio_standard":   BATCH_ROOT / "audio"   / "standard",
    "intelligence":     BATCH_ROOT / "intelligence",
}

WORLD_BIBLE_PATH         = DATA_DIR / "world_bible.json"
PIXABAY_CACHE_PATH       = BASE_DIR / ".pixabay_cache.json"    # caches stay at root
PIXABAY_IMAGE_CACHE_PATH = BASE_DIR / ".pixabay_image_cache.json"

POOL_DIR = DATA_DIR / "sort_media"
IMAGE_POOL_DIRS = {
    "images_priority": POOL_DIR / "images" / "priority",
    "images_standard": POOL_DIR / "images" / "standard",
}

SCORE_PRIORITY_THRESHOLD = 8.0   # ≥8.0 → priority/ (carousel-grade)
SCORE_STANDARD_THRESHOLD = 7.0   # 7.0–7.9 → standard/ (story/single-post grade)

# Hard caps per run — prevents uncontrolled disk usage
# Priority slots fill first; standard fills the remainder
MAX_FOOTAGE_DOWNLOADS = 12   # ~300–600 MB per run at medium quality
MAX_AUDIO_DOWNLOADS   = 8    # ~30–60 MB per run
MAX_IMAGE_DOWNLOADS   = 15   # still images — small files, pool accumulates across runs

REDDIT_RSS_FEEDS = [
    "https://www.reddit.com/r/Anxiety/top.rss?t=day",
    "https://www.reddit.com/r/mentalhealth/top.rss?t=day",
    "https://www.reddit.com/r/selfcare/top.rss?t=day",
    "https://www.reddit.com/r/LifeAdvice/top.rss?t=day",
    "https://www.reddit.com/r/emotionalintelligence/top.rss?t=day",
    "https://www.reddit.com/r/CasualConversation/top.rss?t=day",
    "https://www.reddit.com/r/self/top.rss?t=day",
]

TUMBLR_RSS_FEEDS = [
    "https://www.tumblr.com/tagged/healing/rss",
    "https://www.tumblr.com/tagged/nervous-system/rss",
    "https://www.tumblr.com/tagged/inner-child/rss",
    "https://www.tumblr.com/tagged/emotional-healing/rss",
]

GOOGLE_TRENDS_RSS = "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US"

# Words that signal emotional relevance for Google Trends filtering
EMOTIONAL_TERMS = {
    "anxiety", "stress", "heal", "healing", "mental", "calm", "peace",
    "wellness", "self", "care", "grief", "trauma", "therapy", "mindful",
    "burnout", "lonely", "loneliness", "depression", "nervous", "inner",
    "emotional", "feeling", "breath", "breathe", "rest", "tired", "exhausted",
    "overwhelm", "overwhelmed", "recovery", "support", "safe", "comfort",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_world_bible() -> dict:
    if WORLD_BIBLE_PATH.exists():
        with WORLD_BIBLE_PATH.open() as fh:
            return json.load(fh)
    bible: dict = {
        "created": RUN_DATE,
        "last_updated": RUN_DATE,
        "high_performing_keywords": [],
        "low_performing_keywords": [],
        "emotional_hooks_history": [],
        "era_notes": {},
        "channel_learnings": [],
    }
    with WORLD_BIBLE_PATH.open("w") as fh:
        json.dump(bible, fh, indent=2)
    print("[world_bible] Initialised empty world_bible.json")
    return bible


def _save_world_bible(bible: dict) -> None:
    bible["last_updated"] = RUN_DATE
    with WORLD_BIBLE_PATH.open("w") as fh:
        json.dump(bible, fh, indent=2)


def _make_run_dirs() -> None:
    for path in RUN_DIRS.values():
        path.mkdir(parents=True, exist_ok=True)


def _load_pixabay_cache() -> dict:
    if PIXABAY_CACHE_PATH.exists():
        try:
            with PIXABAY_CACHE_PATH.open() as fh:
                data = json.load(fh)
                return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_pixabay_cache(cache: dict) -> None:
    with PIXABAY_CACHE_PATH.open("w") as fh:
        json.dump(cache, fh, indent=2)


def _is_cache_fresh(entry: dict) -> bool:
    """Return True if a cached Pixabay entry is younger than 24 hours."""
    if not isinstance(entry, dict):
        return False
    cached_at = entry.get("_cached_at")
    if not cached_at:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(cached_at)
        return age < timedelta(hours=24)
    except (ValueError, TypeError):
        return False  # Malformed timestamp → treat as stale


def _init_pool_dirs() -> None:
    for path in IMAGE_POOL_DIRS.values():
        path.mkdir(parents=True, exist_ok=True)


def _load_pixabay_image_cache() -> dict:
    if PIXABAY_IMAGE_CACHE_PATH.exists():
        try:
            with PIXABAY_IMAGE_CACHE_PATH.open() as fh:
                data = json.load(fh)
                return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_pixabay_image_cache(cache: dict) -> None:
    with PIXABAY_IMAGE_CACHE_PATH.open("w") as fh:
        json.dump(cache, fh, indent=2)


def _split_into_slides(text: str, max_words: int = 11) -> list[str]:
    """
    Break a therapeutic paragraph into carousel slides.
    Splits on sentence boundaries; long sentences split at a comma or midpoint.
    Each slide ≤ max_words so text fits comfortably on a phone screen.
    """
    import re
    sentences = [s.strip() for s in re.split(r'(?<=[.!?…])\s+', text.strip()) if s.strip()]
    slides: list[str] = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) <= max_words:
            slides.append(sentence)
        else:
            # Try splitting at a comma near the midpoint
            comma_indices = [i for i, w in enumerate(words) if w.endswith(",")]
            mid = len(words) // 2
            split_at = min(comma_indices, key=lambda i: abs(i - mid), default=None)
            if split_at is None:
                split_at = mid
            slides.append(" ".join(words[:split_at + 1]).rstrip(","))
            slides.append(" ".join(words[split_at + 1:]))
    return slides


_IMAGE_MAGIC: list[tuple[bytes, int]] = [
    (b"\xff\xd8\xff",    0),  # JPEG
    (b"\x89PNG\r\n",     0),  # PNG
    (b"RIFF",            0),  # WebP (RIFF....WEBP)
    (b"GIF8",            0),  # GIF
]


def _is_valid_image_file(path: Path) -> bool:
    try:
        header = path.read_bytes()[:16]
    except Exception:
        return False
    for magic, offset in _IMAGE_MAGIC:
        if header[offset: offset + len(magic)] == magic:
            return True
    # WebP: RIFF at 0 + WEBP at 8
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return True
    return False


def _is_emotionally_relevant(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in EMOTIONAL_TERMS)


# Reddit and Tumblr block requests without a browser-like User-Agent (403).
# Fetch via requests first, then parse the response body with feedparser.
_RSS_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"}


def _fetch_rss(url: str):
    try:
        resp = requests.get(url, headers=_RSS_HEADERS, timeout=10)
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except Exception:
        return feedparser.FeedParserDict(entries=[])


# ---------------------------------------------------------------------------
# Step 1 — Emotional climate (RSS only, zero API keys)
# ---------------------------------------------------------------------------

def step1_climate() -> str:
    """
    Pull emotional climate from Reddit RSS, Tumblr RSS, and Google Trends RSS.
    No auth required. Strips usernames and links before any storage.
    Returns raw_climate_this_run: single string, max 2000 chars.
    """
    print("\n[step1] Fetching emotional climate via RSS…")
    fragments: list[str] = []

    # Reddit: top 15 posts per subreddit — raw emotional language
    for url in tqdm(REDDIT_RSS_FEEDS, desc="reddit RSS"):
        try:
            feed = _fetch_rss(url)
            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()
                if title:
                    # strip links (anything starting with http) from summary
                    clean_summary = " ".join(
                        w for w in summary.split() if not w.startswith("http")
                    )
                    fragments.append(title)
                    if clean_summary:
                        fragments.append(clean_summary[:120])
        except Exception as exc:
            print(f"[step1] ERROR reddit RSS {url}: {exc}")

    # Tumblr: top 10 posts per tag — aesthetic and visual language
    for url in tqdm(TUMBLR_RSS_FEEDS, desc="tumblr RSS"):
        try:
            feed = _fetch_rss(url)
            for entry in feed.entries[:10]:
                title = entry.get("title", "").strip()
                if title:
                    fragments.append(title)
        except Exception as exc:
            print(f"[step1] ERROR tumblr RSS {url}: {exc}")

    # Google Trends: top 20, keep only emotionally relevant topics
    try:
        feed = _fetch_rss(GOOGLE_TRENDS_RSS)
        added = 0
        for entry in feed.entries:
            if added >= 20:
                break
            title = entry.get("title", "").strip()
            if title and _is_emotionally_relevant(title):
                fragments.append(f"[trend] {title}")
                added += 1
    except Exception as exc:
        print(f"[step1] ERROR google trends RSS: {exc}")

    combined = " | ".join(fragments)
    raw_climate_this_run = combined[:2000]
    print(f"[step1] Climate string: {len(raw_climate_this_run)} chars")
    return raw_climate_this_run


# ---------------------------------------------------------------------------
# Step 3 — Keyword + emotional hook generation (Gemini API)
# ---------------------------------------------------------------------------

def step3_keywords(
    raw_climate_this_run: str,
    world_bible: dict,
    client: genai.Client,
) -> dict:
    """
    Generate search keywords and emotional hooks via Gemini.
    Aesthetic direction is embedded in the channel essence — no era lock.
    Reads world_bible to avoid repeating low-performers.
    Returns {"keywords": [...], "audio_keywords": [...], "emotional_hooks": [...]}.
    """
    print("\n[step3] Generating keywords via Gemini…")

    high_performers = world_bible.get("high_performing_keywords", [])
    low_performers  = world_bible.get("low_performing_keywords", [])
    high_tags       = world_bible.get("high_performing_tags", [])
    hooks_history   = world_bible.get("emotional_hooks_history", [])

    prompt = f"""You are a content curator for two Instagram healing channels.
You find CC0 (copyright-free) footage and audio for calming, emotionally resonant posts.
The goal: reach people in hard moments and make them feel safe, seen, and soft inside.

## Emotional climate RIGHT NOW (from Reddit/Tumblr/Google Trends):
{raw_climate_this_run}

## Channel essence — non-negotiable:
Warm, slow, muted, atmospheric. Nervous system relief, inner child healing.
Timeless over trendy — universal human softness, not decade-specific nostalgia.
Organic textures, soft light, slow movement. Lo-fi grain is welcome when it
feels natural, not forced. NEVER bright, fast, energetic, hustle, toxic positivity,
or generic wellness stock.

## Keywords that worked before (lean into these themes):
{', '.join(high_performers) if high_performers else 'None yet'}

## Keywords that flopped (avoid these):
{', '.join(low_performers) if low_performers else 'None yet'}

## Visual/audio qualities from highly-rated material (let these inspire new keywords):
{', '.join(high_tags[:25]) if high_tags else 'None yet'}

## Emotional hooks already used (write fresh ones — avoid repeating these):
{', '.join(hooks_history[-20:]) if hooks_history else 'None yet'}

Generate exactly:
1. 15 FOOTAGE KEYWORDS — visual scenes, textures, aesthetics (searched on Pexels/Pixabay video)
2. 10 AUDIO KEYWORDS — sounds, music moods, instruments, nature audio (searched on sound libraries)
3. 5 EMOTIONAL HOOKS — short poetic phrases that resonate with the healing audience

Format your response EXACTLY as:

FOOTAGE KEYWORDS:
keyword1
keyword2
...

AUDIO KEYWORDS:
keyword1
keyword2
...

EMOTIONAL HOOKS:
hook1
hook2
..."""

    keywords: list[str] = []
    audio_keywords: list[str] = []
    emotional_hooks: list[str] = []

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=GEMINI_KEYWORDS_MODEL, contents=prompt)
            text = response.text
            break
        except Exception as exc:
            error_str = str(exc)
            is_day_limit   = "429" in error_str and "PerDay" in error_str
            is_minute_limit = "429" in error_str and "PerMinute" in error_str
            is_exhausted   = "RESOURCE_EXHAUSTED" in error_str
            is_overload    = "503" in error_str or "UNAVAILABLE" in error_str
            if is_day_limit or (is_exhausted and attempt >= 2):
                print("[step3] QUOTA EXHAUSTED — check Vertex AI quotas at console.cloud.google.com")
                return {"keywords": [], "audio_keywords": [], "emotional_hooks": []}
            if (is_minute_limit or is_overload or is_exhausted) and attempt < 2:
                wait = 20 if is_overload else 65
                print(f"[step3] Gemini busy — waiting {wait}s (attempt {attempt + 1}/3)…")
                time.sleep(wait)
                continue
            print(f"[step3] ERROR calling Gemini: {exc}")
            return {"keywords": [], "audio_keywords": [], "emotional_hooks": []}
    else:
        print("[step3] Gemini unavailable after 3 attempts — aborting run")
        return {"keywords": [], "audio_keywords": [], "emotional_hooks": []}

    if "FOOTAGE KEYWORDS:" in text:
        section = text.split("FOOTAGE KEYWORDS:")[1].split("AUDIO KEYWORDS:")[0]
        keywords = [
            k.strip()
            for k in section.strip().split("\n")
            if k.strip() and not k.startswith("#")
        ]

    if "AUDIO KEYWORDS:" in text:
        section = text.split("AUDIO KEYWORDS:")[1].split("EMOTIONAL HOOKS:")[0]
        audio_keywords = [
            k.strip()
            for k in section.strip().split("\n")
            if k.strip() and not k.startswith("#")
        ]

    if "EMOTIONAL HOOKS:" in text:
        hooks_section = text.split("EMOTIONAL HOOKS:")[1]
        emotional_hooks = [
            h.strip()
            for h in hooks_section.strip().split("\n")
            if h.strip() and not h.startswith("#")
        ]

    print(f"[step3] Generated {len(keywords)} footage keywords, {len(audio_keywords)} audio keywords, {len(emotional_hooks)} hooks")

    if not keywords:
        print("[step3] WARNING: 0 footage keywords — step4 will return no candidates")
    if not audio_keywords:
        print("[step3] WARNING: 0 audio keywords — step5 will return no candidates")

    return {"keywords": keywords, "audio_keywords": audio_keywords, "emotional_hooks": emotional_hooks}


# ---------------------------------------------------------------------------
# Step 3b — Carousel scripts + power quotes (Gemini)
# ---------------------------------------------------------------------------

def _generate_captions(
    carousels: list[dict],
    quotes: list[str],
    raw_climate: str,
    client: genai.Client,
) -> list[list[str]]:
    """
    Write Instagram captions for carousels and per-quote hashtag lists via gemini-2.5-pro.
    Attaches 'instagram_caption' to each carousel dict in-place.
    Returns quote_hashtags: list[list[str]] — one hashtag list per quote, index-aligned.
    Non-fatal: on failure attaches empty strings / returns empty lists.
    """
    print("\n[step3b] Writing Instagram captions via Gemini Pro…")

    carousel_context = "\n".join(
        f"CAROUSEL {i + 1} THEME: {c.get('theme', '')}\n"
        f"CAROUSEL {i + 1} OPENING: {c.get('slides', [''])[0][:120]}"
        for i, c in enumerate(carousels)
    )
    quote_context = "\n".join(
        f"QUOTE {i + 1}: {q[:120]}" for i, q in enumerate(quotes)
    )
    num_quotes = len(quotes)
    # Build quote hashtag template lines for the prompt
    quote_template = "\n\n".join(
        f"QUOTE {i + 1} HASHTAGS:\n[15–18 #hashtags space-separated, matching this quote's emotional register]"
        for i in range(num_quotes)
    )

    prompt = f"""You write Instagram captions for a soft healing account.
Audience: people in quiet pain — anxious, exhausted, grieving, or just needing a moment.
Voice: warm, poetic, unhurried. No exclamation marks. No toxic positivity. No "you've got this."

## Emotional climate this week:
{raw_climate[:400]}

## Carousels to caption:
{carousel_context}

## Story quotes needing hashtags:
{quote_context}

---

Write EXACTLY this structure — use these exact headers, nothing before the first header:

CAROUSEL 1 CAPTION:
[2–3 sentences that tease the carousel without giving it away. Make the reader want to swipe.
End with one soft engagement line such as "Save this for when you need it" or "Drop a 🌿 if this is you."
Then a blank line, then 15–18 relevant #hashtags on one line.]

CAROUSEL 2 CAPTION:
[same structure — different angle and different CTA variant]

CAROUSEL 3 CAPTION:
[same structure]

{quote_template}

---
No preamble. No text outside the sections listed above."""

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=GEMINI_CAPTION_MODEL, contents=prompt)
            text = response.text
            break
        except Exception as exc:
            error_str = str(exc)
            is_overload = "503" in error_str or "UNAVAILABLE" in error_str
            is_limit    = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str
            if is_limit and attempt >= 2:
                print("[step3b] Caption quota exhausted — skipping caption generation")
                return []
            if (is_overload or is_limit) and attempt < 2:
                wait = 30 if is_overload else 65
                print(f"[step3b] Gemini Pro busy — waiting {wait}s (attempt {attempt + 1}/3)…")
                time.sleep(wait)
                continue
            print(f"[step3b] Caption generation failed: {exc}")
            return []
    else:
        return []

    # All section headers in order — used to find boundaries when parsing
    all_headers = (
        [f"CAROUSEL {i + 1} CAPTION:" for i in range(len(carousels))]
        + [f"QUOTE {i + 1} HASHTAGS:" for i in range(num_quotes)]
    )

    def _extract(header: str, after_index: int) -> str:
        if header not in text:
            return ""
        segment = text.split(header)[1]
        for later in all_headers[after_index + 1:]:
            if later in segment:
                segment = segment.split(later)[0]
                break
        return segment.strip()

    # Attach carousel captions in-place
    for i, c in enumerate(carousels):
        caption = _extract(f"CAROUSEL {i + 1} CAPTION:", i)
        if caption:
            c["instagram_caption"] = caption

    # Parse per-quote hashtag lists
    quote_hashtags: list[list[str]] = []
    for i in range(num_quotes):
        raw_tags = _extract(f"QUOTE {i + 1} HASHTAGS:", len(carousels) + i)
        words = raw_tags.split()
        tags  = [f"#{w.lstrip('#')}" for w in words if w.lstrip('#').replace('_', '').isalnum()]
        quote_hashtags.append(tags[:18])

    attached = sum(1 for c in carousels if c.get("instagram_caption"))
    print(f"[step3b] Captions attached: {attached} carousel(s), {len(quote_hashtags)} quote hashtag list(s)")
    return quote_hashtags


def step3b_content(
    keywords_data: dict,
    raw_climate: str,
    client: genai.Client,
) -> dict:
    """
    Generate 3 therapeutic carousel scripts and 10 one-liner power quotes.

    Carousel format: each script is a short paragraph designed for 4–7 swipe-able
    slides. The opening line creates a pause; the closing line offers resolution.

    Power quotes: single punchy sentences for stories or single-image posts.

    Returns {"carousels": [{"theme": str, "slides": [str, ...]}, ...], "quotes": [str, ...]}.
    """
    print("\n[step3b] Generating carousel scripts + quotes via Gemini…")

    hooks = keywords_data.get("emotional_hooks", [])
    keywords = keywords_data.get("keywords", [])

    prompt = f"""You are writing content for two soft, healing Instagram channels.
Audience: people in hard moments — anxious, exhausted, grieving, or just quietly struggling.
Tone: warm, poetic, unhurried. Never toxic positivity. No exclamation marks. No "you've got this."
Style: conversational therapy meets soft journaling. Sentences breathe.

## Emotional climate right now:
{raw_climate[:600]}

## Visual direction these posts will use:
{', '.join(keywords[:8]) if keywords else 'slow nature, soft light, rain, quiet spaces'}

## Emotional hooks from this run (let them inspire the writing):
{chr(10).join(hooks) if hooks else ''}

---

Generate EXACTLY this structure:

CAROUSEL 1:
[Write one paragraph of 4–6 sentences. Opening line = a gentle mirror of what people feel.
Middle = a small reframe or permission slip. Closing = a soft landing. 40–70 words total.]

CAROUSEL 2:
[Different angle — maybe body / nervous system / rest. Same paragraph structure.]

CAROUSEL 3:
[Different angle again — maybe self-compassion / inner child / being enough. Same structure.]

STORY QUOTES:
[10 standalone quotes for single story posts. 2–3 sentences each. 20–35 words total per quote.
One clear emotional idea per quote — open with a feeling, close with a gentle permission or reframe.
No bullet points. No numbering. No quotes around them. One blank line between each quote.
These are read as a standalone image — they need enough weight to land without context.]

---
Important: use the exact headers CAROUSEL 1:, CAROUSEL 2:, CAROUSEL 3:, POWER QUOTES:
Do not add any other text before or after."""

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=GEMINI_KEYWORDS_MODEL, contents=prompt)
            text = response.text
            break
        except Exception as exc:
            error_str = str(exc)
            is_overload = "503" in error_str or "UNAVAILABLE" in error_str
            is_limit    = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str
            if is_limit and attempt >= 2:
                print("[step3b] Quota exhausted — skipping content generation")
                return {"carousels": [], "quotes": []}
            if (is_overload or is_limit) and attempt < 2:
                wait = 30 if is_overload else 65
                print(f"[step3b] Gemini busy — waiting {wait}s (attempt {attempt + 1}/3)…")
                time.sleep(wait)
                continue
            print(f"[step3b] ERROR: {exc}")
            return {"carousels": [], "quotes": []}
    else:
        return {"carousels": [], "quotes": []}

    carousels: list[dict] = []
    quotes: list[str] = []

    for n in range(1, 4):
        header = f"CAROUSEL {n}:"
        next_header = f"CAROUSEL {n + 1}:" if n < 3 else "POWER QUOTES:"
        if header in text and next_header in text:
            raw = text.split(header)[1].split(next_header)[0].strip()
        elif header in text:
            raw = text.split(header)[1].strip()
        else:
            continue
        if raw:
            slides = _split_into_slides(raw)
            carousels.append({"theme": hooks[n - 1] if n - 1 < len(hooks) else f"carousel_{n}", "slides": slides})

    for quotes_header in ("STORY QUOTES:", "POWER QUOTES:"):
        if quotes_header in text:
            raw_quotes = text.split(quotes_header)[1].strip()
            # Split on blank lines — each quote is a paragraph (2–3 sentences)
            blocks = [b.strip().strip('"').strip("'") for b in raw_quotes.split("\n\n") if b.strip()]
            if blocks:
                quotes = blocks[:10]
                break
            # Fallback: line-by-line (old one-liner format)
            quotes = [
                q.strip().strip('"').strip("'")
                for q in raw_quotes.split("\n")
                if q.strip() and not q.strip().startswith("#")
            ][:10]
            break

    print(f"[step3b] Generated {len(carousels)} carousels, {len(quotes)} quotes")
    for i, c in enumerate(carousels):
        theme_preview = c['theme'][:50]
        print(f'[step3b]   carousel {i+1}: {len(c["slides"])} slides — "{theme_preview}"')

    # Write proper Instagram captions via Pro — non-fatal if it fails
    quote_hashtags: list[list[str]] = []
    if carousels or quotes:
        quote_hashtags = _generate_captions(carousels, quotes, raw_climate, client)

    return {"carousels": carousels, "quotes": quotes, "quote_hashtags": quote_hashtags}


# ---------------------------------------------------------------------------
# Step 4 — Footage search (Pexels → Pixabay → Coverr)
# ---------------------------------------------------------------------------

def step4_footage(keywords: list[str], keys: dict) -> list[dict]:
    """
    Search all three footage sources in priority order.
    Each candidate dict includes source, licence_url, and full API response
    for audit trail.
    Returns raw_footage_candidates: list of candidate dicts.
    """
    print("\n[step4] Searching footage sources…")
    raw_footage_candidates: list[dict] = []
    pixabay_cache = _load_pixabay_cache()

    for keyword in tqdm(keywords, desc="footage keywords"):
        # Pexels first — best quality
        candidates = _search_pexels_videos(keyword, keys["PEXELS_API_KEY"])
        for c in candidates: c["keyword"] = keyword
        raw_footage_candidates.extend(candidates)

        # Pixabay second — with caching (TOS requirement)
        candidates = _search_pixabay_videos(keyword, keys["PIXABAY_API_KEY"], pixabay_cache)
        for c in candidates: c["keyword"] = keyword
        raw_footage_candidates.extend(candidates)

        # Coverr third — fallback
        candidates = _search_coverr_videos(keyword, keys["COVERR_API_KEY"])
        for c in candidates: c["keyword"] = keyword
        raw_footage_candidates.extend(candidates)

    _save_pixabay_cache(pixabay_cache)
    print(f"[step4] Found {len(raw_footage_candidates)} footage candidates")
    return raw_footage_candidates


def _search_pexels_videos(keyword: str, api_key: str) -> list[dict]:
    """Portrait-orientation videos, 15 per keyword. Best quality — curated, no AI."""
    candidates: list[dict] = []
    try:
        url = "https://api.pexels.com/videos/search"
        headers = {"Authorization": api_key}
        params = {"query": keyword, "orientation": "portrait", "per_page": 15}
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        for video in data.get("videos", []):
            # Pick the smallest file that is still HD (720p) to control disk usage.
            # Pexels returns video_files sorted largest-first; we find the lowest
            # quality that is still >= 720px wide so the file is usable but not huge.
            files = video.get("video_files", [])
            chosen = next(
                (f for f in reversed(files) if (f.get("width") or 0) >= 720),
                files[0] if files else {}
            )
            # Pexels videos rarely have a name field — extract description from the
            # page URL slug instead (e.g. "pexels.com/video/rain-on-window-6366602/"
            # → "Rain On Window"), which gives Gemini something real to score.
            page_url = video.get("url", "")
            if page_url:
                slug = page_url.rstrip("/").rsplit("/", 1)[-1]
                parts = slug.rsplit("-", 1)
                title = parts[0].replace("-", " ").title() if (len(parts) == 2 and parts[1].isdigit()) else keyword
            else:
                title = video.get("name", keyword)
            candidate = {
                "source": "pexels",
                "id": video.get("id"),
                "title": title,
                "url": page_url,
                "download_url": chosen.get("link"),
                "licence_url": "https://www.pexels.com/license/",
                "platform_tags": [],  # Pexels has no tag field on videos
                "full_response": video,
            }
            if candidate["download_url"]:
                candidates.append(candidate)
    except Exception as exc:
        print(f"[step4] ERROR Pexels {keyword}: {exc}")

    return candidates


def _search_pixabay_videos(keyword: str, api_key: str, cache: dict) -> list[dict]:
    """
    Pixabay secondary source. Responses cached to disk (TOS: 24-hr cache required).
    Contains disclosed AI-generated content — scorer must filter.
    """
    candidates: list[dict] = []
    cache_key = f"pixabay_videos_{keyword}"

    cached = cache.get(cache_key)
    if cached and _is_cache_fresh(cached):
        data = cached["data"]
        print(f"[step4] Pixabay {keyword} (cached)")
    else:
        try:
            url = "https://pixabay.com/api/videos/"
            params = {"key": api_key, "q": keyword, "video_type": "film", "per_page": 5}
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            cache[cache_key] = {"data": data, "_cached_at": datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            print(f"[step4] ERROR Pixabay {keyword}: {exc}")
            return candidates

    for video in data.get("hits", []):
        # tags is a comma-separated string; split into a list for world_bible learning
        tags_str = video.get("tags", "")
        platform_tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        # Use first 3 tags as a short title — the full tag string is too long to read
        title_parts = platform_tags[:3]
        title = ", ".join(title_parts) if title_parts else keyword
        candidate = {
            "source": "pixabay",
            "id": video.get("id"),
            "title": title,
            "url": video.get("pageURL"),
            "download_url": video.get("videos", {}).get("medium", {}).get("url"),
            "licence_url": "https://pixabay.com/service/license/",
            "platform_tags": platform_tags,
            "full_response": video,
        }
        if candidate["download_url"]:
            candidates.append(candidate)

    return candidates


def _search_coverr_videos(keyword: str, api_key: str) -> list[dict]:
    """
    Coverr tertiary source. Auth uses Bearer header (not query param).
    Videos delivered via Mux: download URL = stream.mux.com/{playback_id}/high.mp4.
    Filters out AI-generated and landscape videos client-side.
    """
    candidates: list[dict] = []
    try:
        url = "https://api.coverr.co/videos"
        headers = {"Authorization": f"Bearer {api_key}"}
        params = {"keywords": keyword, "number": 10}
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        for video in data.get("hits", []):
            if video.get("is_ai_generated"):
                continue
            playback_id = video.get("playback_id", "")
            if not playback_id:
                continue
            if not _re.match(r'^[a-zA-Z0-9_-]+$', str(playback_id)):
                continue  # Reject non-alphanumeric playback IDs
            slug = str(video.get("slug", ""))
            safe_slug = _re.sub(r"[^\w\-]", "_", slug)
            candidate = {
                "source": "coverr",
                "id": video.get("id", video.get("slug")),
                "title": video.get("title", keyword),
                "url": f"https://coverr.co/videos/{safe_slug}",
                "download_url": f"https://stream.mux.com/{playback_id}/high.mp4",
                "licence_url": "https://coverr.co/license",
                "platform_tags": [],  # Coverr API does not expose a tags field
                "full_response": video,
            }
            candidates.append(candidate)
    except Exception as exc:
        print(f"[step4] WARNING Coverr {keyword}: {exc}")

    return candidates


# ---------------------------------------------------------------------------
# Step 4b — Still image search (Pexels photos → Pixabay photos → Unsplash)
# ---------------------------------------------------------------------------

def step4b_images(keywords: list[str], keys: dict) -> list[dict]:
    """
    Search for CC0 / free-use still images for carousel post backgrounds.
    Portrait orientation preferred (9:16 for Instagram stories/carousels).
    Pexels and Pixabay reuse existing auth. Unsplash is optional (skip if no key).
    Returns raw_image_candidates: list of candidate dicts.
    """
    print("\n[step4b] Searching for still images…")
    raw_image_candidates: list[dict] = []
    pixabay_cache = _load_pixabay_image_cache()
    unsplash_key = keys.get("UNSPLASH_API_KEY", "")

    for keyword in keywords:
        candidates = _search_pexels_photos(keyword, keys["PEXELS_API_KEY"])
        for c in candidates: c["keyword"] = keyword
        raw_image_candidates.extend(candidates)

        candidates = _search_pixabay_photos(keyword, keys["PIXABAY_API_KEY"], pixabay_cache)
        for c in candidates: c["keyword"] = keyword
        raw_image_candidates.extend(candidates)

        if unsplash_key:
            candidates = _search_unsplash_photos(keyword, unsplash_key)
            for c in candidates: c["keyword"] = keyword
            raw_image_candidates.extend(candidates)

    _save_pixabay_image_cache(pixabay_cache)
    if not unsplash_key:
        print("[step4b] NOTE: UNSPLASH_API_KEY not set — add to agent1/bin/activate for Unsplash results")
    print(f"[step4b] Found {len(raw_image_candidates)} image candidates")
    return raw_image_candidates


def _search_pexels_photos(keyword: str, api_key: str) -> list[dict]:
    """Portrait photos from Pexels. 8 per keyword — curated, human editorial."""
    candidates: list[dict] = []
    try:
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": api_key}
        params = {"query": keyword, "orientation": "portrait", "per_page": 8, "size": "medium"}
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        for photo in resp.json().get("photos", []):
            src = photo.get("src", {})
            download_url = src.get("large2x") or src.get("large") or src.get("medium")
            if not download_url:
                continue
            candidates.append({
                "source": "pexels_photo",
                "id": photo.get("id"),
                "title": photo.get("alt") or keyword,
                "url": photo.get("url"),
                "download_url": download_url,
                "licence_url": "https://www.pexels.com/license/",
                "platform_tags": [],
                "media_type": "image",
                "full_response": {"id": photo.get("id"), "alt": photo.get("alt")},
            })
    except Exception as exc:
        print(f"[step4b] ERROR Pexels photos {keyword}: {exc}")
    return candidates


def _search_pixabay_photos(keyword: str, api_key: str, cache: dict) -> list[dict]:
    """
    Portrait photos from Pixabay. Cached to disk (same 24-hr TOS requirement as videos).
    Marks AI-generated content in candidate so scorer can penalise.
    """
    candidates: list[dict] = []
    cache_key = f"pixabay_photos_{keyword}"

    cached = cache.get(cache_key)
    if cached and _is_cache_fresh(cached):
        data = cached["data"]
    else:
        try:
            params = {
                "key": api_key, "q": keyword,
                "image_type": "photo", "orientation": "vertical",
                "per_page": 5, "safesearch": "true",
            }
            resp = requests.get("https://pixabay.com/api/", params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            cache[cache_key] = {"data": data, "_cached_at": datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            print(f"[step4b] ERROR Pixabay photos {keyword}: {exc}")
            return candidates

    for photo in data.get("hits", []):
        tags_str = photo.get("tags", "")
        platform_tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        title = ", ".join(platform_tags[:3]) if platform_tags else keyword
        download_url = photo.get("largeImageURL") or photo.get("webformatURL")
        if not download_url:
            continue
        candidates.append({
            "source": "pixabay_photo",
            "id": photo.get("id"),
            "title": title,
            "url": photo.get("pageURL"),
            "download_url": download_url,
            "licence_url": "https://pixabay.com/service/license/",
            "platform_tags": platform_tags,
            "media_type": "image",
            "full_response": {"id": photo.get("id"), "tags": tags_str},
        })
    return candidates


def _search_unsplash_photos(keyword: str, access_key: str) -> list[dict]:
    """
    Curated editorial photos from Unsplash (Unsplash License — commercial use allowed).
    Portrait orientation, 5 per keyword. Requires UNSPLASH_API_KEY.
    """
    candidates: list[dict] = []
    try:
        url = "https://api.unsplash.com/search/photos"
        headers = {"Authorization": f"Client-ID {access_key}"}
        params = {"query": keyword, "orientation": "portrait", "per_page": 5}
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        for photo in resp.json().get("results", []):
            urls = photo.get("urls", {})
            download_url = urls.get("regular") or urls.get("small")
            if not download_url:
                continue
            candidates.append({
                "source": "unsplash",
                "id": photo.get("id"),
                "title": photo.get("description") or photo.get("alt_description") or keyword,
                "url": photo.get("links", {}).get("html", ""),
                "download_url": download_url,
                "licence_url": "https://unsplash.com/license",
                "platform_tags": [t.get("title", "") for t in photo.get("tags", [])],
                "media_type": "image",
                "full_response": {"id": photo.get("id"), "description": photo.get("description")},
            })
    except Exception as exc:
        print(f"[step4b] ERROR Unsplash {keyword}: {exc}")
    return candidates


# ---------------------------------------------------------------------------
# Step 5 — Audio search (Jamendo → Freesound music → Freesound sounds → Internet Archive)
# ---------------------------------------------------------------------------

def step5_audio(keywords: list[str], keys: dict) -> list[dict]:
    """
    Search audio sources in priority order.
    Jamendo: primary — dedicated CC music library, 600k+ tracks, mood/genre filtering.
    Freesound music: CC0 composed tracks only (type:music filter).
    Freesound sounds: ambient/nature/atmosphere (no type filter).
    Internet Archive: public domain fallback, no auth needed.
    Returns raw_audio_candidates: list of candidate dicts.
    """
    print("\n[step5] Searching audio sources…")
    raw_audio_candidates: list[dict] = []
    jamendo_key = keys.get("JAMENDO_API_KEY", "")

    for keyword in tqdm(keywords, desc="audio keywords"):
        # Jamendo first — best source for composed CC music tracks
        if jamendo_key:
            candidates = _search_jamendo(keyword, jamendo_key)
            for c in candidates: c["keyword"] = keyword
            raw_audio_candidates.extend(candidates)

        # Freesound music — CC0 composed tracks only
        candidates = _search_freesound_music(keyword, keys["FREESOUND_CLIENT_SECRET"])
        for c in candidates: c["keyword"] = keyword
        raw_audio_candidates.extend(candidates)

        # Freesound sounds — ambient/nature/atmosphere
        candidates = _search_freesound_sounds(keyword, keys["FREESOUND_CLIENT_SECRET"])
        for c in candidates: c["keyword"] = keyword
        raw_audio_candidates.extend(candidates)

        # Internet Archive — public domain fallback
        candidates = _search_internet_archive(keyword)
        for c in candidates: c["keyword"] = keyword
        raw_audio_candidates.extend(candidates)

    if not jamendo_key:
        print("[step5] NOTE: JAMENDO_API_KEY not set — add it to agent1/bin/activate for music tracks")
    print(f"[step5] Found {len(raw_audio_candidates)} audio candidates")
    return raw_audio_candidates


def _search_jamendo(keyword: str, api_key: str) -> list[dict]:
    """
    Primary music source. Jamendo hosts 600k+ CC-licensed tracks.
    Uses free client_id auth. Returns tracks where audio download is permitted.
    """
    candidates: list[dict] = []
    try:
        url = "https://api.jamendo.com/v3.0/tracks/"
        params = {
            "client_id": api_key,
            "format": "json",
            "limit": 5,
            "search": keyword,
            "audiodownload_allowed": "true",
            "order": "relevance_desc",
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        for track in data.get("results", []):
            download_url = track.get("audio") or track.get("audiodownload")
            if not download_url:
                continue
            tags_raw = track.get("tags", "")
            platform_tags = [t.strip() for t in tags_raw.replace("+", " ").split() if t.strip()]
            candidate = {
                "source": "jamendo",
                "id": track.get("id"),
                "title": f"{track.get('name', keyword)} — {track.get('artist_name', '')}".strip(" —"),
                "url": track.get("shareurl", ""),
                "download_url": download_url,
                "licence_url": track.get("license_ccurl", "https://creativecommons.org/licenses/by/3.0/"),
                "platform_tags": platform_tags,
                "full_response": {k: v for k, v in track.items() if k != "audio"},
            }
            candidates.append(candidate)
    except Exception as exc:
        print(f"[step5] ERROR Jamendo {keyword}: {exc}")
    return candidates


def _search_freesound_music(keyword: str, api_key: str) -> list[dict]:
    """CC0 composed music tracks from Freesound (type:music filter excludes field recordings)."""
    candidates: list[dict] = []
    try:
        params = {
            "query": keyword,
            "token": api_key,
            "filter": 'license:"Creative Commons 0" type:music',
            "fields": "id,name,url,previews,duration,tags,license",
            "page_size": 5,
        }
        resp = requests.get("https://freesound.org/apiv2/search/text/", params=params, timeout=10)
        resp.raise_for_status()
        for sound in resp.json().get("results", []):
            dl = sound.get("previews", {}).get("preview-hq-mp3")
            if not dl:
                continue
            candidates.append({
                "source": "freesound",
                "id": sound.get("id"),
                "title": sound.get("name", keyword),
                "url": sound.get("url"),
                "download_url": dl,
                "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "platform_tags": sound.get("tags", []),
                "full_response": sound,
            })
    except Exception as exc:
        print(f"[step5] ERROR Freesound music {keyword}: {exc}")
    return candidates


def _search_freesound_sounds(keyword: str, api_key: str) -> list[dict]:
    """
    CC0 ambient/nature/atmosphere sounds from Freesound.
    No type filter — returns field recordings, loops, sfx alongside music.
    Uses preview MP3s since full downloads require OAuth2.
    """
    candidates: list[dict] = []
    try:
        search_url = "https://freesound.org/apiv2/search/text/"
        params = {
            "query": keyword,
            "token": api_key,
            "filter": 'license:"Creative Commons 0"',
            "fields": "id,name,url,previews,duration,tags,license",
            "page_size": 5,
        }
        resp = requests.get(search_url, params=params, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        for sound in data.get("results", []):
            candidate = {
                "source": "freesound",
                "id": sound.get("id"),
                "title": sound.get("name", keyword),
                "url": sound.get("url"),
                "download_url": sound.get("previews", {}).get("preview-hq-mp3"),
                "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "platform_tags": sound.get("tags", []),
                "full_response": sound,
            }
            if candidate["download_url"]:
                candidates.append(candidate)
    except Exception as exc:
        print(f"[step5] ERROR Freesound {keyword}: {exc}")

    return candidates


def _search_internet_archive(keyword: str) -> list[dict]:
    """
    CC-licensed audio from Internet Archive.
    Accepts CC0 and CC-BY (both safe for commercial use; BY requires credit).
    Download URL uses IA's standard pattern: {identifier}/{identifier}.mp3.
    """
    candidates: list[dict] = []
    try:
        url = "https://archive.org/advancedsearch.php"
        params = {
            "q": f'mediatype:audio ({keyword}) licenseurl:*creativecommons*',
            "output": "json",
            "rows": 5,
            "fl[]": ["identifier", "title", "licenseurl"],
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        for doc in data.get("response", {}).get("docs", []):
            identifier = doc.get("identifier", "")
            if not identifier:
                continue
            if not _re.match(r'^[a-zA-Z0-9_.-]+$', str(identifier)):
                continue  # Reject identifiers with path traversal characters
            licence = doc.get("licenseurl", "https://creativecommons.org/publicdomain/zero/1.0/")
            candidate = {
                "source": "internet_archive",
                "platform_tags": [],
                "id": identifier,
                "title": doc.get("title", keyword),
                "url": f"https://archive.org/details/{identifier}",
                "download_url": f"https://archive.org/download/{identifier}/{identifier}.mp3",
                "licence_url": licence,
                "full_response": doc,
            }
            candidates.append(candidate)
    except Exception as exc:
        print(f"[step5] ERROR Internet Archive {keyword}: {exc}")

    return candidates


# ---------------------------------------------------------------------------
# Step 6 — Scoring (Gemini API, batches of 20)
# ---------------------------------------------------------------------------

def step6_score(
    footage_candidates: list[dict],
    audio_candidates: list[dict],
    client: genai.Client,
    footage_keywords: list[str],
    audio_keywords: list[str],
) -> tuple[list[dict], list[dict]]:
    """
    Score all candidates via Gemini in batches of 20.
    Each item gains "score" and "matched_keywords" — the subset of this run's
    keywords that Gemini says genuinely describe the item. This lets a single
    file accumulate feedback across multiple relevant keywords in world_bible,
    not just the one search term that happened to find it first.
    Returns (scored_footage, scored_audio).
    """
    BATCH_SIZE = 20
    print("\n[step6] Scoring candidates…")

    def _dedup(candidates: list[dict]) -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []
        for c in candidates:
            key = c.get("download_url", "")
            if key and key not in seen:
                seen.add(key)
                out.append(c)
        return out

    footage_candidates = _dedup(footage_candidates)
    audio_candidates   = _dedup(audio_candidates)
    print(f"[step6] After dedup: {len(footage_candidates)} footage, {len(audio_candidates)} audio to score")

    scored_footage: list[dict] = []
    scored_audio: list[dict] = []

    for i in tqdm(range(0, len(footage_candidates), BATCH_SIZE), desc="footage batches"):
        batch = footage_candidates[i : i + BATCH_SIZE]
        scored_footage.extend(_score_batch(batch, "footage", client, footage_keywords))

    for i in tqdm(range(0, len(audio_candidates), BATCH_SIZE), desc="audio batches"):
        batch = audio_candidates[i : i + BATCH_SIZE]
        scored_audio.extend(_score_batch(batch, "audio", client, audio_keywords))

    print(f"[step6] Scored {len(scored_footage)} footage, {len(scored_audio)} audio")
    return scored_footage, scored_audio


def _score_batch(
    items: list[dict],
    media_type: str,
    client: genai.Client,
    run_keywords: list[str],
) -> list[dict]:
    """
    Score one batch of ≤20 items via Gemini and attach scores + matched keywords.
    Also asks Gemini which run keywords describe each item so a single file can
    accumulate feedback across multiple relevant keywords, not just the one that
    happened to find it.
    Retries once on per-minute 429.
    """
    scored: list[dict] = []

    if not items:
        return scored

    kw_set = set(run_keywords)  # for validating Gemini's output

    items_text = "\n".join(
        [f"{i+1}. [{item['source']}] {item['title']}" for i, item in enumerate(items)]
    )
    keywords_text = ", ".join(run_keywords) if run_keywords else "none"

    prompt = f"""You are scoring {media_type} for an Instagram healing channel.

Channel essence: Warm, slow, muted, atmospheric. Nervous system relief, inner child healing.
Lo-fi / VHS grain / Ghibli-adjacent.

Score 1-10. Be strict — most items should score 4–7. Reserve 9–10 for content that is
genuinely exceptional: clearly atmospheric, slow, and emotionally resonant. Reserve 8–8.9
for strong matches. Give 7–7.9 to acceptable-but-generic content. Below 7 for anything
bright, energetic, stock-generic, or AI-generated.

Scoring criteria:
- Aesthetic match (calm, atmospheric, vintage vibes)
- Nervous system appeal (soothing, not stimulating)
- Usability (identifiable content — penalise abstract/chaotic)
- AI-generated content (Pixabay sometimes discloses this) → max 5

After each score, add " | " then comma-separated keywords from the list below that
genuinely describe the item. Only include keywords that truly fit. Omit " | " entirely
if none apply.

Keywords to match against: {keywords_text}

Rate each:
{items_text}

Response format EXACTLY:
1. 8.5 | rainy window, soft focus nature
2. 7.2
3. 9.0 | VHS grain overlay
...
(one line per item — score first, then optionally " | keyword1, keyword2")"""

    _OVERLOAD_WAITS = [15, 30, 60, 90]  # seconds between retries on 503

    for attempt in range(5):
        model = GEMINI_FALLBACK_MODEL if attempt >= 3 else GEMINI_SCORE_MODEL
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            scores_text = response.text.strip()
            parsed: list[tuple[float, list[str]]] = []

            for line in scores_text.split("\n"):
                parts = line.strip().split(". ", 1)
                if len(parts) != 2:
                    continue
                rest = parts[1].strip()
                if " | " in rest:
                    score_part, kw_part = rest.split(" | ", 1)
                    # Only keep keywords Gemini was actually given — no hallucinations
                    matched = [k.strip() for k in kw_part.split(",") if k.strip() in kw_set]
                else:
                    score_part = rest
                    matched = []
                try:
                    parsed.append((float(score_part.strip()), matched))
                except ValueError:
                    pass

            for i, item in enumerate(items):
                scored_item = item.copy()
                if i < len(parsed):
                    scored_item["score"]            = parsed[i][0]
                    scored_item["matched_keywords"] = parsed[i][1]
                else:
                    scored_item["score"]            = 5.0
                    scored_item["matched_keywords"] = []
                scored.append(scored_item)
            return scored

        except Exception as exc:
            error_str = str(exc)
            is_overload     = "503" in error_str or "UNAVAILABLE" in error_str
            is_minute_limit = "429" in error_str and "PerMinute" in error_str
            is_day_limit    = "429" in error_str and "PerDay" in error_str
            is_exhausted    = "RESOURCE_EXHAUSTED" in error_str

            if is_day_limit or (is_exhausted and attempt >= 3):
                # Daily quota exhausted — retrying is pointless until midnight reset
                print(f"[step6] DAILY QUOTA EXHAUSTED for {model}. "
                      f"Quota resets at midnight UTC. Using fallback score 5.0 for remaining batches.")
                for item in items:
                    scored_item = item.copy()
                    scored_item["score"]            = 5.0
                    scored_item["matched_keywords"] = []
                    scored.append(scored_item)
                return scored

            if attempt == 4:
                print(f"[step6] Batch failed after 5 attempts — using fallback score 5.0")
                for item in items:
                    scored_item = item.copy()
                    scored_item["score"]            = 5.0
                    scored_item["matched_keywords"] = []
                    scored.append(scored_item)
                return scored

            if is_minute_limit or (is_exhausted and attempt < 3):
                wait = 65 if is_minute_limit else 30
                print(f"[step6] Rate limit — waiting {wait}s (attempt {attempt + 1}/5)…")
                time.sleep(wait)
            elif is_overload:
                wait = _OVERLOAD_WAITS[attempt]
                note = f" — switching to {GEMINI_FALLBACK_MODEL}" if attempt == 3 else ""
                print(f"[step6] Gemini overloaded — waiting {wait}s{note} (attempt {attempt + 1}/5)…")
                time.sleep(wait)
            else:
                print(f"[step6] ERROR scoring batch: {exc}")
                for item in items:
                    scored_item = item.copy()
                    scored_item["score"]            = 5.0
                    scored_item["matched_keywords"] = []
                    scored.append(scored_item)
                return scored

    return scored


# ---------------------------------------------------------------------------
# Step 6b — Score still images (reuses _score_batch)
# ---------------------------------------------------------------------------

def step6b_score_images(
    image_candidates: list[dict],
    client: genai.Client,
    keywords: list[str],
) -> list[dict]:
    """
    Score still image candidates for carousel / story post suitability.
    Same batch logic as step6 — images scored on mood, tone, and composition
    rather than motion. Rejects anything bright, busy, or stock-generic.
    Returns scored_images.
    """
    BATCH_SIZE = 20
    print("\n[step6b] Scoring image candidates…")

    seen: set[str] = set()
    deduped: list[dict] = []
    for c in image_candidates:
        key = c.get("download_url", "")
        if key and key not in seen:
            seen.add(key)
            deduped.append(c)

    print(f"[step6b] After dedup: {len(deduped)} images to score")
    scored: list[dict] = []
    for i in range(0, len(deduped), BATCH_SIZE):
        batch = deduped[i: i + BATCH_SIZE]
        scored.extend(_score_image_batch(batch, client, keywords))

    print(f"[step6b] Scored {len(scored)} images")
    return scored


def _score_image_batch(items: list[dict], client: genai.Client, run_keywords: list[str]) -> list[dict]:
    """Score one batch of still images — stricter aesthetic lens than video."""
    scored: list[dict] = []
    if not items:
        return scored

    kw_set = set(run_keywords)
    items_text = "\n".join(
        [f"{i+1}. [{item['source']}] {item['title']}" for i, item in enumerate(items)]
    )
    keywords_text = ", ".join(run_keywords) if run_keywords else "none"

    prompt = f"""You are scoring still photos for an Instagram healing channel's carousel and story posts.

Channel essence: Warm, slow, muted, atmospheric. Nervous system relief, inner child healing.
These images will be paired with therapeutic text — they need to feel like a safe container.

Score 1–10. Be strict:
- 9–10: Genuinely breathtaking — soft light, emotional depth, compositionally quiet
- 8–8.9: Strong — muted tones, atmospheric, clearly calming
- 7–7.9: Acceptable — usable but generic; nothing offensive about it
- Below 7: Bright, busy, stock-generic, AI-generated (max 5), overly posed/corporate

Judge on: colour temperature (warm/cool muted > saturated), emotional quietness,
texture (grain/organic > clinical), compositional breathing room, mood resonance.

Portrait orientation preferred — give landscape a 0.5 penalty unless composition is exceptional.

After each score, add " | " then matching keywords. Omit " | " if none apply.
Keywords to match: {keywords_text}

Rate each:
{items_text}

Response format EXACTLY:
1. 8.5 | rainy window, soft focus
2. 7.2
3. 9.0 | VHS grain, golden hour
...
(one line per item)"""

    _OVERLOAD_WAITS = [15, 30, 60, 90]

    for attempt in range(5):
        model = GEMINI_FALLBACK_MODEL if attempt >= 3 else GEMINI_SCORE_MODEL
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            scores_text = response.text.strip()
            parsed: list[tuple[float, list[str]]] = []
            for line in scores_text.split("\n"):
                parts = line.strip().split(". ", 1)
                if len(parts) != 2:
                    continue
                rest = parts[1].strip()
                if " | " in rest:
                    score_part, kw_part = rest.split(" | ", 1)
                    matched = [k.strip() for k in kw_part.split(",") if k.strip() in kw_set]
                else:
                    score_part = rest
                    matched = []
                try:
                    parsed.append((float(score_part.strip()), matched))
                except ValueError:
                    pass
            for i, item in enumerate(items):
                scored_item = item.copy()
                if i < len(parsed):
                    scored_item["score"]            = parsed[i][0]
                    scored_item["matched_keywords"] = parsed[i][1]
                else:
                    scored_item["score"]            = 5.0
                    scored_item["matched_keywords"] = []
                scored.append(scored_item)
            return scored
        except Exception as exc:
            error_str = str(exc)
            is_overload  = "503" in error_str or "UNAVAILABLE" in error_str
            is_limit     = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str
            if is_limit and attempt >= 3:
                print(f"[step6b] Quota exhausted — fallback score 5.0")
                for item in items:
                    scored_item = item.copy(); scored_item["score"] = 5.0; scored_item["matched_keywords"] = []
                    scored.append(scored_item)
                return scored
            if attempt == 4:
                for item in items:
                    scored_item = item.copy(); scored_item["score"] = 5.0; scored_item["matched_keywords"] = []
                    scored.append(scored_item)
                return scored
            wait = _OVERLOAD_WAITS[min(attempt, 3)] if is_overload else 65
            print(f"[step6b] Waiting {wait}s (attempt {attempt + 1}/5)…")
            time.sleep(wait)

    return scored


# ---------------------------------------------------------------------------
# Step 7 — Download (respects Freesound 500/day + Pixabay 24-hr cache)
# ---------------------------------------------------------------------------

def step7_download(
    scored_footage: list[dict],
    scored_audio: list[dict],
    run_dirs: dict[str, Path],
    rejected_ids: set[str] | None = None,
) -> dict:
    """
    Download files that meet the score threshold into the correct subfolder.
    priority/ ← score >= 8.5
    standard/ ← score 7.0–8.4
    Stores licence URL and API response JSON alongside each file (audit trail).
    Returns summary dict of downloaded counts.
    """
    print("\n[step7] Downloading files…")
    summary: dict = {
        "footage_priority": 0,
        "footage_standard": 0,
        "audio_priority": 0,
        "audio_standard": 0,
    }

    import pool_registry as _reg
    _registry  = _reg.load()
    _blocked   = _reg.blocked_set(_registry)

    rejected = rejected_ids or set()

    def _is_rejected(item: dict) -> bool:
        raw_key = f"{item.get('source', '')}_{item.get('id', '')}"
        reg_key = _reg.key(item.get("source", ""), item.get("id", ""))
        return raw_key in rejected or reg_key in _blocked

    # Sort by score descending so priority items download first; skip previously rejected/used clips
    footage_queue = sorted(
        [x for x in scored_footage
         if x.get("score", 0) >= SCORE_STANDARD_THRESHOLD and not _is_rejected(x)],
        key=lambda x: x.get("score", 0), reverse=True
    )[:MAX_FOOTAGE_DOWNLOADS]

    audio_queue = sorted(
        [x for x in scored_audio
         if x.get("score", 0) >= SCORE_STANDARD_THRESHOLD and not _is_rejected(x)],
        key=lambda x: x.get("score", 0), reverse=True
    )[:MAX_AUDIO_DOWNLOADS]

    # Download footage
    for item in tqdm(footage_queue, desc="downloading footage"):
        score = item.get("score", 0)
        dest = _dest_folder(score, "footage", run_dirs)
        try:
            _download_file(item, dest)
            key = "footage_priority" if score >= SCORE_PRIORITY_THRESHOLD else "footage_standard"
            summary[key] += 1
        except Exception as exc:
            print(f"[step7] ERROR downloading footage {item.get('id')}: {exc}")

    # Download audio
    for item in tqdm(audio_queue, desc="downloading audio"):
        score = item.get("score", 0)
        dest = _dest_folder(score, "audio", run_dirs)
        try:
            _download_file(item, dest)
            key = "audio_priority" if score >= SCORE_PRIORITY_THRESHOLD else "audio_standard"
            summary[key] += 1
        except Exception as exc:
            print(f"[step7] ERROR downloading audio {item.get('id')}: {exc}")

    print(
        f"[step7] Downloaded: {summary['footage_priority']} priority footage, "
        f"{summary['footage_standard']} standard footage, "
        f"{summary['audio_priority']} priority audio, "
        f"{summary['audio_standard']} standard audio"
    )
    return summary


_DOWNLOAD_ALLOWED_HOSTS = {
    "pexels.com", "videos.pexels.com", "images.pexels.com",
    "pixabay.com", "cdn.pixabay.com",
    "images.unsplash.com", "plus.unsplash.com",
    "coverr.co", "cdn.coverr.co", "api.coverr.co",
    "freesound.org", "cdn.freesound.org",
    "storage.googleapis.com",   # Jamendo CDN
    "mp3l.jamendo.com", "mp3d.jamendo.com", "prod-1.storage.jamendo.com",
    "freemusicarchive.org",
    "upload.wikimedia.org",
    "tile.loc.gov", "cdn.loc.gov", "ids.si.edu",
}

_DOWNLOAD_MAGIC: list[tuple[bytes, int]] = [
    (b"ftyp",           4),   # mp4 / m4v
    (b"\x1a\x45\xdf\xa3", 0), # webm / mkv
    (b"RIFF",           0),   # wav
    (b"ID3",            0),   # mp3 with ID3 tag
    (b"\xff\xfb",       0),   # mp3 frame sync
    (b"\xff\xf3",       0),   # mp3 frame sync variant
    (b"OggS",           0),   # ogg
]
_DOWNLOAD_MAX_MB = 500


def _is_valid_media_file(path: Path) -> bool:
    try:
        header = path.read_bytes()[:16]
    except Exception:
        return False
    for magic, offset in _DOWNLOAD_MAGIC:
        if header[offset: offset + len(magic)] == magic:
            return True
    return False


def _download_file(item: dict, dest_dir: Path) -> None:
    """
    Download a single file and store metadata alongside it.
    Metadata includes: licence URL, source, score, full API response.
    """
    url = item.get("download_url")
    if not url:
        return

    # HTTPS only
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError(f"Rejected non-HTTPS URL: {url}")

    # Domain allowlist
    host = parsed.hostname or ""
    if not any(host == d or host.endswith("." + d) for d in _DOWNLOAD_ALLOWED_HOSTS):
        raise RuntimeError(f"Rejected download from untrusted host: {host}")

    source  = _re.sub(r"[^\w\-]", "_", str(item.get("source", "unknown")))[:20]
    item_id = _re.sub(r"[^\w\-]", "_", str(item.get("id",     "unknown")))[:60]
    filename = f"{source}_{item_id}"

    _max_bytes = _DOWNLOAD_MAX_MB * 1024 * 1024
    tmp_path: Path | None = None

    # SIGALRM gives a hard kernel-level timeout — interrupts even a blocked socket
    # read before the first chunk arrives, which timeout=(5,15) alone doesn't catch.
    def _alarm(signum, frame):
        raise RuntimeError("download exceeded 60s hard limit (SIGALRM)")
    _prev_handler = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(60)
    try:
        # Split timeout: 5s to connect, 15s silence between chunks
        with requests.get(url, timeout=(5, 15), allow_redirects=True,
                          stream=True) as resp:
            resp.raise_for_status()

            # Validate final URL after redirects — allowlist must still pass
            from urllib.parse import urlparse as _urlparse
            final_host = _urlparse(resp.url).hostname or ""
            if not any(final_host == d or final_host.endswith("." + d) for d in _DOWNLOAD_ALLOWED_HOSTS):
                raise RuntimeError(f"Redirect chain led to untrusted host: {final_host}")

            content_type = resp.headers.get("content-type", "").lower()
            if "text" in content_type or "html" in content_type:
                raise RuntimeError(f"server returned a page instead of media ({content_type})")

            content_len = int(resp.headers.get("Content-Length", 0))
            if content_len > _max_bytes:
                raise RuntimeError(f"file too large ({content_len // 1024 // 1024} MB)")

            # Determine extension
            url_ext = Path(url.split("?")[0]).suffix.lower()
            _KNOWN = {".mp3", ".mp4", ".webm", ".wav", ".ogg", ".m4v", ".m4a"}
            if url_ext in _KNOWN:
                ext = url_ext
            elif "ogg" in content_type:
                ext = ".ogg"
            elif "audio" in content_type or "mp3" in content_type:
                ext = ".mp3"
            elif "video" in content_type or "mp4" in content_type:
                ext = ".mp4"
            elif "webm" in content_type:
                ext = ".webm"
            elif source == "jamendo":
                ext = ".mp3"
            else:
                ext = ""

            # Write to .tmp first — no partial file left behind if process dies mid-download
            tmp_path  = dest_dir / f"{filename}{ext}.tmp"
            file_path = dest_dir / f"{filename}{ext}"
            bytes_written = 0
            with tmp_path.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    bytes_written += len(chunk)
                    if bytes_written > _max_bytes:
                        raise RuntimeError(f"download exceeded {_DOWNLOAD_MAX_MB} MB limit mid-stream")
                    fh.write(chunk)

        # Magic bytes check — reject anything that isn't actual media
        if not _is_valid_media_file(tmp_path):
            raise RuntimeError(f"file failed magic bytes check (not a valid media file): {tmp_path.name}")

        tmp_path.replace(file_path)
        tmp_path = None  # Successfully moved — do not unlink

        # Store metadata: licence URL, score, keyword, matched keywords, tags, page URL
        metadata = {
            "source": source,
            "id": item_id,
            "title": item.get("title"),
            "keyword": item.get("keyword"),
            "matched_keywords": item.get("matched_keywords", []),
            "url": item.get("url"),
            "score": item.get("score"),
            "licence_url": item.get("licence_url"),
            "platform_tags": item.get("platform_tags", []),
            "download_date": RUN_DATE,
        }
        metadata_path = dest_dir / f"{filename}_metadata.json"
        with metadata_path.open("w") as fh:
            json.dump(metadata, fh, indent=2)

    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to download {url}: {exc}")
    finally:
        signal.alarm(0)                                  # cancel any pending alarm
        signal.signal(signal.SIGALRM, _prev_handler)    # restore previous handler
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _dest_folder(score: float, media_type: str, run_dirs: dict[str, Path]) -> Path:
    key = f"{media_type}_priority" if score >= SCORE_PRIORITY_THRESHOLD else f"{media_type}_standard"
    return run_dirs[key]


# ---------------------------------------------------------------------------
# Step 7b — Download images to persistent pool/
# ---------------------------------------------------------------------------

def step7b_download_images(scored_images: list[dict]) -> dict:
    """
    Download scored images into pool/images/priority/ or pool/images/standard/.
    Images accumulate across runs so the pool grows over time.
    Skips images that already exist in the pool (same source+id).
    Returns summary dict.
    """
    print("\n[step7b] Downloading images to pool…")
    summary = {"images_priority": 0, "images_standard": 0, "skipped_existing": 0}

    import pool_registry as _img_reg
    _img_registry = _img_reg.load()
    _img_blocked  = _img_reg.blocked_set(_img_registry)

    queue = sorted(
        [x for x in scored_images if x.get("score", 0) >= SCORE_STANDARD_THRESHOLD],
        key=lambda x: x.get("score", 0), reverse=True
    )[:MAX_IMAGE_DOWNLOADS]

    for item in tqdm(queue, desc="downloading images"):
        source  = _re.sub(r"[^\w\-]", "_", str(item.get("source", "unknown")))[:20]
        item_id = _re.sub(r"[^\w\-]", "_", str(item.get("id", "unknown")))[:60]
        filename_stem = f"{source}_{item_id}"

        # Skip if registry marks this item as rejected or used (survives file deletion)
        if _img_reg.key(item.get("source", ""), item.get("id", "")) in _img_blocked:
            summary["skipped_existing"] += 1
            continue

        score = item.get("score", 0)
        dest = IMAGE_POOL_DIRS["images_priority"] if score >= SCORE_PRIORITY_THRESHOLD else IMAGE_POOL_DIRS["images_standard"]

        # Skip if any file for this source+id already exists in the pool
        existing = list(dest.glob(f"{filename_stem}.*"))
        if existing:
            summary["skipped_existing"] += 1
            continue

        try:
            _download_image_file(item, dest, filename_stem)
            key = "images_priority" if score >= SCORE_PRIORITY_THRESHOLD else "images_standard"
            summary[key] += 1
        except Exception as exc:
            print(f"[step7b] ERROR downloading image {item.get('id')}: {exc}")

    print(
        f"[step7b] Downloaded: {summary['images_priority']} priority, "
        f"{summary['images_standard']} standard, {summary['skipped_existing']} already in pool"
    )
    return summary


def _download_image_file(item: dict, dest_dir: Path, filename_stem: str) -> None:
    """Download a still image and store metadata. Validates magic bytes (JPEG/PNG/WebP)."""
    url = item.get("download_url")
    if not url:
        return

    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError(f"Rejected non-HTTPS URL: {url}")

    host = parsed.hostname or ""
    if not any(host == d or host.endswith("." + d) for d in _DOWNLOAD_ALLOWED_HOSTS):
        raise RuntimeError(f"Rejected download from untrusted host: {host}")

    _max_bytes = _DOWNLOAD_MAX_MB * 1024 * 1024
    tmp_path: Path | None = None

    def _alarm_img(signum, frame):
        raise RuntimeError("image download exceeded 60s hard limit (SIGALRM)")
    _prev_handler_img = signal.signal(signal.SIGALRM, _alarm_img)
    signal.alarm(60)
    try:
        with requests.get(url, timeout=(5, 15), allow_redirects=True,
                          stream=True) as resp:
            resp.raise_for_status()

            # Validate final URL after redirects — prevent redirect chain bypass
            from urllib.parse import urlparse as _urlparse
            final_host = _urlparse(resp.url).hostname or ""
            if not any(final_host == d or final_host.endswith("." + d) for d in _DOWNLOAD_ALLOWED_HOSTS):
                raise RuntimeError(f"Redirect chain led to untrusted host: {final_host}")

            content_type = resp.headers.get("content-type", "").lower()
            if "text" in content_type or "html" in content_type:
                raise RuntimeError(f"Server returned a page instead of image ({content_type})")

            content_len = int(resp.headers.get("Content-Length", 0))
            if content_len > _max_bytes:
                raise RuntimeError(f"Image too large ({content_len // 1024 // 1024} MB)")

            if "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            elif "gif" in content_type:
                ext = ".gif"
            else:
                ext = ".jpg"

            tmp_path  = dest_dir / f"{filename_stem}{ext}.tmp"
            file_path = dest_dir / f"{filename_stem}{ext}"
            bytes_written = 0
            with tmp_path.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    bytes_written += len(chunk)
                    if bytes_written > _max_bytes:
                        raise RuntimeError(f"Image download exceeded {_DOWNLOAD_MAX_MB} MB limit mid-stream")
                    fh.write(chunk)

        if not _is_valid_image_file(tmp_path):
            raise RuntimeError(f"Failed magic bytes check: {tmp_path.name}")

        tmp_path.replace(file_path)
        tmp_path = None  # Successfully moved — do not unlink

    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, _prev_handler_img)

    metadata = {
        "source": item.get("source"),
        "id": str(item.get("id")),
        "title": item.get("title"),
        "keyword": item.get("keyword"),
        "matched_keywords": item.get("matched_keywords", []),
        "url": item.get("url"),
        "score": item.get("score"),
        "licence_url": item.get("licence_url"),
        "platform_tags": item.get("platform_tags", []),
        "download_date": RUN_DATE,
    }
    metadata_path = dest_dir / f"{filename_stem}_metadata.json"
    with metadata_path.open("w") as fh:
        json.dump(metadata, fh, indent=2)


# ---------------------------------------------------------------------------
# Step 8 — Intelligence summary
# ---------------------------------------------------------------------------

def step8_summary(
    scored_footage: list[dict],
    scored_audio: list[dict],
    keywords_data: dict,
    run_dirs: dict[str, Path],
    content_data: dict | None = None,
    scored_images: list[dict] | None = None,
) -> Path:
    """
    Write keywords_this_run.json, emotional_hooks.txt, scoring_log.json,
    carousel_scripts.json, quotes.json, and summary.txt to intelligence/.
    Returns path to summary.txt.
    """
    print("\n[step8] Writing intelligence summary…")
    intel_dir = run_dirs["intelligence"]

    with (intel_dir / "keywords_this_run.json").open("w") as fh:
        json.dump({
            "footage": keywords_data.get("keywords", []),
            "audio":   keywords_data.get("audio_keywords", []),
        }, fh, indent=2)

    hooks = "\n".join(keywords_data.get("emotional_hooks", []))
    (intel_dir / "emotional_hooks.txt").write_text(hooks)

    images = scored_images or []
    scoring_log = {"footage": scored_footage, "audio": scored_audio, "images": images}
    with (intel_dir / "scoring_log.json").open("w") as fh:
        json.dump(scoring_log, fh, indent=2)

    # Carousel scripts, power quotes, and Gemini-written captions
    content        = content_data or {}
    carousels      = content.get("carousels", [])
    quotes         = content.get("quotes", [])
    quote_hashtags = content.get("quote_hashtags", [])

    if carousels:
        with (intel_dir / "carousel_scripts.json").open("w") as fh:
            json.dump(carousels, fh, indent=2, ensure_ascii=False)

    if quotes:
        (intel_dir / "quotes.txt").write_text("\n".join(quotes), encoding="utf-8")
        with (intel_dir / "quotes.json").open("w") as fh:
            json.dump(quotes, fh, indent=2, ensure_ascii=False)

    if quote_hashtags:
        with (intel_dir / "quote_hashtags.json").open("w") as fh:
            json.dump(quote_hashtags, fh, indent=2, ensure_ascii=False)

    top_footage = sorted(scored_footage, key=lambda x: x.get("score", 0), reverse=True)[:3]
    top_audio   = sorted(scored_audio,   key=lambda x: x.get("score", 0), reverse=True)[:3]
    top_images  = sorted(images,         key=lambda x: x.get("score", 0), reverse=True)[:3]

    footage_priority = sum(1 for x in scored_footage if x.get("score", 0) >= SCORE_PRIORITY_THRESHOLD)
    footage_standard = sum(1 for x in scored_footage if SCORE_STANDARD_THRESHOLD <= x.get("score", 0) < SCORE_PRIORITY_THRESHOLD)
    audio_priority   = sum(1 for x in scored_audio   if x.get("score", 0) >= SCORE_PRIORITY_THRESHOLD)
    audio_standard   = sum(1 for x in scored_audio   if SCORE_STANDARD_THRESHOLD <= x.get("score", 0) < SCORE_PRIORITY_THRESHOLD)
    image_priority   = sum(1 for x in images         if x.get("score", 0) >= SCORE_PRIORITY_THRESHOLD)
    image_standard   = sum(1 for x in images         if SCORE_STANDARD_THRESHOLD <= x.get("score", 0) < SCORE_PRIORITY_THRESHOLD)

    carousel_lines: list[str] = []
    for i, c in enumerate(carousels):
        theme_snip = c.get('theme', '')[:55]
        carousel_lines.append(f'  Carousel {i+1}: {len(c.get("slides", []))} slides — "{theme_snip}"')
        for j, slide in enumerate(c.get("slides", [])[:2]):
            carousel_lines.append(f"    slide {j+1}: {slide[:60]}")

    lines = [
        f"Run date: {RUN_DATE}",
        f"",
        f"FOOTAGE",
        f"  Total candidates : {len(scored_footage)}",
        f"  Priority (≥8.5)  : {footage_priority}",
        f"  Standard (7–8.4) : {footage_standard}",
        f"  Top 3:",
        *[f"    {i+1}. [{x['source']}] {x['title']} — {x.get('score', '?')}" for i, x in enumerate(top_footage)],
        f"",
        f"AUDIO",
        f"  Total candidates : {len(scored_audio)}",
        f"  Priority (≥8.5)  : {audio_priority}",
        f"  Standard (7–8.4) : {audio_standard}",
        f"  Top 3:",
        *[f"    {i+1}. [{x['source']}] {x['title']} — {x.get('score', '?')}" for i, x in enumerate(top_audio)],
        f"",
        f"IMAGES (pool)",
        f"  Total candidates : {len(images)}",
        f"  Priority (≥8.5)  : {image_priority}",
        f"  Standard (7–8.4) : {image_standard}",
        f"  Top 3:",
        *[f"    {i+1}. [{x['source']}] {x['title']} — {x.get('score', '?')}" for i, x in enumerate(top_images)],
        f"",
        f"CAROUSEL SCRIPTS ({len(carousels)} generated)",
        *carousel_lines,
        f"",
        f"POWER QUOTES ({len(quotes)} generated)",
        *[f"  {q}" for q in quotes],
        f"",
        f"KEYWORDS USED",
        *[f"  {k}" for k in keywords_data.get("keywords", [])],
        f"",
        f"EMOTIONAL HOOKS",
        *[f"  {h}" for h in keywords_data.get("emotional_hooks", [])],
        f"",
    ]
    summary_path = intel_dir / "summary.txt"
    summary_path.write_text("\n".join(lines))

    print(f"[step8] Intelligence written to {intel_dir}")
    return summary_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Healing Agent scraper")
    parser.add_argument(
        "--quick", action="store_true",
        help="Generate quotes + carousel scripts only (no media scraping/downloading) — ~2 min"
    )
    args = parser.parse_args()

    validate_env()

    keys = _load_keys()
    global CLIENT
    CLIENT = genai.Client(
        vertexai=True,
        project=keys["GOOGLE_CLOUD_PROJECT"],
        location="us-central1",
    )

    _make_run_dirs()
    world_bible = _init_world_bible()

    if args.quick:
        print(f"=== Healing Agent — quick content run {RUN_DATE} ===")
        raw_climate   = step1_climate()
        keywords_data = step3_keywords(raw_climate, world_bible, CLIENT)
        new_hooks = keywords_data.get("emotional_hooks", [])
        if new_hooks:
            history = set(world_bible.get("emotional_hooks_history", []))
            history.update(new_hooks)
            world_bible["emotional_hooks_history"] = sorted(history)
            _save_world_bible(world_bible)
        content_data = step3b_content(keywords_data, raw_climate, CLIENT)
        intel_dir = RUN_DIRS["intelligence"]
        intel_dir.mkdir(parents=True, exist_ok=True)
        quotes    = content_data.get("quotes", [])
        carousels = content_data.get("carousels", [])
        if quotes:
            (intel_dir / "quotes.txt").write_text("\n".join(quotes), encoding="utf-8")
            with (intel_dir / "quotes.json").open("w") as fh:
                json.dump(quotes, fh, indent=2, ensure_ascii=False)
            print(f"\n[quick] {len(quotes)} quotes → {intel_dir}/quotes.json")
        if carousels:
            with (intel_dir / "carousel_scripts.json").open("w") as fh:
                json.dump(carousels, fh, indent=2, ensure_ascii=False)
            print(f"[quick] {len(carousels)} carousel scripts → {intel_dir}/carousel_scripts.json")
        quote_hashtags = content_data.get("quote_hashtags", [])
        if quote_hashtags:
            with (intel_dir / "quote_hashtags.json").open("w") as fh:
                json.dump(quote_hashtags, fh, indent=2, ensure_ascii=False)
            print(f"[quick] {len(quote_hashtags)} quote hashtag lists → {intel_dir}/quote_hashtags.json")
        print(f"\n=== Quick run complete. Run editor_story.py or editor_carousel.py ===")
        return

    print(f"=== Healing Agent — run {RUN_DATE} ===")

    _init_pool_dirs()

    raw_climate   = step1_climate()
    keywords_data = step3_keywords(raw_climate, world_bible, CLIENT)

    # Persist hooks so step3 avoids repeating them across runs
    new_hooks = keywords_data.get("emotional_hooks", [])
    if new_hooks:
        history = set(world_bible.get("emotional_hooks_history", []))
        history.update(new_hooks)
        world_bible["emotional_hooks_history"] = sorted(history)
        _save_world_bible(world_bible)

    # Generate carousel scripts and power quotes from this run's emotional climate
    content_data = step3b_content(keywords_data, raw_climate, CLIENT)

    footage_candidates = step4_footage(keywords_data["keywords"], keys)
    image_candidates   = step4b_images(keywords_data["keywords"], keys)
    audio_candidates   = step5_audio(keywords_data["audio_keywords"], keys)

    scored_footage, scored_audio = step6_score(
        footage_candidates, audio_candidates, CLIENT,
        keywords_data["keywords"], keywords_data["audio_keywords"],
    )
    scored_images = step6b_score_images(image_candidates, CLIENT, keywords_data["keywords"])

    rejected_ids = set(world_bible.get("rejected_ids", []))
    step7_download(scored_footage, scored_audio, RUN_DIRS, rejected_ids)
    step7b_download_images(scored_images)
    step8_summary(
        scored_footage, scored_audio, keywords_data, RUN_DIRS,
        content_data=content_data, scored_images=scored_images,
    )

    print(f"\n=== Run complete. Batch at {BATCH_ROOT} ===")


if __name__ == "__main__":
    main()
