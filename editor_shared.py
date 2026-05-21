"""
editor_shared.py — utilities shared by editor_carousel.py and editor_story.py.
Not used by editor_reels.py (video pipeline is entirely separate).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from google import genai

BASE_DIR         = Path(__file__).parent
DATA_DIR         = BASE_DIR / "data"
POOL_DIR         = DATA_DIR / "sort_media"
IMAGE_POOL_DIR   = POOL_DIR / "images"
RENDERS_DIR      = DATA_DIR / "pending"
BATCH_ROOT       = DATA_DIR / "scrape"
WORLD_BIBLE_PATH = DATA_DIR / "world_bible.json"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
CAROUSEL_W       = 1080
CAROUSEL_H       = 1350   # 4:5 portrait — standard Instagram carousel
STORY_W          = 1080
STORY_H          = 1920   # 9:16 — story / reel cover
_RENDER_FONT        = "/usr/share/fonts/opentype/linux-libertine/LinLibertine_RB.otf"
_RENDER_FONT_BI     = "/usr/share/fonts/opentype/linux-libertine/LinLibertine_RBI.otf"

GEMINI_IMAGE_MODEL   = "gemini-2.5-flash"
BEIGE_BG_PATH        = DATA_DIR / "Beige_background.png"


# ---------------------------------------------------------------------------
# Batch + render path navigation
# ---------------------------------------------------------------------------

def find_latest_batch() -> Path:
    if not BATCH_ROOT.exists() or not (BATCH_ROOT / "intelligence").exists():
        raise RuntimeError("No staging content found — run agent.py first.")
    return BATCH_ROOT


def make_render_path(content_type: str = "") -> Path:
    """Return the next available numbered render path — folder is NOT created yet.
    Creation happens lazily on first write so empty folders are never left behind."""
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = f"_{content_type}" if content_type else ""
    n = 1
    while True:
        path = RENDERS_DIR / f"{today}_{n:03d}{suffix}"
        if not path.exists():
            return path
        n += 1


# ---------------------------------------------------------------------------
# World bible
# ---------------------------------------------------------------------------

def load_world_bible() -> dict:
    if WORLD_BIBLE_PATH.exists():
        return json.loads(WORLD_BIBLE_PATH.read_text())
    return {}


def save_world_bible(bible: dict) -> None:
    bible["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    WORLD_BIBLE_PATH.write_text(json.dumps(bible, indent=2))


# ---------------------------------------------------------------------------
# Image pool
# ---------------------------------------------------------------------------

def load_pool_images() -> list[dict]:
    """Load approved images from pool/images/(priority|standard)."""
    images: list[dict] = []
    for subdir in ["priority", "standard"]:
        dir_path = IMAGE_POOL_DIR / subdir
        if not dir_path.exists():
            continue
        for meta_file in sorted(dir_path.glob("*_metadata.json")):
            base = meta_file.stem.replace("_metadata", "")
            img_file = next(
                (f for f in dir_path.iterdir()
                 if f.stem == base and f.suffix.lower() in IMAGE_EXTENSIONS),
                None,
            )
            if not img_file:
                continue
            try:
                meta = json.loads(meta_file.read_text())
                meta["_path"]   = str(img_file)
                meta["_folder"] = f"images/{subdir}"
                images.append(meta)
            except Exception:
                continue
    return images


def load_content_data(batch_path: Path) -> dict:
    """Read carousel_scripts.json, quotes.json, and quote_hashtags.json from intelligence/."""
    intel     = batch_path / "intelligence"
    carousels:      list[dict]       = []
    quotes:         list[str]        = []
    quote_hashtags: list[list[str]]  = []

    carousel_file = intel / "carousel_scripts.json"
    if carousel_file.exists():
        try:
            carousels = json.loads(carousel_file.read_text())
        except Exception as exc:
            print(f"[shared] WARNING: carousel_scripts.json unreadable: {exc}")

    quotes_file = intel / "quotes.json"
    if quotes_file.exists():
        try:
            quotes = json.loads(quotes_file.read_text())
        except Exception as exc:
            print(f"[shared] WARNING: quotes.json unreadable: {exc}")

    hashtags_file = intel / "quote_hashtags.json"
    if hashtags_file.exists():
        try:
            quote_hashtags = json.loads(hashtags_file.read_text())
        except Exception as exc:
            print(f"[shared] WARNING: quote_hashtags.json unreadable: {exc}")

    return {"carousels": carousels, "quotes": quotes, "quote_hashtags": quote_hashtags}


# ---------------------------------------------------------------------------
# Gemini image assignment — theme-first strategy
# ---------------------------------------------------------------------------

def plan_image_assignments(
    slides: list[str],
    theme: str,
    images: list[dict],
    client: genai.Client,
) -> list[int]:
    """
    Theme-first: Gemini picks ONE visual world that fits the carousel's emotional topic,
    then assigns images within that world slide-by-slide.

    Intensity arc:  lighter/open early → more intimate at peak → same as slide 1 at close.
    Repeating images is expected and allowed — coherence beats variety.
    Returns 0-based indices, one per slide.
    """
    img_list = [
        {
            "idx":     i,
            "title":   img.get("title", "")[:60],
            "keyword": img.get("keyword", ""),
            "score":   img.get("score", 0),
            "tags":    img.get("platform_tags", [])[:5],
            "tier":    img.get("_folder", ""),
        }
        for i, img in enumerate(images[:20])
    ]
    prompt = f"""You are pairing images to slides for an Instagram healing post.

## Theme: "{theme}"
## Slides ({len(slides)} total):
{json.dumps([{"slide": i + 1, "text": s} for i, s in enumerate(slides)], indent=2)}

## Available images ({len(img_list)}):
{json.dumps(img_list, indent=2)}

## Task — two steps:

STEP 1 — Choose ONE visual world for this post.
A visual world = one consistent setting/atmosphere that runs through ALL slides.
Examples: "rainy window", "forest path", "café corner", "golden field", "bedroom light", "ocean edge".
Choose the world that best fits the emotional topic. Coherence matters more than variety.

STEP 2 — Assign images within that world to each slide.
Rules:
- One index (0-based) per slide. Reuse images freely — pool may be smaller than slide count.
- Slide 1 (hook): highest-score or most atmospheric image — this stops the scroll.
- Middle slides: vary composition within your world (closer, further, different angle).
- Last slide (resolution): repeat slide 1 image OR second-best — creates a satisfying loop.
- Do NOT jump between very different settings (forest → café → bedroom) in one post.

Return ONLY valid JSON — no markdown, no explanation:
{{
  "visual_world": "short phrase (2–4 words)",
  "reasoning": "one sentence on why this world fits the theme",
  "assignments": [0, 2, 1, 2, 0]
}}
(assignments must have exactly {len(slides)} integers, 0-based, within 0–{len(images) - 1})"""

    for attempt in range(2):
        try:
            response = client.models.generate_content(model=GEMINI_IMAGE_MODEL, contents=prompt)
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            data    = json.loads(raw)
            indices = data.get("assignments", [])
            if isinstance(indices, list) and len(indices) == len(slides):
                world = data.get("visual_world", "")
                if world:
                    print(f"  visual world : {world!r}")
                    print(f"  reasoning    : {data.get('reasoning', '')}")
                n = len(images)
                return [max(0, min(int(idx), n - 1)) for idx in indices]
        except Exception as exc:
            print(f"[shared] plan_images attempt {attempt + 1}: {exc}")
            if attempt == 0:
                time.sleep(10)

    # Fallback: cycle through images; same on first and last
    n      = len(images)
    result = [i % n for i in range(len(slides))]
    if len(slides) > 1:
        result[-1] = result[0]
    return result


# ---------------------------------------------------------------------------
# Registry — mark images used in a render
# ---------------------------------------------------------------------------

def mark_images_used(images: list[dict], indices: list[int]) -> None:
    """Mark each unique image placed in a render as 'used' in pool_registry."""
    import pool_registry
    used = [images[i] for i in set(indices) if 0 <= i < len(images)]
    pool_registry.mark_many(used, "used")


# ---------------------------------------------------------------------------
# PIL slide renderer
# ---------------------------------------------------------------------------

def render_slide(img_path: str, text: str, out_path: Path, w: int, h: int) -> None:
    """
    Crop image to target ratio, add warm gradient, centre-render text.
    Font: LinLibertine — same as video captions for consistency across all three products.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(img_path).convert("RGB")

    target_ratio = w / h
    img_ratio    = img.width / img.height
    if img_ratio > target_ratio:
        new_w = int(img.height * target_ratio)
        left  = (img.width - new_w) // 2
        img   = img.crop((left, 0, left + new_w, img.height))
    else:
        new_h = int(img.width / target_ratio)
        top   = (img.height - new_h) // 2
        img   = img.crop((0, top, img.width, top + new_h))
    img = img.resize((w, h), Image.LANCZOS)

    # Warm gradient: near-transparent at top, moderately dark at bottom
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    for y_px in range(h):
        alpha = int(30 + 140 * (y_px / h) ** 1.4)
        draw_ov.line([(0, y_px), (w, y_px)], fill=(10, 5, 0, min(alpha, 170)))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    draw       = ImageDraw.Draw(img)
    max_text_w = int(w * 0.80)
    font       = None
    lines:     list[str] = []
    chosen_sz  = 48

    for size in range(88, 30, -4):
        try:
            font = ImageFont.truetype(_RENDER_FONT, size)
        except Exception:
            font      = ImageFont.load_default()
            size      = 36

        words      = text.split()
        cur_lines: list[str] = []
        current    = ""
        for word in words:
            test = f"{current} {word}".strip()
            try:
                tw = draw.textlength(test, font=font)
            except Exception:
                tw = len(test) * size * 0.6
            if tw <= max_text_w:
                current = test
            else:
                if current:
                    cur_lines.append(current)
                current = word
        if current:
            cur_lines.append(current)

        line_h  = int(size * 1.45)
        total_h = len(cur_lines) * line_h
        chosen_sz = size
        lines     = cur_lines
        if total_h <= int(h * 0.52):
            break

    line_h  = int(chosen_sz * 1.45)
    total_h = len(lines) * line_h
    y_start = (h - total_h) // 2 + int(h * 0.04)

    for line in lines:
        try:
            text_w = draw.textlength(line, font=font)
        except Exception:
            text_w = len(line) * chosen_sz * 0.55
        x = int((w - text_w) / 2)
        draw.text((x + 2, y_start + 2), line, fill=(0, 0, 0, 140), font=font)
        draw.text((x, y_start),         line, fill=(255, 251, 242), font=font)
        y_start += line_h

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "JPEG", quality=95)


def render_slide_minimal(text: str, out_path: Path, w: int, h: int) -> None:
    """
    Render a quote on a warm cream canvas — no external image needed.

    Palette:   #F4EDE0 cream base, #3B2F2F dark warm-brown text.
    Gradient:  very subtle darkening toward the bottom (depth without drama).
    Grain:     light paper noise via numpy when available; skipped gracefully otherwise.
    Font:      LinLibertine — same as all other editor outputs for brand consistency.
    """
    from PIL import Image, ImageDraw, ImageFont

    # Use the fixed beige PNG as the background, scaled to fill the frame
    bg  = Image.open(BEIGE_BG_PATH).convert("RGB")
    bg_ratio = bg.width / bg.height
    target_ratio = w / h
    if bg_ratio > target_ratio:
        new_w = int(bg.height * target_ratio)
        left  = (bg.width - new_w) // 2
        bg    = bg.crop((left, 0, left + new_w, bg.height))
    else:
        new_h = int(bg.width / target_ratio)
        top   = (bg.height - new_h) // 2
        bg    = bg.crop((0, top, bg.width, top + new_h))
    canvas = bg.resize((w, h), Image.LANCZOS)

    draw      = ImageDraw.Draw(canvas)
    max_w     = int(w * 0.74)   # slightly tighter margins than photo slides — editorial feel
    font      = None
    lines:    list[str] = []
    chosen_sz = 48

    for size in range(68, 28, -4):
        try:
            font = ImageFont.truetype(_RENDER_FONT, size)
        except Exception:
            font = ImageFont.load_default()
            size = 32

        words      = text.split()
        cur_lines: list[str] = []
        current    = ""
        for word in words:
            test = f"{current} {word}".strip()
            try:
                tw = draw.textlength(test, font=font)
            except Exception:
                tw = len(test) * size * 0.6
            if tw <= max_w:
                current = test
            else:
                if current:
                    cur_lines.append(current)
                current = word
        if current:
            cur_lines.append(current)

        line_h  = int(size * 1.55)
        total_h = len(cur_lines) * line_h
        chosen_sz = size
        lines     = cur_lines
        if total_h <= int(h * 0.58):
            break

    line_h  = int(chosen_sz * 1.55)
    total_h = len(lines) * line_h
    y_start = (h - total_h) // 2 - int(h * 0.01)   # fractionally above centre

    text_color   = (59, 47, 47)    # #3B2F2F — warm dark brown
    shadow_color = (195, 178, 158) # muted warm shadow — not harsh on cream

    for line in lines:
        try:
            text_w = draw.textlength(line, font=font)
        except Exception:
            text_w = len(line) * chosen_sz * 0.55
        x = int((w - text_w) / 2)
        draw.text((x + 1, y_start + 1), line, fill=shadow_color, font=font)
        draw.text((x,     y_start),     line, fill=text_color,   font=font)
        y_start += line_h

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out_path), "JPEG", quality=97)


def render_closing_slide(handle: str, out_path: Path, w: int, h: int) -> None:
    """
    Warm near-black closing slide — drives saves rather than follows.

    Main text : "Save this for when you need it."  (bold italic, cream, centred)
    Handle    : bottom-right corner at 50% opacity (blended with background)
    Background: #1E1916 — warm dark, not cold black
    """
    from PIL import Image, ImageDraw, ImageFont

    BG      = (30, 25, 22)    # #1E1916
    CREAM   = (250, 246, 238) # #FAF6EE
    CTA     = "Save this for when you need it."
    MARGIN  = int(w * 0.08)

    canvas = Image.new("RGB", (w, h), BG)
    draw   = ImageDraw.Draw(canvas)

    # ── Main CTA text (bold italic, scaled to fit 76% width) ──
    max_w = int(w * 0.76)
    font  = None
    lines: list[str] = []
    chosen_sz = 52

    for size in range(88, 32, -4):
        try:
            font = ImageFont.truetype(_RENDER_FONT_BI, size)
        except Exception:
            font = ImageFont.truetype(_RENDER_FONT, size)
            size = 52

        words      = CTA.split()
        cur_lines: list[str] = []
        current    = ""
        for word in words:
            test = f"{current} {word}".strip()
            try:
                tw = draw.textlength(test, font=font)
            except Exception:
                tw = len(test) * size * 0.6
            if tw <= max_w:
                current = test
            else:
                if current:
                    cur_lines.append(current)
                current = word
        if current:
            cur_lines.append(current)

        line_h  = int(size * 1.50)
        total_h = len(cur_lines) * line_h
        chosen_sz = size
        lines     = cur_lines
        if total_h <= int(h * 0.38):
            break

    line_h  = int(chosen_sz * 1.50)
    total_h = len(lines) * line_h
    # Centre block, shifted 6% above visual centre — feels balanced on a tall canvas
    y_start = (h - total_h) // 2 - int(h * 0.06)

    for line in lines:
        try:
            text_w = draw.textlength(line, font=font)
        except Exception:
            text_w = len(line) * chosen_sz * 0.55
        x = int((w - text_w) / 2)
        draw.text((x, y_start), line, fill=CREAM, font=font)
        y_start += line_h

    # ── Handle — bottom-right, 50% opacity via mid-blend with background ──
    if handle:
        handle_color = tuple((BG[c] + CREAM[c]) // 2 for c in range(3))  # type: ignore[arg-type]
        h_size = max(28, int(chosen_sz * 0.42))
        try:
            h_font = ImageFont.truetype(_RENDER_FONT, h_size)
        except Exception:
            h_font = ImageFont.load_default()
        try:
            h_w = draw.textlength(handle, font=h_font)
        except Exception:
            h_w = len(handle) * h_size * 0.55
        h_x = w - MARGIN - int(h_w)
        h_y = h - MARGIN - h_size
        draw.text((h_x, h_y), handle, fill=handle_color, font=h_font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out_path), "JPEG", quality=95)
