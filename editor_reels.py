"""
editor_reels.py — video assembler for the healing-agent pipeline.

Footage and audio come from the global pool (pool/footage/, pool/audio/).
Context (hooks, emotional direction) comes from the most recent agent.py run.
Output goes to renders/YYYY-MM-DD/ — independent of any batch.

For carousel posts : python editor_carousel.py
For story posts    : python editor_story.py
Shared utilities   : editor_shared.py

Usage:
    python editor_reels.py                      # assemble a new reel
    python editor_reels.py --feedback           # log notes on most recent render → world_bible
    python editor_reels.py --feedback --render 2026-05-05
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from google import genai
from dotenv import load_dotenv
from editor_shared import load_pool_images, load_content_data

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR         = Path(__file__).parent
DATA_DIR         = BASE_DIR / "data"
BATCH_ROOT       = DATA_DIR / "scrape"
POOL_DIR         = DATA_DIR / "sort_media"
RENDERS_DIR      = DATA_DIR / "pending"
VOICEOVER_HISTORY_PATH = RENDERS_DIR / "voiceover_history.txt"
WORLD_BIBLE_PATH = DATA_DIR / "world_bible.json"
SCIENCE_REF_PATH = BASE_DIR / "md" / "science_reference.md"

GEMINI_EDIT_MODEL   = "gemini-2.5-pro"    # editorial planning — most capable reasoning model
GEMINI_PROOF_MODEL  = "gemini-2.5-flash"  # voiceover proofreading — lightweight single-purpose pass

VIDEO_W   = 1080
VIDEO_H   = 1920
VIDEO_FPS = 24
TARGET_LUFS = -14
MUSIC_TAIL_S = 6.0
MUSIC_FADE_OUT_S = 2.0

REQUIRED_KEYS = ["GOOGLE_CLOUD_PROJECT"]

MEDIA_EXTENSIONS = {".mp4", ".mp3", ".webm", ".wav", ".ogg", ".m4v", ".m4a"}

# ---------------------------------------------------------------------------
# Bootstrap — same pattern as agent.py
# ---------------------------------------------------------------------------

def get_secret(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise EnvironmentError(f"Required secret '{key}' is missing from environment")
    return value


def validate_env() -> None:
    missing = [k for k in REQUIRED_KEYS if not os.environ.get(k)]
    if missing:
        for k in missing:
            print(f"[env] Missing required secret: {k}")
        sys.exit(1)


def _load_keys() -> dict:
    return {k: get_secret(k) for k in REQUIRED_KEYS}


def _init_gemini(keys: dict) -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=keys["GOOGLE_CLOUD_PROJECT"],
        location="us-central1",
    )

# ---------------------------------------------------------------------------
# Batch navigation
# ---------------------------------------------------------------------------

def _find_latest_batch() -> Path:
    """Return data/scrape/ — the flat staging area written by agent.py."""
    if not BATCH_ROOT.exists() or not (BATCH_ROOT / "intelligence").exists():
        print("[editor] No content in data/scrape/ — run agent.py first.")
        sys.exit(1)
    return BATCH_ROOT


def _make_render_path(content_type: str = "reel") -> Path:
    """Return the next available numbered render path — folder NOT created yet.
    Creation is deferred to first write so empty folders are never left behind."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n = 1
    while True:
        render_path = RENDERS_DIR / f"{today}_{n:03d}_{content_type}"
        if not render_path.exists():
            return render_path
        n += 1


def _find_render(date_str: str | None) -> Path:
    """Find a render folder for --feedback mode."""
    if date_str:
        path = RENDERS_DIR / date_str
        if not path.exists():
            print(f"[editor] Render not found: {path}")
            sys.exit(1)
        return path
    renders = sorted(p for p in RENDERS_DIR.iterdir() if p.is_dir()) if RENDERS_DIR.exists() else []
    if not renders:
        print("[editor] No renders found — run without --feedback first.")
        sys.exit(1)
    return renders[-1]

# ---------------------------------------------------------------------------
# Load approved files from footage/good and audio/good folders
# ---------------------------------------------------------------------------

def _get_video_duration(path: str) -> float | None:
    """Use ffprobe to get clip duration without loading the full file."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    dur = stream.get("duration")
                    if dur:
                        return float(dur)
    except Exception:
        pass
    return None


def load_good_files() -> tuple[list[dict], list[dict]]:
    """
    Read footage/good and audio/good folders (written by feedback.py) and return
    (good_footage, good_audio). Each item includes metadata fields + _path, _folder, _duration_s.
    The folder is the source of truth — if a file is here, it was approved.
    """
    footage: list[dict] = []
    audio:   list[dict] = []

    for subdir, is_audio in [("footage", False), ("audio", True)]:
        dir_path = POOL_DIR / subdir
        if not dir_path.exists():
            continue

        for meta_file in sorted(dir_path.glob("*_metadata.json")):
            base = meta_file.stem.replace("_metadata", "")
            media_file = next(
                (f for f in dir_path.iterdir()
                 if f.stem == base and f.suffix.lower() in MEDIA_EXTENSIONS),
                None,
            )
            if not media_file:
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                continue

            meta["_path"]   = str(media_file)
            meta["_folder"] = f"data/sort_media/{subdir}"
            if not is_audio:
                meta["_duration_s"] = _get_video_duration(str(media_file))

            (audio if is_audio else footage).append(meta)

    if not footage:
        print("[editor] data/sort_media/footage/ is empty — run feedback.py and rate at least one footage clip 'g' first.")
        sys.exit(1)

    if not audio:
        print("[editor] No audio in pool — video will be voiceover only. Add music via Instagram or rate audio 'g' in feedback.py.")

    print(f"[editor] Pool loaded — {len(footage)} footage clips, {len(audio)} audio tracks")
    return footage, audio

# ---------------------------------------------------------------------------
# Context: world_bible + intelligence from this batch
# ---------------------------------------------------------------------------

def load_context(batch_path: Path) -> dict:
    bible = {}
    if WORLD_BIBLE_PATH.exists():
        bible = json.loads(WORLD_BIBLE_PATH.read_text())

    hooks = []
    hooks_file = batch_path / "intelligence" / "emotional_hooks.txt"
    if hooks_file.exists():
        hooks = [h.strip() for h in hooks_file.read_text().splitlines() if h.strip()]

    summary = ""
    summary_file = batch_path / "intelligence" / "summary.txt"
    if summary_file.exists():
        summary = summary_file.read_text()

    # science_reference.md — created manually by you, lives in md/
    # Sonnet uses it to ensure every caption claim is grounded to the right tier
    science_ref = ""
    if SCIENCE_REF_PATH.exists():
        science_ref = SCIENCE_REF_PATH.read_text()

    # Universal theme rotation — pick current theme, advance index for next run
    themes = bible.get("universal_themes", [])
    current_theme = ""
    if themes:
        idx = int(bible.get("universal_theme_index", 0))
        current_theme = themes[idx % len(themes)]
        bible["universal_theme_index"] = (idx + 1) % len(themes)
        save_world_bible(bible)

    # Last 10 used voiceover texts so Gemini avoids repeating them
    recent_voiceovers: list[str] = []
    if VOICEOVER_HISTORY_PATH.exists():
        lines = VOICEOVER_HISTORY_PATH.read_text().splitlines()
        recent_voiceovers = [
            l.split("] ", 1)[1] for l in lines[-10:] if "] " in l
        ]

    return {
        "world_bible":       bible,
        "emotional_hooks":   hooks,
        "summary":           summary,
        "science_ref":       science_ref,
        "current_theme":     current_theme,
        "recent_voiceovers": recent_voiceovers,
    }

# ---------------------------------------------------------------------------
# Token logging — printed + appended to renders/token_log.jsonl
# ---------------------------------------------------------------------------

# Gemini 2.5 Pro pricing per 1M tokens (≤200K window)
_PRICE_IN  = 1.25 / 1_000_000
_PRICE_OUT = 10.0 / 1_000_000

def _log_tokens(response, call_name: str) -> None:
    """Print token usage and estimated cost for a Gemini call."""
    try:
        meta    = response.usage_metadata
        t_in    = getattr(meta, "prompt_token_count", 0) or 0
        t_out   = getattr(meta, "candidates_token_count", 0) or 0
        t_think = getattr(meta, "thoughts_token_count", 0) or 0
        cost    = t_in * _PRICE_IN + (t_out + t_think) * _PRICE_OUT
        print(f"[tokens] {call_name}: {t_in} in / {t_out} out"
              + (f" / {t_think} thinking" if t_think else "")
              + f"  ≈ ${cost:.4f}")
        # Append to token_log.jsonl in RENDERS_DIR so cost accumulates across runs
        log_entry = {
            "ts":       datetime.now(timezone.utc).isoformat(),
            "call":     call_name,
            "model":    GEMINI_EDIT_MODEL,
            "in":       t_in,
            "out":      t_out,
            "thinking": t_think,
            "cost_usd": round(cost, 6),
        }
        RENDERS_DIR.mkdir(parents=True, exist_ok=True)
        with open(RENDERS_DIR / "token_log.jsonl", "a") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception:
        pass  # never let logging crash the pipeline


# ---------------------------------------------------------------------------
# Plan edit — Gemini 2.5 Pro decides everything
# ---------------------------------------------------------------------------

def plan_edit(
    footage: list[dict],
    audio:   list[dict],
    context: dict,
    client:  genai.Client,
) -> dict:
    """
    Send good files + world_bible + hooks to Gemini 2.5 Pro.
    Returns a structured edit plan as a dict (parsed from JSON).
    """
    bible        = context["world_bible"]
    editor_prefs = bible.get("editor", {})
    voice_speed  = float(bible.get("voice", {}).get("speed", 1.0))
    target_words_min = max(20, round(35 * voice_speed / 1.4))
    target_words_max = max(target_words_min + 4, round(45 * voice_speed / 1.4))
    science_ref  = context["science_ref"]

    science_block = (
        f"\nSCIENCE REFERENCE (grounding tiers — respect these):\n{science_ref}\n"
        if science_ref else ""
    )
    editor_block = (
        f"\nEDITOR PREFERENCES (learned from past runs):\n{json.dumps(editor_prefs, indent=2)}\n"
        if editor_prefs else "\nEDITOR PREFERENCES: (first run — use defaults)\n"
    )

    footage_list = json.dumps(
        [
            {k: v for k, v in f.items() if k in _PROMPT_FIELDS}
            | {"duration_s": f.get("_duration_s"), "file": Path(f["_path"]).name}
            for f in footage
        ],
        indent=2,
    )
    hooks_block   = "\n".join(f"- {h}" for h in context["emotional_hooks"]) or "(none)"
    theme_block   = (
        f"\nHUMAN MOMENT THIS RUN:\n"
        f"Theme: \"{context['current_theme']}\"\n"
        f"Collective signal: {context['summary'][:300] if context['summary'] else '(none)'}\n\n"
        f"Use the theme as your entry point — speak to someone quietly living this feeling.\n"
        f"Bring them to the channel's healing essence. Do not explain the theme, address the feeling beneath it.\n"
        if context.get("current_theme") else ""
    )
    avoid_block   = (
        "\nRECENT VOICEOVER TEXTS (do not repeat these themes or phrasings):\n"
        + "\n".join(f'- "{v}"' for v in context["recent_voiceovers"]) + "\n"
        if context.get("recent_voiceovers") else ""
    )
    def _audio_entry(a: dict) -> dict:
        return (
            {k: v for k, v in a.items() if k in _PROMPT_FIELDS}
            | {"file": Path(a["_path"]).name}
        )

    sounds = [a for a in audio if _classify_audio(a) == "sound"]
    music  = [a for a in audio if _classify_audio(a) == "music"]
    # Shuffle so Gemini sees different orderings each run — prevents always picking
    # the same track due to position bias when scores are no longer shown.
    random.shuffle(sounds)
    random.shuffle(music)

    audio_section = ""
    if sounds:
        audio_section += f"AVAILABLE AMBIENT SOUNDS ({len(sounds)} tracks — baked into BOTH draft versions):\n"
        audio_section += json.dumps([_audio_entry(a) for a in sounds], indent=2) + "\n"
    else:
        audio_section += "AVAILABLE AMBIENT SOUNDS: none\n"
    if music:
        audio_section += f"\nAVAILABLE MUSIC ({len(music)} tracks — only in draft_music version):\n"
        audio_section += json.dumps([_audio_entry(a) for a in music], indent=2)
    else:
        audio_section += "\nAVAILABLE MUSIC: none"

    audio_schema = (
        '"sound": {{"file": "exact filename from sounds list above", "reasoning": "one sentence"}},'
        '\n  "music": {{"file": "exact filename from music list above", "reasoning": "one sentence"}} or null,'
    )

    prompt = f"""You are the editor for a healing Instagram channel. Plan a 12–18 second vertical (9:16) video.

CHANNEL ESSENCE:
- Warm, slow, muted, atmospheric
- Nervous system relief, inner child healing
- Lo-fi / VHS grain / Ghibli-adjacent aesthetic
- 90s–early 2000s nostalgia register
- Never: bright, fast, energetic, hustle, toxic positivity
{science_block}{editor_block}
AVAILABLE FOOTAGE ({len(footage)} good clips):
{footage_list}

{audio_section}

EMOTIONAL HOOKS GENERATED THIS RUN:
{hooks_block}
{theme_block}{avoid_block}
Return ONLY valid JSON — no markdown fences, no explanation outside the JSON:
{{
  "voiceover_script": "therapeutic voiceover text, {target_words_min}–{target_words_max} words — current Kokoro narration speed is {voice_speed:.2f}x, so keep this tight enough for ~15 seconds. One clear emotional idea. Grounded in hooks and science tiers. No punctuation at all — no commas, periods, ellipses or question marks. Keep capital letters for the first word of each sentence only.",
  "clips": [
    {{
      "file": "exact filename from the list above",
      "position": 1,
      "reasoning": "one sentence — why this clip"
    }}
  ],
  {audio_schema}
  "hashtags": ["healing", "nervoussystem"],
  "music_suggestions": [
    {{"name": "Track Name — Artist", "reason": "one sentence"}}
  ],
  "overall_reasoning": "one paragraph on the emotional arc"
}}

Pick exactly ONE clip. Do not add more. The clip will be looped to match the voiceover length.
Pick ONE ambient sound that fits the footage and emotional tone — or set sound to null if none fit.
Pick ONE music track for the music variant — or set music to null if none fit or if music would distract.
Use ONLY exact filenames from the lists provided. Do not invent or modify filenames."""

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=GEMINI_EDIT_MODEL, contents=prompt)
            _log_tokens(response, "plan_edit")
            raw = response.text.strip()
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1] if len(parts) > 1 else raw
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip().rstrip("`").strip()
            plan = json.loads(raw)
            print(f"[editor] Edit plan received — {len(plan.get('clips', []))} clips selected")
            return plan

        except json.JSONDecodeError as exc:
            print(f"[editor] JSON parse error (attempt {attempt + 1}/3): {exc}")
            if attempt == 2:
                print("[editor] Could not parse Gemini's edit plan. Exiting.")
                sys.exit(1)

        except Exception as exc:
            error_str = str(exc)
            is_exhausted = "RESOURCE_EXHAUSTED" in error_str or ("429" in error_str and "PerDay" in error_str)
            is_busy      = "503" in error_str or "UNAVAILABLE" in error_str or ("429" in error_str and "PerMinute" in error_str)
            if is_exhausted:
                print("[editor] Gemini quota exhausted.")
                sys.exit(1)
            if is_busy and attempt < 2:
                print(f"[editor] Gemini busy — waiting 20s (attempt {attempt + 1}/3)…")
                import time; time.sleep(20)
                continue
            print(f"[editor] Gemini error (attempt {attempt + 1}/3): {exc}")
            if attempt == 2:
                sys.exit(1)

    return {}  # unreachable but satisfies type checker


def save_edit_log(plan: dict, render_path: Path) -> Path:
    """Write the full edit plan to edit_log.json BEFORE any rendering starts."""
    log = {
        "run_date":          datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "overall_reasoning": plan.get("overall_reasoning", ""),
        "clips":             plan.get("clips", []),
        "audio":             plan.get("audio", {}),
        "pacing":            plan.get("pacing", {}),
    }
    render_path.mkdir(parents=True, exist_ok=True)
    log_path = render_path / "edit_log.json"
    log_path.write_text(json.dumps(log, indent=2))
    print(f"[editor] Edit log saved → {log_path.name}")
    return log_path

# ---------------------------------------------------------------------------
# Voiceover: proofread, save script file, then call tts.py
# ---------------------------------------------------------------------------

def _process_voiceover(text: str, client: genai.Client) -> tuple[str, str]:
    """
    One Gemini Flash pass that does two jobs at once:
    - Fixes broken sentence structure / nonsensical words (clean version, no punctuation)
    - Produces a punctuated version for caption.txt (same text, commas/periods added back)
    Returns (clean_text, captioned_text). Falls back to (original, original) on any error.
    """
    prompt = f"""You are a copy editor for a short therapeutic voiceover script.

Your job: return a JSON object with two fields.

1. "clean" — the corrected voiceover text:
   - No punctuation of any kind (no commas, periods, ellipses, question marks, exclamation marks)
   - Capital letter only on the first word of each sentence
   - Fix: grammatically broken sentences, misplaced words, words that do not make sense in context, accidental duplicates
   - Do NOT change meaning, tone, or emotional content. Do NOT add new words or sentences.

2. "caption" — the same corrected text WITH natural punctuation added back:
   - Add commas, periods, and sentence capitalisation as you normally would
   - This is for display as an Instagram caption — it should read naturally

SCRIPT TO CHECK:
{text}

Return ONLY valid JSON, no markdown fences:
{{"clean": "...", "caption": "..."}}"""

    try:
        response = client.models.generate_content(model=GEMINI_PROOF_MODEL, contents=prompt)
        raw = response.text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        data = json.loads(raw)
        clean   = data.get("clean", "").strip() or text
        caption = data.get("caption", "").strip() or clean
        return clean, caption
    except Exception as exc:
        print(f"[editor] Voiceover process skipped ({exc})")
    return text, text


def save_script(plan: dict, render_path: Path) -> Path:
    """Save voiceover text to script.txt — the voiceover file will match this name."""
    script_path = render_path / "script.txt"
    script_path.write_text(plan["voiceover_script"])
    return script_path


def generate_voice(script_text: str, script_path: Path, render_path: Path) -> Path:
    """
    Generate voiceover WAV via tts.py.
    Output is saved as voiceovers/<script_stem>.wav — matching the script filename.
    """
    from tts import generate_voiceover

    voice_path = render_path / "voiceovers" / f"{script_path.stem}.wav"
    print("[editor] Generating voiceover with Kokoro...")
    generate_voiceover(script_text, voice_path)
    print(f"[editor] Voiceover saved → voiceovers/{voice_path.name}")
    return voice_path

# ---------------------------------------------------------------------------
# Caption alignment — stable-ts forced alignment on the generated audio
# ---------------------------------------------------------------------------

def align_captions(voiceover_path: Path, script_text: str) -> list[tuple[str, float, float]]:
    """
    Force-align script_text to the voiceover audio using stable-ts.
    stable-ts maps the known text onto the audio — no re-transcription needed.
    Returns list of (word, start_s, end_s).
    """
    try:
        import stable_whisper
    except ImportError:
        print("[editor] stable-ts is not installed.")
        print("[editor] Run: pip install stable-ts")
        print("[editor] (Downloads ~140 MB Whisper base model on first use)")
        sys.exit(1)

    print("[captions] Aligning words to voiceover audio...")
    print("[captions] First run: stable-ts downloads a Whisper base model (~140 MB)")

    model  = stable_whisper.load_model("base")
    result = model.align(str(voiceover_path), script_text, language="en")

    words = []
    for segment in result.segments:
        for word in segment.words:
            w = word.word.strip()
            if w:
                words.append((w, float(word.start), float(word.end)))

    print(f"[captions] {len(words)} words aligned")
    return words

# ---------------------------------------------------------------------------
# Caption layout — word-level drawtext filters, no subtitle dependency
# ---------------------------------------------------------------------------

# Absolute font path — bypasses font lookup entirely, confirmed present on this system
_CAPTION_FONT = "/usr/share/fonts/opentype/linux-libertine/LinLibertine_RB.otf"
_CAPTION_SIZE = 82
_CAPTION_MIN_SIZE = 42
_CAPTION_MIN_PLANNED_SIZE = 76
_CAPTION_MARGIN = 80   # px each side -- equal left/right breathing room
_CAPTION_BORDER_W = 3
_CAPTION_WIDTH_SAFETY = 12
_CAPTION_MAX_WORDS_PER_PHRASE = 9
_CAPTION_MIN_FILL_RATIO = 0.55
_CAPTION_TARGET_FILL_RATIO = 0.88
_CAPTION_IDEAL_MIN_DUR = 1.25
_CAPTION_IDEAL_MAX_DUR = 3.35
_CAPTION_LEAD_IN_S = 0.08  # 80ms: roughly 1-2 frames early at 24fps
_FADE_IN_DUR   = 0.18  # seconds — soft dissolve-in per word

_CAPTION_BAD_END_WORDS = {
    "a", "an", "and", "as", "at", "but", "for", "from", "if", "in", "into",
    "of", "on", "or", "so", "that", "the", "to", "when", "while", "with",
    "your", "you",
}
_CAPTION_BAD_START_WORDS = {
    "a", "an", "as", "at", "for", "from", "in", "into", "of", "on", "or",
    "that", "the", "to", "while", "with",
}


@lru_cache(maxsize=32)
def _caption_font(size: int = _CAPTION_SIZE):
    from PIL import ImageFont
    return ImageFont.truetype(_CAPTION_FONT, size)


def _caption_usable_width() -> int:
    return VIDEO_W - 2 * (_CAPTION_MARGIN + _CAPTION_BORDER_W + _CAPTION_WIDTH_SAFETY)


@lru_cache(maxsize=4096)
def _measure_caption_text(text: str, size: int = _CAPTION_SIZE) -> tuple[int, int]:
    """
    Return (visual_width, left_bearing) for the rendered text.

    getlength() measures advance, not the actual visible box. Centering on the
    visual bbox makes the finished phrase have identical left/right margins.
    """
    try:
        font = _caption_font(size)
        left, _, right, _ = font.getbbox(text)
        return max(0, int(math.ceil(right - left))), int(math.floor(left))
    except Exception:
        return int(math.ceil(len(text) * size * 0.36)), 0


@lru_cache(maxsize=4096)
def _caption_advance(text: str, size: int = _CAPTION_SIZE) -> float:
    try:
        return float(_caption_font(size).getlength(text))
    except Exception:
        return float(len(text) * size * 0.36)


def _caption_phrase_text(word_list: list[tuple[str, float, float]]) -> str:
    return " ".join(w for w, _, _ in word_list)


def _caption_text_fits(word_list: list[tuple[str, float, float]], size: int = _CAPTION_SIZE) -> bool:
    visual_w, _ = _measure_caption_text(_caption_phrase_text(word_list), size)
    return visual_w <= _caption_usable_width()


def _caption_estimated_width(
    word_list: list[tuple[str, float, float]],
    size: int = _CAPTION_SIZE,
) -> float:
    if not word_list:
        return 0.0
    base_w = sum(_caption_advance(w, _CAPTION_SIZE) for w, _, _ in word_list)
    base_w += _caption_advance(" ", _CAPTION_SIZE) * (len(word_list) - 1)
    return base_w * (size / _CAPTION_SIZE)


def _caption_estimated_fitted_size(word_list: list[tuple[str, float, float]]) -> int:
    base_w = _caption_estimated_width(word_list, _CAPTION_SIZE)
    usable_w = _caption_usable_width()
    if base_w <= usable_w:
        return _CAPTION_SIZE
    size = int((_CAPTION_SIZE * usable_w) / max(1.0, base_w))
    size = max(_CAPTION_MIN_SIZE, min(_CAPTION_SIZE, size))
    if size % 2:
        size -= 1
    return max(_CAPTION_MIN_SIZE, size)


def _caption_estimated_fits_at_planned_size(word_list: list[tuple[str, float, float]]) -> bool:
    return _caption_estimated_width(word_list, _CAPTION_MIN_PLANNED_SIZE) <= _caption_usable_width()


def _caption_estimated_fill_ratio(
    word_list: list[tuple[str, float, float]],
    font_size: int | None = None,
) -> float:
    if font_size is None:
        font_size = _caption_estimated_fitted_size(word_list)
    return _caption_estimated_width(word_list, font_size) / max(1, _caption_usable_width())


@lru_cache(maxsize=4096)
def _fit_caption_text_size(text: str) -> int:
    base_w, _ = _measure_caption_text(text, _CAPTION_SIZE)
    usable_w = _caption_usable_width()
    if base_w <= usable_w:
        return _CAPTION_SIZE

    size = int((_CAPTION_SIZE * usable_w) / max(1, base_w))
    size = max(_CAPTION_MIN_SIZE, min(_CAPTION_SIZE, size))
    if size % 2:
        size -= 1
    size = max(_CAPTION_MIN_SIZE, size)

    while size > _CAPTION_MIN_SIZE and _measure_caption_text(text, size)[0] > usable_w:
        size -= 2
    while size + 2 <= _CAPTION_SIZE and _measure_caption_text(text, size + 2)[0] <= usable_w:
        size += 2
    return size


def _fit_caption_size(word_list: list[tuple[str, float, float]]) -> int:
    return _fit_caption_text_size(_caption_phrase_text(word_list))


def _phrase_layout(
    word_list: list[tuple[str, float, float]],
    font_size: int | None = None,
) -> tuple[list[float], str, int]:
    """
    Return (x_positions, y_expr, font_size) for independent drawtext filters.

    x_positions: pixel x for each word so the complete phrase's visible bbox is
                 centered. The finished line has identical left/right margins.

    y_expr: FFmpeg expression that anchors every word to the same font baseline.
    """
    if font_size is None:
        font_size = _fit_caption_size(word_list)
    full_text = _caption_phrase_text(word_list)
    visual_w, left_bearing = _measure_caption_text(full_text, font_size)

    # Anchor is adjusted by the full phrase's left bearing. That centers the
    # actual painted pixels, not the looser advance box.
    x_anchor = (VIDEO_W - visual_w) / 2 - left_bearing

    x_positions = [x_anchor]
    for i in range(1, len(word_list)):
        prefix = _caption_phrase_text(word_list[:i]) + " "
        x_positions.append(x_anchor + _caption_advance(prefix, font_size))

    # FFmpeg's ascent is font-size aware and constant for each word in a phrase,
    # so letters with descenders/caps all sit on one shared baseline.
    y_expr = "(h/2)-ascent"

    return x_positions, y_expr, font_size


def _shared_caption_font_size(
    phrases: list[tuple[list[tuple[str, float, float]], float]],
) -> int:
    """
    Pick one render font size for the whole video.

    The line breaker already avoids lines that need visible shrinking, so this
    should usually stay at _CAPTION_SIZE. If exact font metrics are a little
    wider than the fast estimate, all lines step down together.
    """
    if not phrases:
        return _CAPTION_SIZE

    size = _CAPTION_SIZE
    for word_list, _ in phrases:
        size = min(size, _fit_caption_size(word_list))

    if size < _CAPTION_MIN_PLANNED_SIZE:
        size = _CAPTION_MIN_PLANNED_SIZE

    while size > _CAPTION_MIN_SIZE:
        if all(_caption_text_fits(word_list, size) for word_list, _ in phrases):
            return size
        size -= 2

    return _CAPTION_MIN_SIZE


def _caption_word_key(word: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", word.lower())


def _caption_line_cost(
    words: list[tuple[str, float, float]],
    start_i: int,
    end_i: int,
    total_words: int,
) -> float:
    """
    Score one possible caption line. Lower is better.

    The score balances natural speech boundaries with usable screen width. It
    rewards punctuation and real audio pauses, while penalizing lines that end
    on dangling connector words or leave the next line as a tiny orphan.
    """
    line = words[start_i:end_i]
    if not line:
        return math.inf
    if len(line) > _CAPTION_MAX_WORDS_PER_PHRASE:
        return math.inf
    if not _caption_estimated_fits_at_planned_size(line):
        return math.inf

    is_last = end_i >= total_words
    duration = max(0.0, line[-1][2] - line[0][1])
    font_size = _caption_estimated_fitted_size(line)
    fill = _caption_estimated_fill_ratio(line, font_size)
    end_word = line[-1][0].strip()
    end_key = _caption_word_key(end_word)

    cost = 0.0

    # Keep lines visually intentional, but allow natural short final clauses.
    target_fill = _CAPTION_TARGET_FILL_RATIO if not is_last else 0.70
    cost += abs(fill - target_fill) * 3.0
    if fill < _CAPTION_MIN_FILL_RATIO and not is_last:
        cost += (_CAPTION_MIN_FILL_RATIO - fill) * 14.0
    if len(line) <= 2 and not is_last:
        cost += 8.0

    # Prefer readable font sizes; still allow shrink-to-fit for long thoughts.
    if font_size < 60:
        cost += (60 - font_size) * 0.05

    # Caption cards should breathe with the voice, not stall forever mid-thought.
    if duration < _CAPTION_IDEAL_MIN_DUR and not is_last:
        cost += (_CAPTION_IDEAL_MIN_DUR - duration) * 2.5
    if duration > _CAPTION_IDEAL_MAX_DUR:
        cost += (duration - _CAPTION_IDEAL_MAX_DUR) * 2.0

    # Avoid hiding a natural narrator stop inside the middle of a still-visible
    # line. If the voice or punctuation stops, prefer a new caption line there.
    for k in range(start_i, end_i - 1):
        inner_word = words[k][0].strip()
        inner_pause = max(0.0, words[k + 1][1] - words[k][2])
        if re.search(r"[.!?]$", inner_word):
            cost += 8.0
        elif re.search(r"[,;:]$", inner_word):
            cost += 2.0
        if inner_pause >= 0.55:
            cost += 6.0
        elif inner_pause >= 0.35:
            cost += 3.0

    # Natural linguistic boundaries.
    if re.search(r"[.!?]$", end_word):
        cost -= 4.0
    elif re.search(r"[,;:]$", end_word):
        cost -= 2.0

    # Natural audio boundary from forced alignment.
    if not is_last:
        next_word = words[end_i]
        pause = max(0.0, next_word[1] - line[-1][2])
        next_key = _caption_word_key(next_word[0])
        remaining = total_words - end_i

        if pause >= 0.55:
            cost -= 4.0
        elif pause >= 0.35:
            cost -= 2.0
        elif pause >= 0.22:
            cost -= 0.8

        if end_key in _CAPTION_BAD_END_WORDS:
            cost += 5.0
        if next_key in _CAPTION_BAD_START_WORDS:
            cost += 2.0
        if remaining <= 2:
            cost += 7.0
        elif remaining <= 4:
            cost += 2.0

    return cost


def _choose_caption_lines(
    words: list[tuple[str, float, float]],
) -> list[list[tuple[str, float, float]]]:
    """Find globally good caption line breaks for the whole aligned script."""
    n = len(words)
    if not n:
        return []

    best: list[tuple[float, int | None]] = [(math.inf, None) for _ in range(n + 1)]
    best[n] = (0.0, None)

    for i in range(n - 1, -1, -1):
        for j in range(i + 1, min(n, i + _CAPTION_MAX_WORDS_PER_PHRASE) + 1):
            line = words[i:j]
            if not _caption_estimated_fits_at_planned_size(line):
                break
            line_cost = _caption_line_cost(words, i, j, n)
            future_cost, _ = best[j]
            total_cost = line_cost + future_cost
            if total_cost < best[i][0]:
                best[i] = (total_cost, j)

    groups: list[list[tuple[str, float, float]]] = []
    i = 0
    while i < n:
        j = best[i][1]
        if j is None or j <= i:
            j = min(n, i + 1)
        groups.append(words[i:j])
        i = j

    return groups


def _filter_quote(value: str | Path) -> str:
    """Quote a value for FFmpeg's filter parser when subprocess bypasses the shell."""
    escaped = str(value).replace("\\", "\\\\").replace("'", r"\'")
    return f"'{escaped}'"


def group_into_phrases(
    word_timestamps: list[tuple[str, float, float]],
) -> list[tuple[list[tuple[str, float, float]], float]]:
    """
    Group per-word timestamps into natural spoken caption lines.
    Returns list of (word_list, phrase_t_out) where:
      word_list    — [(word, start, end), ...] for progressive word reveal
      phrase_t_out — when the complete phrase disappears (capped at next phrase start)
    """
    words: list[tuple[str, float, float]] = []
    for word, start, end in word_timestamps:
        word = word.strip()
        if word:
            words.append((word, start, end))

    groups = _choose_caption_lines(words)

    # Phrase t_out = last word end + 2s hold, capped exactly at the next line.
    # No early gap: the old line clears only when the replacement line starts.
    raw = [(g, g[-1][2] + 2.0) for g in groups]
    result = []
    for i, (g, t_out) in enumerate(raw):
        if i + 1 < len(raw):
            next_line_in = max(0.0, raw[i + 1][0][0][1] - _CAPTION_LEAD_IN_S)
            t_out = min(t_out, next_line_in)
        result.append((g, t_out))
    return result

# ---------------------------------------------------------------------------
# Audio classification — sound vs music
# ---------------------------------------------------------------------------

_SOUND_TAGS = {
    "field-recording", "soundscape", "rain", "rainfall", "birds", "birdsong",
    "wind", "chimes", "wind-chimes", "ambience", "outdoor", "nature",
    "environment", "water", "fire", "forest", "thunder", "ocean", "stream",
    "weather", "weather-ambience", "post-rain", "hail", "hailstorm",
}
_MUSIC_TAGS = {
    "piano", "synth", "pads", "beat", "bpm", "melodic", "drum", "guitar",
    "chord", "instrumental", "bass", "melody", "atmospheric", "drone",
    "lofi", "lo-fi", "ambient",
}


def _classify_audio(item: dict) -> str:
    tags = {t.lower() for t in item.get("platform_tags", [])}
    title_words = set(item.get("title", "").lower().split())
    text = tags | title_words
    return "music" if len(text & _MUSIC_TAGS) > len(text & _SOUND_TAGS) else "sound"


def _pick_best_audio(items: list[dict], audio_type: str) -> dict | None:
    """Randomly pick from the top-scored audio of a given type."""
    typed = [a for a in items if _classify_audio(a) == audio_type]
    if not typed:
        return None
    typed.sort(key=lambda a: a.get("score", 0), reverse=True)
    top_score = typed[0].get("score", 0)
    top = [a for a in typed if top_score - a.get("score", 0) <= 0.5]
    return random.choice(top)


# ---------------------------------------------------------------------------
# Video assembly — MoviePy
# ---------------------------------------------------------------------------

def _crop_to_portrait(clip, video_w: int = VIDEO_W, video_h: int = VIDEO_H):
    """Center-crop any clip to 9:16 and resize to target resolution."""
    w, h = clip.size
    if w / h > video_w / video_h:
        new_w = int(h * video_w / video_h)
        x1    = (w - new_w) // 2
        clip  = clip.cropped(x1=x1, x2=x1 + new_w)
    return clip.resized((video_w, video_h)).with_fps(VIDEO_FPS)


def _loop_video_to_duration(clip, duration: float):
    """Repeat a video clip (without audio) until it covers target duration, then trim."""
    from moviepy import concatenate_videoclips
    if clip.duration >= duration:
        return clip.subclipped(0, duration)
    n      = math.ceil(duration / clip.duration) + 1
    looped = concatenate_videoclips([clip] * n, method="compose")
    return looped.subclipped(0, duration)


def _resolve_path(filename: str, items: list[dict]) -> str | None:
    """Find the actual file path for a filename chosen by Gemini."""
    for item in items:
        if Path(item["_path"]).name == filename:
            return item["_path"]
    return None


# Fields sent to Gemini for editorial decisions — full_response excluded (raw API blob, ~500 tokens/item)
_PROMPT_FIELDS = {"source", "id", "title", "keyword", "platform_tags", "duration_s"}
# "score" intentionally excluded — it's already the entry gate (only pool files reach here).
# Sending scores causes Gemini to always pick the highest-scored track rather than
# matching by vibe. Gemini should pick by title/tags/keyword fit, not score chasing.


def assemble_video(
    plan:           dict,
    footage:        list[dict],
    voiceover_path: Path,
    render_path:    Path,
) -> Path:
    """
    Assemble clip + voiceover only → assembly.mp4 via MoviePy.
    Sound and music are added as separate lightweight FFmpeg passes so all
    four draft variants can be produced without re-encoding the video.
    """
    try:
        from moviepy import VideoFileClip, AudioFileClip
    except ImportError:
        print("[editor] moviepy is not installed. Run: pip install moviepy")
        sys.exit(1)

    clips_spec = plan.get("clips", [])
    if not clips_spec:
        print("[editor] No clips in edit plan.")
        sys.exit(1)

    spec      = sorted(clips_spec, key=lambda c: c.get("position", 99))[0]
    path      = _resolve_path(spec["file"], footage)
    if not path:
        print(f"[editor] ERROR: clip '{spec['file']}' not found in pool.")
        sys.exit(1)

    voiceover = AudioFileClip(str(voiceover_path))
    total_dur = voiceover.duration + MUSIC_TAIL_S

    try:
        raw_clip    = VideoFileClip(path)
        clip        = _crop_to_portrait(raw_clip.without_audio())
        final_video = _loop_video_to_duration(clip, total_dur)
        print(f"[editor]   clip: {spec['file']} (looped to {total_dur:.1f}s)")
    except Exception as exc:
        print(f"[editor] ERROR: could not load '{spec['file']}': {exc}")
        sys.exit(1)

    final_video = final_video.with_audio(voiceover)

    assembly_path = render_path / "assembly.mp4"
    print("[editor] Assembling video (this may take a minute)...")
    final_video.write_videofile(
        str(assembly_path),
        fps=VIDEO_FPS,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=str(render_path / "temp_audio.m4a"),
        logger=None,
    )

    clip.close()
    voiceover.close()

    print(f"[editor] Assembly complete → {assembly_path.name} ({total_dur:.1f}s)")
    return assembly_path

# ---------------------------------------------------------------------------
# Final export — FFmpeg: burn captions + loudnorm audio
# ---------------------------------------------------------------------------

def export_final(
    assembly_path: Path,
    phrases:       list[tuple[list[tuple[str, float, float]], float]],
    render_path:   Path,
) -> Path:
    """
    Single FFmpeg pass: burn captions via drawtext + loudnorm → final/draft.mp4
    drawtext uses an absolute font path and freetype directly — no libass, no font lookup.
    Captions fade in word-by-word and hold until the next natural speech line.
    """
    final_dir  = render_path / "final"
    final_dir.mkdir(exist_ok=True)
    draft_path = final_dir / "draft_voice.mp4"

    caption_text_dir = render_path / "caption_text"
    caption_text_dir.mkdir(exist_ok=True)

    # --- Caption filter chain ---
    # Each word is an independent drawtext layer at a fixed (x, y).
    # Words fade in when spoken and stay until phrase_t_out — no string
    # replacement means no flicker between words.
    #
    # y uses FFmpeg's `ascent` runtime variable, so every word in a phrase is
    # anchored to one shared baseline regardless of the glyphs in that word.
    dt_parts: list[str] = []
    shared_font_size = _shared_caption_font_size(phrases)
    print(f"[captions] render font size: {shared_font_size}px")
    for pi, (word_list, phrase_t_out) in enumerate(phrases):
        x_positions, y_expr, font_size = _phrase_layout(word_list, shared_font_size)

        for wi, (word, w_start, _) in enumerate(word_list):
            word_in = max(0.0, w_start - _CAPTION_LEAD_IN_S)
            # Per-word fade-in. Once visible, a word holds until the full line
            # is replaced by the next line.
            alpha_expr = (
                f"if(lt(t,{word_in + _FADE_IN_DUR:.3f}),"
                f"max(0,(t-{word_in:.3f})/{_FADE_IN_DUR:.3f}),"
                "1)"
            )
            text_path = caption_text_dir / f"p{pi:03d}_w{wi:02d}.txt"
            text_path.write_text(word)
            dt_parts.append(
                f"drawtext=fontfile={_filter_quote(_CAPTION_FONT)}"
                f":textfile={_filter_quote(text_path)}"
                f":fontsize={font_size}"
                f":fontcolor=white:bordercolor=black:borderw={_CAPTION_BORDER_W}"
                f":x={x_positions[wi]:.3f}:y={y_expr}"
                f":enable='between(t,{word_in:.3f},{phrase_t_out:.3f})'"
                f":alpha={_filter_quote(alpha_expr)}"
            )

    # Fade only when there is a real music tail, so speech-only drafts do not
    # have their final words faded out by the mastering pass.
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(assembly_path)],
        capture_output=True, text=True,
    )
    try:
        total_s = float(json.loads(probe.stdout)["format"]["duration"])
        last_spoken_s = max((w[2] for phrase, _ in phrases for w in phrase), default=0.0)
        has_music_tail = total_s - last_spoken_s >= MUSIC_TAIL_S - 0.5
        audio_filters = [f"loudnorm=I={TARGET_LUFS}:LRA=11:TP=-1.5"]
        if has_music_tail:
            fade_start = max(0.0, total_s - MUSIC_FADE_OUT_S)
            audio_filters.append(
                f"afade=type=out:start_time={fade_start:.3f}:duration={MUSIC_FADE_OUT_S:.3f}"
            )
        af = ",".join(audio_filters)

        # Watermark: two centred lines fade in together over 1 s, 3 s before the end.
        # Soft warm-white at 70% opacity max — matches the muted lo-fi aesthetic.
        wm_start = max(0.0, total_s - 3.0)
        wm_alpha = (
            f"if(lt(t,{wm_start + 1.0:.3f}),"
            f"max(0,(t-{wm_start:.3f})/1.0)*0.7,"
            "0.7)"
        )
        wm_common = (
            f"fontfile={_filter_quote(_CAPTION_FONT)}"
            f":fontsize={shared_font_size}"
            f":fontcolor=0xFFF5E4:bordercolor=black:borderw=1"
            f":enable='between(t,{wm_start:.3f},{total_s:.3f})'"
            f":alpha={_filter_quote(wm_alpha)}"
        )
        # Two lines treated as one block, centred at the same point as the voiceover captions.
        # (h/2)-th-4 puts line 1 just above centre; (h/2)+6 puts line 2 just below.
        dt_parts.append(f"drawtext={wm_common}:text='Follow for':x=(w-tw)/2:y=(h/2)-th-4")
        dt_parts.append(f"drawtext={wm_common}:text='Daily Healing':x=(w-tw)/2:y=(h/2)+6")
    except Exception:
        af = f"loudnorm=I={TARGET_LUFS}:LRA=11:TP=-1.5"

    vf = ",".join(dt_parts) if dt_parts else "null"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(assembly_path),
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-r", str(VIDEO_FPS),
        str(draft_path),
    ]

    # Print the first phrase so we can verify escaping looks correct
    if dt_parts:
        print(f"[captions] sample filter: {dt_parts[0][:180]}")

    print("[editor] Exporting final draft with captions + audio normalization...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Always surface drawtext warnings — text issues are silent without this
    dt_lines = [l for l in result.stderr.splitlines()
                if any(k in l.lower() for k in ("drawtext", "text", "font", "alpha", "parse"))]
    if dt_lines:
        print("[captions] FFmpeg drawtext log:")
        for l in dt_lines[:6]:
            print(f"  {l}")

    if result.returncode != 0:
        print("[editor] FFmpeg export failed:")
        print(result.stderr[-3000:])
        sys.exit(1)

    # Extract frame at t=2s to visually verify text rendered
    test_frame = render_path / "test_frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "2", "-i", str(draft_path),
         "-frames:v", "1", str(test_frame)],
        capture_output=True,
    )

    size_mb = draft_path.stat().st_size / 1_048_576
    print(f"[editor] draft_voice.mp4 → {size_mb:.1f} MB")
    return draft_path

# ---------------------------------------------------------------------------
# Music variant — lightweight FFmpeg pass, video stream copied not re-encoded
# ---------------------------------------------------------------------------

def _mix_audio_variant(
    base_path:   Path,
    tracks:      list[tuple[str, float]],  # [(file_path, volume), ...]
    output_name: str,
    render_path: Path,
) -> Path | None:
    """
    Mix one or more audio tracks into base_path → final/{output_name}.mp4.
    Video stream is copied (no re-encode). Each track is looped and faded.
    """
    valid = [(p, v) for p, v in tracks if Path(p).exists()]
    if not valid:
        print(f"[editor] WARNING: no valid audio for {output_name} — skipping")
        return None

    out_path = render_path / "final" / f"{output_name}.mp4"

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(base_path)],
        capture_output=True, text=True,
    )
    try:
        total_s = float(json.loads(probe.stdout)["format"]["duration"])
    except Exception:
        total_s = 30.0

    fade_start = max(0.0, total_s - MUSIC_FADE_OUT_S)

    cmd = ["ffmpeg", "-y", "-i", str(base_path)]
    for path, _ in valid:
        cmd += ["-i", str(path)]

    filter_parts = [
        f"[{i + 1}:a]aloop=loop=-1:size=2000000000,"
        f"atrim=duration={total_s:.3f},"
        f"volume={vol:.2f},"
        f"afade=type=out:start_time={fade_start:.3f}:duration={MUSIC_FADE_OUT_S:.3f}[t{i}]"
        for i, (_, vol) in enumerate(valid)
    ]
    mix_inputs = "[0:a]" + "".join(f"[t{i}]" for i in range(len(valid)))
    # duration=longest: keep mixing until the trimmed music ends (= full video length),
    # not when the voiceover audio ends. normalize=0 prevents a volume jump when
    # the voiceover stream finishes and only the music track remains.
    filter_parts.append(f"{mix_inputs}amix=inputs={len(valid) + 1}:duration=longest:normalize=0[aout]")

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[editor] WARNING: {output_name} failed:\n{result.stderr[-500:]}")
        return None

    size_mb = out_path.stat().st_size / 1_048_576
    names   = ", ".join(Path(p).name for p, _ in valid)
    print(f"[editor] {output_name}.mp4 → {size_mb:.1f} MB  ({names})")
    return out_path


# ---------------------------------------------------------------------------
# Deliverables — caption.txt and posting_notes.txt
# ---------------------------------------------------------------------------

def write_deliverables(plan: dict, render_path: Path, caption_body: str = "") -> None:
    final_dir = render_path / "final"
    final_dir.mkdir(exist_ok=True)

    # caption.txt — voiceover text with punctuation + hashtags, ready to paste into Instagram
    hashtags     = " ".join(f"#{t.lstrip('#')}" for t in plan.get("hashtags", []))
    caption_text = f"{caption_body}\n\n{hashtags}\n" if hashtags else f"{caption_body}\n"
    (final_dir / "caption.txt").write_text(caption_text)

    # posting_notes.txt — music suggestions + checklist
    suggestions = plan.get("music_suggestions", [])
    music_lines = "\n".join(
        f"  Option {chr(65 + i)}: \"{s['name']}\" — {s['reason']}"
        for i, s in enumerate(suggestions)
    )
    notes = (
        "Suggested Instagram music (search by name in the app):\n"
        f"{music_lines}\n\n"
        "Your CC0 audio is already baked in. Instagram music is optional on top.\n"
        "Add it in the app before posting (~30 seconds).\n\n"
        "Posting checklist:\n"
        "  [ ] Watch the drafts (draft_voice / draft_sound / draft_music / draft)\n"
        "  [ ] Copy caption.txt into Instagram caption field\n"
        "  [ ] Add Instagram music (optional)\n"
        "  [ ] Post\n"
        "  [ ] Run: python editor_agent.py --feedback\n"
    )
    (final_dir / "posting_notes.txt").write_text(notes)
    print("[editor] caption.txt and posting_notes.txt written → final/")

# ---------------------------------------------------------------------------
# World bible helpers
# ---------------------------------------------------------------------------

def load_world_bible() -> dict:
    if WORLD_BIBLE_PATH.exists():
        return json.loads(WORLD_BIBLE_PATH.read_text())
    return {}


def save_world_bible(bible: dict) -> None:
    bible["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    WORLD_BIBLE_PATH.write_text(json.dumps(bible, indent=2))

# ---------------------------------------------------------------------------
# Feedback mode — free-form notes → structured learnings → world_bible
# ---------------------------------------------------------------------------

def extract_learnings(
    notes:    str,
    edit_log: dict,
    client:   genai.Client,
) -> dict:
    """Send user's free-form notes + edit_log to Gemini → structured learnings JSON."""
    prompt = f"""Extract structured editing learnings from the user's feedback notes below.

EDIT LOG from this run:
{json.dumps(edit_log, indent=2)}

USER FEEDBACK:
{notes}

Return ONLY valid JSON — no markdown fences:
{{
  "pacing": {{
    "change": true or false,
    "preferred_cuts_per_30s": null or integer,
    "preferred_clip_duration_s": null or integer,
    "note": "verbatim feel of what the user said"
  }},
  "audio": {{
    "change": true or false,
    "music_mix_level": null or float between 0.0 and 1.0,
    "note": "what the user said"
  }},
  "captions": {{
    "change": true or false,
    "note": "what the user said"
  }},
  "clip_feedback": {{
    "liked":    [],
    "disliked": []
  }},
  "general_note": "one-sentence summary of the overall feedback"
}}

Only mark change=true when the user clearly said something should be different.
If ambiguous, mark change=false and add the note for context."""

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=GEMINI_EDIT_MODEL, contents=prompt)
            _log_tokens(response, "extract_learnings")
            raw = response.text.strip()
            if raw.startswith("```"):
                parts = raw.split("```")
                raw   = parts[1] if len(parts) > 1 else raw
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip().rstrip("`").strip())

        except json.JSONDecodeError as exc:
            print(f"[feedback] JSON parse error (attempt {attempt + 1}/3): {exc}")
            if attempt == 2:
                return {}

        except Exception as exc:
            print(f"[feedback] Gemini error: {exc}")
            if attempt == 2:
                return {}

    return {}


def apply_editor_learnings(bible: dict, learnings: dict) -> None:
    """Merge structured learnings into world_bible's editor section."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Initialise editor section on first feedback run
    editor = bible.setdefault("editor", {
        "pacing":   {"preferred_cuts_per_30s": 3, "preferred_clip_duration_s": 10, "notes": ""},
        "audio":    {"music_mix_level": 0.55, "notes": ""},
        "captions": {
            "max_words_per_card": 8,
            "preferred_display_s": 4.0,
            "font":      "Cormorant",
            "color":     "#F5F0E8",
            "animation": "fade_word",
            "notes":     "",
        },
        "winning_combinations": [],
        "explicit_rejects":     [],
        "run_history":          [],
    })

    if learnings.get("pacing", {}).get("change"):
        p = learnings["pacing"]
        if p.get("preferred_cuts_per_30s") is not None:
            editor["pacing"]["preferred_cuts_per_30s"] = p["preferred_cuts_per_30s"]
        if p.get("preferred_clip_duration_s") is not None:
            editor["pacing"]["preferred_clip_duration_s"] = p["preferred_clip_duration_s"]
        if p.get("note"):
            editor["pacing"]["notes"] = f"{p['note']} ({today})"

    if learnings.get("audio", {}).get("change"):
        a = learnings["audio"]
        if a.get("music_mix_level") is not None:
            editor["audio"]["music_mix_level"] = a["music_mix_level"]
        if a.get("note"):
            editor["audio"]["notes"] = f"{a['note']} ({today})"

    if learnings.get("captions", {}).get("change"):
        c = learnings["captions"]
        if c.get("note"):
            editor["captions"]["notes"] = f"{c['note']} ({today})"

    # Track liked/disliked clips across runs for future Claude prompts
    liked    = learnings.get("clip_feedback", {}).get("liked", [])
    disliked = learnings.get("clip_feedback", {}).get("disliked", [])
    if liked:
        editor["winning_combinations"].extend(liked)
    if disliked:
        editor["explicit_rejects"].extend(disliked)

    if learnings.get("general_note"):
        editor["run_history"].append({"date": today, "note": learnings["general_note"]})


def run_feedback_mode(args: argparse.Namespace, client: genai.Client) -> None:
    render_path = _find_render(getattr(args, "render", None))
    log_file    = render_path / "edit_log.json"

    if not log_file.exists():
        print(f"[feedback] No edit_log.json in {render_path.name}")
        print("[feedback] Run without --feedback first to assemble a draft.")
        sys.exit(1)

    edit_log = json.loads(log_file.read_text())
    bible    = load_world_bible()

    print(f"\n=== Edit Feedback — render {render_path.name} ===")
    print("Type your notes about the draft (any language, no structure needed).")
    print("Press Ctrl+D when done.\n")

    try:
        notes = sys.stdin.read().strip()
    except KeyboardInterrupt:
        print("\nCancelled.")
        return

    if not notes:
        print("No notes entered.")
        return

    print("\n[feedback] Sending to Gemini...")
    learnings = extract_learnings(notes, edit_log, client)

    if not learnings:
        print("[feedback] Could not extract learnings — world_bible not updated.")
        return

    apply_editor_learnings(bible, learnings)
    save_world_bible(bible)

    editor = bible.get("editor", {})
    print(f"\n  world_bible.json updated.")
    print(f"  Summary    : {learnings.get('general_note', '—')}")
    print(f"  Pacing     : {editor.get('pacing', {})}")
    print(f"  Audio      : {editor.get('audio', {})}")
    print()

# ---------------------------------------------------------------------------
# Pool management — retire used footage after assembly
# ---------------------------------------------------------------------------

def _retire_used_footage(plan: dict) -> None:
    """
    Move footage clips used in this edit to pool/footage/used/.
    Registry marks them so agent.py won't re-download even after the files are deleted.
    Audio stays in pool/audio/ — it is reusable; only marked in registry, not moved.
    """
    import shutil as _shutil
    import pool_registry
    used_dir   = POOL_DIR / "footage" / "used"
    used_dir.mkdir(parents=True, exist_ok=True)
    retired    = 0
    used_metas: list[dict] = []

    for clip in plan.get("clips", []):
        filename = clip.get("file", "")
        if not filename:
            continue
        media_file = POOL_DIR / "footage" / filename
        if not media_file.exists():
            continue
        stem      = media_file.stem
        meta_file = POOL_DIR / "footage" / f"{stem}_metadata.json"
        # Read metadata before moving so registry can record source+id
        if meta_file.exists():
            try:
                used_metas.append(json.loads(meta_file.read_text()))
            except Exception:
                pass
        _shutil.move(str(media_file), used_dir / media_file.name)
        if meta_file.exists():
            _shutil.move(str(meta_file), used_dir / meta_file.name)
        retired += 1

    if used_metas:
        pool_registry.mark_many(used_metas, "used")
    if retired:
        print(f"[editor] {retired} footage clip(s) retired to pool/footage/used/")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _reset_pool() -> None:
    """Move all footage from pool/footage/used/ back to pool/footage/ for reuse."""
    import shutil as _shutil
    used_dir = POOL_DIR / "footage" / "used"
    if not used_dir.exists() or not list(used_dir.iterdir()):
        print("[reset] pool/footage/used/ is already empty — nothing to restore.")
        return
    count = sum(1 for _ in used_dir.iterdir())
    print(f"\n  This will restore ALL {count} used footage clip(s) back to the pool.")
    print("  The pipeline will be able to pick any of them again on the next run.")
    answer = input("\n  Are you sure? (yes / no): ").strip().lower()
    if answer not in ("yes", "y"):
        print("[reset] Cancelled.")
        return
    restored = 0
    for f in used_dir.iterdir():
        _shutil.move(str(f), POOL_DIR / "footage" / f.name)
        restored += 1
    print(f"[reset] {restored} file(s) restored from used/ → pool/footage/")
    print("[reset] All footage clips are available again for the next run.")


def _log_voiceover(render_name: str, text: str) -> None:
    """Append this render's voiceover text to the persistent history log."""
    RENDERS_DIR.mkdir(exist_ok=True)
    with VOICEOVER_HISTORY_PATH.open("a") as f:
        f.write(f"[{render_name}] {text.strip()}\n")
    print(f"[editor] Voiceover logged → voiceover_history.txt")


def _remove_voiceover_log(render_name: str) -> None:
    """Remove a render's voiceover entry from the history log on restore."""
    if not VOICEOVER_HISTORY_PATH.exists():
        return
    lines = VOICEOVER_HISTORY_PATH.read_text().splitlines()
    kept = [l for l in lines if not l.startswith(f"[{render_name}]")]
    if len(kept) < len(lines):
        VOICEOVER_HISTORY_PATH.write_text("\n".join(kept) + ("\n" if kept else ""))
        print(f"[restore] Voiceover entry for '{render_name}' removed from history.")


def _restore_render(render_name: str | None) -> None:
    """
    Move footage used in a specific render back to pool/footage/ for reuse.
    Reads edit_log.json from the render folder to know exactly which clip to restore.
    If render_name is None, uses the most recent render folder.
    """
    import shutil as _shutil

    # Resolve render folder
    if render_name:
        render_path = RENDERS_DIR / render_name
        if not render_path.exists():
            matches = sorted(d for d in RENDERS_DIR.iterdir() if d.is_dir() and render_name in d.name)
            if len(matches) == 1:
                render_path = matches[0]
            elif len(matches) > 1:
                print(f"[restore] Ambiguous name '{render_name}'. Did you mean one of these?")
                for m in matches:
                    print(f"  {m.name}")
                return
            else:
                print(f"[restore] Render '{render_name}' not found in {RENDERS_DIR}")
                return
    else:
        folders = sorted(d for d in RENDERS_DIR.iterdir() if d.is_dir())
        if not folders:
            print("[restore] No render folders found.")
            return
        render_path = folders[-1]
        print(f"[restore] No render specified — using most recent: {render_path.name}")

    log_file = render_path / "edit_log.json"
    if not log_file.exists():
        print(f"[restore] No edit_log.json in {render_path.name} — cannot determine which clips to restore.")
        return

    clips = json.loads(log_file.read_text()).get("clips", [])
    if not clips:
        print(f"[restore] No clips recorded in {render_path.name}/edit_log.json")
        return

    used_dir = POOL_DIR / "footage" / "used"
    restored = 0
    for clip in clips:
        filename = clip.get("file", "")
        if not filename:
            continue
        used_file = used_dir / filename
        if not used_file.exists():
            print(f"[restore] '{filename}' not in used/ — already in pool or missing")
            continue
        _shutil.move(str(used_file), POOL_DIR / "footage" / filename)
        restored += 1
        meta = used_dir / f"{Path(filename).stem}_metadata.json"
        if meta.exists():
            _shutil.move(str(meta), POOL_DIR / "footage" / meta.name)

    if restored:
        print(f"[restore] {restored} clip(s) from '{render_path.name}' restored to pool/footage/")
    else:
        print(f"[restore] Nothing to restore — clips may already be in pool or were never retired.")
    _remove_voiceover_log(render_path.name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble a healing reel from the global footage/audio pool."
    )
    parser.add_argument("--feedback", action="store_true",
                        help="Log notes on the most recent render and update world_bible")
    parser.add_argument("--render", help="Render date YYYY-MM-DD for --feedback (default: most recent)")
    parser.add_argument("--reset-pool", action="store_true",
                        help="Move ALL footage from used/ back to pool")
    parser.add_argument("--restore-render", metavar="RENDER", nargs="?", const="",
                        help="Restore footage from a specific render back to pool (e.g. 2026-05-05_001). Omit value to use most recent.")
    parser.add_argument("--carousel", action="store_true",
                        help="Render carousel posts from the latest batch carousel_scripts.json + pool/images/")
    parser.add_argument("--story", action="store_true",
                        help="Render story/single-image quote posts from the latest batch quotes.json + pool/images/")
    args = parser.parse_args()

    if args.reset_pool:
        _reset_pool()
        return

    if args.restore_render is not None:
        _restore_render(args.restore_render if args.restore_render else None)
        return

    validate_env()
    keys   = _load_keys()
    client = _init_gemini(keys)

    if args.feedback:
        run_feedback_mode(args, client)
        return

    # ── Carousel mode ────────────────────────────────────────────────────────
    if args.carousel:
        batch_path    = _find_latest_batch()
        render_path   = _make_render_path("carousel")
        content       = load_content_data(batch_path)
        images        = load_pool_images()
        carousels     = content.get("carousels", [])

        print(f"\n=== Editor Agent — carousel mode (context: {batch_path.name}) ===")
        print(f"  {len(carousels)} carousel(s) · {len(images)} approved image(s) in pool\n")

        if not carousels:
            print("[editor] No carousel scripts found. Run agent.py first.")
            return
        if not images:
            print("[editor] No images in pool. Run agent.py then: python feedback.py --images")
            return

        all_outputs: list[Path] = []
        for i, carousel in enumerate(carousels, 1):
            outputs = render_carousel(carousel, images, render_path, i, client)
            all_outputs.extend(outputs)

        print(f"\n{'─' * 52}")
        print(f"  Rendered {len(all_outputs)} slides across {len(carousels)} carousel(s)")
        print(f"  Output  : {render_path}/")
        print(f"{'─' * 52}")
        print("\nTip: also run  python editor_story.py  to render quote posts.")
        return

    # ── Story / single-image quote mode ─────────────────────────────────────
    if args.story:
        batch_path  = _find_latest_batch()
        render_path = _make_render_path("story")
        content     = load_content_data(batch_path)
        images      = load_pool_images()
        quotes      = content.get("quotes", [])

        print(f"\n=== Editor Agent — story post mode (context: {batch_path.name}) ===")
        print(f"  {len(quotes)} quote(s) · {len(images)} approved image(s) in pool\n")

        if not quotes:
            print("[editor] No quotes found. Run agent.py first.")
            return
        if not images:
            print("[editor] No images in pool. Run agent.py then: python feedback.py --images")
            return

        outputs = render_story_posts(quotes, images, render_path, client)

        print(f"\n{'─' * 52}")
        print(f"  Rendered {len(outputs)} story post(s)")
        print(f"  Output  : {render_path}/story_quotes/")
        print(f"{'─' * 52}")
        return

    # Context from most recent agent.py run; output to fresh render folder
    batch_path   = _find_latest_batch()
    render_path  = _make_render_path()
    print(f"\n=== Editor Agent — render {render_path.name} (context: {batch_path.name}) ===\n")

    # Load
    footage, audio = load_good_files()
    context        = load_context(batch_path)

    # Plan — Gemini 2.5 Pro decides everything; log written before a single frame renders
    plan = plan_edit(footage, audio, context, client)
    save_edit_log(plan, render_path)

    # Resolve Gemini's sound + music choices; fall back to Python if Gemini returns null
    sound_file = (plan.get("sound") or {}).get("file", "")
    music_file  = (plan.get("music") or {}).get("file", "")
    sound_item  = next((a for a in audio if Path(a["_path"]).name == sound_file), None)
    music_item  = next((a for a in audio if Path(a["_path"]).name == music_file), None)

    # Python fallback — only triggers when Gemini returned null
    if not sound_item:
        sound_item = _pick_best_audio(audio, "sound")
        if sound_item:
            print(f"[editor] Sound: {Path(sound_item['_path']).name} (fallback)")
    else:
        print(f"[editor] Sound: {Path(sound_item['_path']).name} (Gemini)")
    if not music_item:
        music_item = _pick_best_audio(audio, "music")
        if music_item:
            print(f"[editor] Music: {Path(music_item['_path']).name} (fallback)")
    else:
        print(f"[editor] Music: {Path(music_item['_path']).name} (Gemini)")

    # Voiceover — one Gemini Flash pass: fix errors + produce punctuated caption version
    plan["voiceover_script"], caption_body = _process_voiceover(plan["voiceover_script"], client)
    script_path = save_script(plan, render_path)
    voice_path  = generate_voice(plan["voiceover_script"], script_path, render_path)

    # Caption alignment → phrase grouping
    word_timestamps = align_captions(voice_path, plan["voiceover_script"])
    phrases         = group_into_phrases(word_timestamps)
    word_count = sum(len(g) for g, _ in phrases)
    print(f"[captions] {len(phrases)} phrase(s), {word_count} word filters ready for drawtext")

    # Video — voiceover only in MoviePy; sound/music added via FFmpeg passes
    assembly_path    = assemble_video(plan, footage, voice_path, render_path)
    draft_voice_path = export_final(assembly_path, phrases, render_path)

    # Two variants — fast FFmpeg passes, video stream copied not re-encoded
    sp = sound_item["_path"] if sound_item else None
    mp = music_item["_path"] if music_item else None

    if sp:
        _mix_audio_variant(draft_voice_path, [(sp, 0.45)], "draft_sound", render_path)
    if mp:
        _mix_audio_variant(draft_voice_path, [(mp, 0.40)], "draft_music", render_path)

    # Deliverables
    write_deliverables(plan, render_path, caption_body)

    # Move used footage to pool/footage/used/ — single-use rule; mark in registry
    _retire_used_footage(plan)

    # Mark audio used in this render so agent.py won't re-fetch if files are deleted
    import pool_registry as _preg
    _audio_used = [a for a in [sound_item, music_item] if a]
    if _audio_used:
        _preg.mark_many(_audio_used, "used")
        print(f"[editor] {len(_audio_used)} audio track(s) marked as used in registry")

    # Log voiceover text for diversity memory and history tracking
    _log_voiceover(render_path.name, plan["voiceover_script"])

    final = render_path / "final"
    print(f"\n{'─' * 52}")
    print(f"  draft_voice.mp4 : voiceover only")
    if sp: print(f"  draft_sound.mp4 : voiceover + sound")
    if mp: print(f"  draft_music.mp4 : voiceover + music")
    print(f"  Caption    : {final / 'caption.txt'}")
    print(f"  Notes      : {final / 'posting_notes.txt'}")
    print(f"  Edit log   : {render_path / 'edit_log.json'}")
    print(f"{'─' * 52}")
    print(f"\nWatch the drafts, then run:")
    print(f"  python editor_agent.py --feedback")
    print(f"to log your notes and improve the next run.\n")


if __name__ == "__main__":
    main()
